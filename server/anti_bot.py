"""Address and URL reputation interfaces used by the security pipeline.

Providers never own sockets, authentication, room membership, or delivery.
"""

from dataclasses import dataclass
import re
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

if __package__:
    from . import reason_codes as reasons
    from .security_decision import Decision, SecurityDecision
else:
    import reason_codes as reasons
    from security_decision import Decision, SecurityDecision


@dataclass(frozen=True)
class ReputationResult:
    verdict: str
    malicious: bool


class ReputationProvider(Protocol):
    def check(self, address: str) -> ReputationResult:
        """Return reputation evidence for the trusted address; no message input."""
        ...


class URLReputationProvider(Protocol):
    def check_url(self, url: str) -> ReputationResult:
        """Return reputation evidence for one normalized HTTP(S) URL."""
        ...


class UnconfiguredReputationProvider:
    def check(self, address: str) -> ReputationResult:
        # Neutral absence of evidence, not a clean-address certification.
        return ReputationResult("reputation_not_configured", malicious=False)


class UnconfiguredURLReputationProvider:
    def check_url(self, url: str) -> ReputationResult:
        return ReputationResult("url_reputation_not_configured", malicious=False)


_HTTP_URL = re.compile(r"https?://[^\s<>\[\]{}\"']+", re.IGNORECASE)
_TRAILING_SENTENCE_PUNCTUATION = ".,;:!?)]}"


def extract_http_urls(content: str) -> tuple[str, ...]:
    """Extract unique, normalized HTTP(S) URLs without logging message text."""
    urls = []
    seen = set()
    for match in _HTTP_URL.finditer(content):
        candidate = match.group(0).rstrip(_TRAILING_SENTENCE_PUNCTUATION)
        try:
            parts = urlsplit(candidate)
            hostname = parts.hostname
        except ValueError:
            continue
        if not hostname or parts.scheme.lower() not in ("http", "https"):
            continue
        # Fragments are browser-local and do not identify the HTTP resource.
        normalized = urlunsplit((
            parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""
        ))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return tuple(urls)


class AntiBotService:
    def __init__(
        self,
        provider: ReputationProvider | None = None,
        url_provider: URLReputationProvider | None = None,
    ):
        self.provider = provider if provider is not None else UnconfiguredReputationProvider()
        self.url_provider = (
            url_provider if url_provider is not None
            else UnconfiguredURLReputationProvider()
        )

    def evaluate(self, address: str | None) -> SecurityDecision:
        if not address:
            return SecurityDecision(Decision.ALLOW, verdict="address_unavailable")
        result = self.provider.check(address)
        if result.malicious:
            return SecurityDecision(
                Decision.BLOCK,
                reasons.MALICIOUS_ADDRESS,
                result.verdict,
            )
        # Only configured providers may claim a clean verdict. Provider verdicts
        # must be safe metadata, never content/credentials or raw response dumps.
        return SecurityDecision(Decision.ALLOW, verdict=result.verdict)

    def evaluate_urls(self, urls: tuple[str, ...]) -> SecurityDecision:
        incomplete_verdict = None
        for url in urls:
            result = self.url_provider.check_url(url)
            if result.malicious:
                return SecurityDecision(
                    Decision.BLOCK,
                    reasons.MALICIOUS_ADDRESS,
                    result.verdict,
                )
            if result.verdict in (
                "url_reputation_not_configured",
                "virustotal_url_unavailable",
                "virustotal_url_not_found",
                "virustotal_url_invalid",
                "virustotal_url_non_public",
            ):
                incomplete_verdict = incomplete_verdict or result.verdict
        return SecurityDecision(
            Decision.ALLOW,
            verdict=incomplete_verdict or f"urls_checked_{len(urls)}",
        )
