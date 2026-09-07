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
    def __init__(self, anti_bot: AntiBotService, dlp: DLPService):
        self.anti_bot = anti_bot
        self.dlp = dlp

    def evaluate(self, content: str, address: str | None,
                 username: str | None) -> SecurityDecision:
        """Backend supplies validated content and trusted connection metadata."""
        reputation = self.anti_bot.evaluate(address)
        log_anti_bot_decision(reputation, username, address)
        if reputation.decision is Decision.BLOCK:
            return reputation
        dlp = self.dlp.evaluate(content)
        log_dlp_decision(dlp, username)
        # The final allow verdict retains incomplete reputation evidence instead
        # of presenting a clean DLP result as a fully screened security outcome.
        if dlp.decision is Decision.ALLOW and reputation.verdict in (
            "reputation_not_configured", "address_unavailable"
        ):
            return SecurityDecision(Decision.ALLOW, verdict=reputation.verdict)
        return dlp
