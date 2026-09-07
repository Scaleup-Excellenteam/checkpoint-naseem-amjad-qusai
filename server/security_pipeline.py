"""Compose security checks and metadata logging; never route or deliver."""

if __package__:
    from .anti_bot import AntiBotService
    from .dlp import DLPService
    from .security_decision import Decision, SecurityDecision
    from .security_logging import log_anti_bot_decision, log_dlp_decision
else:
    from anti_bot import AntiBotService
    from dlp import DLPService
    from security_decision import Decision, SecurityDecision
    from security_logging import log_anti_bot_decision, log_dlp_decision


class SecurityPipeline:
    def __init__(self, anti_bot: AntiBotService, dlp: DLPService,
                 on_decision=None):
        self.anti_bot = anti_bot
        self.dlp = dlp
        # Optional per-stage observer, called with the same safe metadata the
        # logger receives. It exists so a caller can persist decisions without
        # depending on any log level; it never receives message content, and
        # it never influences the security outcome.
        self.on_decision = on_decision

    def _observe(self, event: str, result: SecurityDecision,
                 username: str | None, address: str | None = None) -> None:
        if self.on_decision is not None:
            self.on_decision(event, result, username, address)

    def evaluate(self, content: str, address: str | None,
                 username: str | None) -> SecurityDecision:
        """Backend supplies validated content and trusted connection metadata."""
        reputation = self.anti_bot.evaluate(address)
        log_anti_bot_decision(reputation, username, address)
        self._observe("anti_bot", reputation, username, address)
        if reputation.decision is Decision.BLOCK:
            return reputation
        dlp = self.dlp.evaluate(content)
        log_dlp_decision(dlp, username)
        self._observe("dlp", dlp, username)
        # The final allow verdict retains incomplete reputation evidence instead
        # of presenting a clean DLP result as a fully screened security outcome.
        if dlp.decision is Decision.ALLOW and reputation.verdict in (
            "reputation_not_configured",
            "address_unavailable",
            "virustotal_unavailable",
            "virustotal_non_public_address",
            "virustotal_invalid_address",
        ):
            return SecurityDecision(Decision.ALLOW, verdict=reputation.verdict)
        return dlp
