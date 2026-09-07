"""Compose security checks and metadata logging; never route or deliver."""

if __package__:
    from .anti_bot import AntiBotService, extract_http_urls
    from .dlp import DLPService
    from .security_decision import Decision, SecurityDecision
    from .security_logging import (
        log_anti_bot_decision,
        log_dlp_decision,
        log_url_reputation_decision,
    )
else:
    from anti_bot import AntiBotService, extract_http_urls
    from dlp import DLPService
    from security_decision import Decision, SecurityDecision
    from security_logging import (
        log_anti_bot_decision,
        log_dlp_decision,
        log_url_reputation_decision,
    )


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

        urls = extract_http_urls(content)
        url_reputation = None
        if urls:
            url_reputation = self.anti_bot.evaluate_urls(urls)
            log_url_reputation_decision(url_reputation, username, len(urls))
            if url_reputation.decision is Decision.BLOCK:
                return url_reputation

        dlp = self.dlp.evaluate(content)
        log_dlp_decision(dlp, username)
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
        if dlp.decision is Decision.ALLOW and url_reputation is not None and (
            url_reputation.verdict in (
                "url_reputation_not_configured",
                "virustotal_url_unavailable",
                "virustotal_url_not_found",
                "virustotal_url_invalid",
                "virustotal_url_non_public",
            )
        ):
            return SecurityDecision(Decision.ALLOW, verdict=url_reputation.verdict)
        return dlp
