"""Address reputation interface used by the server security pipeline.

Providers receive only a server-derived address and never own sockets or delivery.
"""

from dataclasses import dataclass
from typing import Protocol

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


class UnconfiguredReputationProvider:
    def check(self, address: str) -> ReputationResult:
        # Neutral absence of evidence, not a clean-address certification.
        return ReputationResult("reputation_not_configured", malicious=False)


class AntiBotService:
    def __init__(self, provider: ReputationProvider | None = None):
        self.provider = provider if provider is not None else UnconfiguredReputationProvider()

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
