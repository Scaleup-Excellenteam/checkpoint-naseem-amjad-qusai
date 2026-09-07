"""VirusTotal API v3 reputation provider for public IP addresses and URLs."""

from __future__ import annotations

import base64
import ipaddress
import json
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

if __package__:
    from .anti_bot import ReputationResult
else:
    from anti_bot import ReputationResult


class VirusTotalReputationProvider:
    IP_API_URL = "https://www.virustotal.com/api/v3/ip_addresses/{address}"
    URL_API_URL = "https://www.virustotal.com/api/v3/urls/{url_id}"

    def __init__(
        self,
        api_key: str,
        *,
        malicious_threshold: int = 1,
        timeout_seconds: float = 5.0,
        cache_seconds: float = 900.0,
        opener=urlopen,
        clock=time.monotonic,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("VirusTotal API key is required")
        if isinstance(malicious_threshold, bool) or malicious_threshold < 1:
            raise ValueError("malicious threshold must be at least 1")
        if timeout_seconds <= 0 or cache_seconds < 0:
            raise ValueError("timeout must be positive and cache duration non-negative")
        self._api_key = api_key.strip()
        self.malicious_threshold = malicious_threshold
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._opener = opener
        self._clock = clock
        self._cache: dict[str, tuple[float, ReputationResult]] = {}
        self._cache_lock = Lock()

    def check(self, address: str) -> ReputationResult:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError:
            return ReputationResult("virustotal_invalid_address", False)

        # VirusTotal reports are for public Internet addresses. Sending a LAN,
        # loopback, link-local, multicast, or reserved address wastes quota and
        # cannot provide evidence about the client behind that address.
        if not parsed_address.is_global:
            return ReputationResult("virustotal_non_public_address", False)

        normalized = str(parsed_address)
        # The provider is shared by all WebSocket handlers. Serialize cache
        # misses so simultaneous messages from the same peer consume one API
        # request rather than one request per message.
        endpoint = self.IP_API_URL.format(address=quote(normalized, safe=""))
        return self._cached(
            f"ip:{normalized}",
            lambda: self._request_report(endpoint, "virustotal"),
        )

    def check_url(self, url: str) -> ReputationResult:
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
        except (TypeError, ValueError):
            return ReputationResult("virustotal_url_invalid", False)
        if (
            parts.scheme.lower() not in ("http", "https")
            or not hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return ReputationResult("virustotal_url_invalid", False)
        if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
            return ReputationResult("virustotal_url_non_public", False)
        try:
            host_ip = ipaddress.ip_address(hostname)
        except ValueError:
            host_ip = None
        if host_ip is not None and not host_ip.is_global:
            return ReputationResult("virustotal_url_non_public", False)

        normalized = urlunsplit((
            parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""
        ))
        url_id = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii")
        url_id = url_id.rstrip("=")
        endpoint = self.URL_API_URL.format(url_id=url_id)
        return self._cached(
            f"url:{normalized}",
            lambda: self._request_report(
                endpoint,
                "virustotal_url",
                not_found_verdict="virustotal_url_not_found",
            ),
        )

    def _cached(self, key: str, request_report) -> ReputationResult:
        with self._cache_lock:
            cached = self._cache.get(key)
            now = self._clock()
            if cached is not None and cached[0] > now:
                return cached[1]

            result = request_report()
            self._cache[key] = (now + self.cache_seconds, result)
            return result

    def _request_report(
        self,
        endpoint: str,
        verdict_prefix: str,
        *,
        not_found_verdict: str | None = None,
    ) -> ReputationResult:
        request = Request(
            endpoint,
            headers={"Accept": "application/json", "x-apikey": self._api_key},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
            stats = payload["data"]["attributes"]["last_analysis_stats"]
            malicious = self._count(stats, "malicious")
            suspicious = self._count(stats, "suspicious")
        except HTTPError as error:
            if error.code == 404 and not_found_verdict is not None:
                return ReputationResult(not_found_verdict, False)
            return ReputationResult(f"{verdict_prefix}_unavailable", False)
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            # Availability or quota failure is neutral evidence. It remains
            # visible in the security log and never crashes message handling.
            return ReputationResult(f"{verdict_prefix}_unavailable", False)

        if malicious >= self.malicious_threshold:
            return ReputationResult(
                f"{verdict_prefix}_malicious_{malicious}_suspicious_{suspicious}", True
            )
        if suspicious:
            return ReputationResult(f"{verdict_prefix}_suspicious_{suspicious}", False)
        return ReputationResult(f"{verdict_prefix}_no_malicious_detections", False)

    @staticmethod
    def _count(stats: dict, key: str) -> int:
        value = stats[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid VirusTotal {key} count")
        return value
