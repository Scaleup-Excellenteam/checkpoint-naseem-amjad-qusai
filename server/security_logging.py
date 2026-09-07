"""Structured decision metadata only. Never accepts content or credentials."""

import json
import logging

if __package__:
    from .security_decision import Decision, SecurityDecision
else:
    from security_decision import Decision, SecurityDecision


logger = logging.getLogger("tspo.security")


def log_dlp_decision(result: SecurityDecision, username: str | None) -> None:
    _log_decision("dlp", result, username)


def log_anti_bot_decision(result: SecurityDecision, username: str | None,
                          address: str | None) -> None:
    _log_decision("anti_bot", result, username, address)


def _log_decision(event: str, result: SecurityDecision, username: str | None,
                  address: str | None = None) -> None:
    fields = {
        "security_event": event,
        "action": "CHAT_MESSAGE",
        "decision": result.decision.value,
        "verdict": result.verdict,
        "reason": result.reason,
        "username": username,
    }
    if event == "anti_bot":
        fields["address"] = address
    level = logging.WARNING if result.decision is Decision.BLOCK else logging.INFO
    logger.log(level, json.dumps(fields), extra=fields)
