"""VirusTotal API v3 reputation provider for public client IP addresses."""

from __future__ import annotations

import ipaddress
import json
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

if __package__:
    from .anti_bot import ReputationResult
else:
    from anti_bot import ReputationResult


class VirusTotalReputationProvider:
    API_URL = "https://www.virustotal.com/api/v3/ip_addresses/{address}"

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
        with self._cache_lock:
            cached = self._cache.get(normalized)
            now = self._clock()
            if cached is not None and cached[0] > now:
                return cached[1]

            result = self._request_report(normalized)
            self._cache[normalized] = (now + self.cache_seconds, result)
            return result

    def _request_report(self, address: str) -> ReputationResult:
        request = Request(
            self.API_URL.format(address=quote(address, safe="")),
            headers={"Accept": "application/json", "x-apikey": self._api_key},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
            stats = payload["data"]["attributes"]["last_analysis_stats"]
            malicious = self._count(stats, "malicious")
            suspicious = self._count(stats, "suspicious")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            # Availability or quota failure is neutral evidence. It remains
            # visible in the security log and never crashes message handling.
            return ReputationResult("virustotal_unavailable", False)

        if malicious >= self.malicious_threshold:
            return ReputationResult(
                f"virustotal_malicious_{malicious}_suspicious_{suspicious}", True
            )
        if suspicious:
            return ReputationResult(f"virustotal_suspicious_{suspicious}", False)
        return ReputationResult("virustotal_no_malicious_detections", False)

    @staticmethod
    def _count(stats: dict, key: str) -> int:
        value = stats[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid VirusTotal {key} count")
        return value
