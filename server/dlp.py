"""Composable content-only DLP interface; production rules await the Day-2 spec.

Detectors are trusted server configuration: each receives validated content and
returns True for a sensitive-content match. They must not perform delivery or
include content in logs. No production detection rules have been specified.
"""

from collections.abc import Callable, Iterable

if __package__:
    from . import reason_codes as reasons
    from .security_decision import Decision, SecurityDecision
else:
    import reason_codes as reasons
    from security_decision import Decision, SecurityDecision


Detector = Callable[[str], bool]


class DLPService:
    def __init__(self, detectors: Iterable[Detector] = ()):
        self._detectors = tuple(detectors)

    def evaluate(self, content: str) -> SecurityDecision:
        """Evaluate already validated content without modifying it or socket state."""
        if not self._detectors:
            # Explicitly incomplete: ALLOW here does not certify content as clean.
            return SecurityDecision(Decision.ALLOW, verdict="rules_not_configured")
        for detector in self._detectors:
            if detector(content):
                return SecurityDecision(
                    Decision.BLOCK, reasons.DLP_SENSITIVE_CONTENT,
                    "sensitive_content_detected",
                )
        return SecurityDecision(Decision.ALLOW, verdict="clean")
