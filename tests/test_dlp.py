import json
import logging
from dataclasses import FrozenInstanceError

import pytest

from server import reason_codes as reasons
from server.dlp import DLPService
from server.security_decision import Decision, SecurityDecision
from server.security_logging import log_dlp_decision


def test_allow_decision():
    result = SecurityDecision(Decision.ALLOW, verdict="clean")
    assert result.decision.value == "ALLOW"
    assert result.reason is None
    assert result.verdict == "clean"


def test_block_decision_uses_central_reason():
    result = SecurityDecision(Decision.BLOCK, reasons.DLP_SENSITIVE_CONTENT)
    assert result.decision.value == "BLOCK"
    assert result.reason == reasons.DLP_SENSITIVE_CONTENT == "DLP_SENSITIVE_CONTENT"


def test_decision_is_immutable():
    result = SecurityDecision(Decision.ALLOW)
    with pytest.raises(FrozenInstanceError):
        result.decision = Decision.BLOCK


@pytest.mark.parametrize("value", ["ALLOW", "invalid", None])
def test_decision_requires_enum(value):
    with pytest.raises(TypeError):
        SecurityDecision(value)


def test_default_rules_explicitly_incomplete():
    result = DLPService().evaluate("arbitrary content")
    assert result == SecurityDecision(Decision.ALLOW, verdict="rules_not_configured")


def test_configured_clean_message_allows():
    result = DLPService([lambda text: text == "TEST_ONLY_MARKER"]).evaluate("hello")
    assert result == SecurityDecision(Decision.ALLOW, verdict="clean")


def test_composed_detectors_block_and_short_circuit():
    calls = []

    def no_match(content):
        calls.append("first")
        return False

    def match(content):
        calls.append("second")
        return content == "TEST_ONLY_MARKER"

    def must_not_run(content):
        pytest.fail("Evaluation continued after BLOCK")

    result = DLPService([no_match, match, must_not_run]).evaluate("TEST_ONLY_MARKER")
    assert result == SecurityDecision(
        Decision.BLOCK, reasons.DLP_SENSITIVE_CONTENT, "sensitive_content_detected"
    )
    assert calls == ["first", "second"]


def test_content_only_interface_preserves_input_without_transport():
    seen = []

    def detector(content):
        seen.append(content)
        return False

    content = "  hello\nworld\t "
    result = DLPService([detector]).evaluate(content)
    assert result.decision is Decision.ALLOW
    assert seen == [content]
    assert seen[0] is content


def test_detector_configuration_is_snapshotted():
    detectors = [lambda content: True]
    service = DLPService(detectors)
    detectors.clear()
    assert service.evaluate("TEST_ONLY_MARKER").decision is Decision.BLOCK


@pytest.mark.parametrize("decision,reason,level", [
    (Decision.ALLOW, None, logging.INFO),
    (Decision.BLOCK, reasons.DLP_SENSITIVE_CONTENT, logging.WARNING),
])
def test_structured_logging(decision, reason, level, caplog):
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        log_dlp_decision(SecurityDecision(decision, reason, "test_verdict"), "alice")
    record, = caplog.records
    assert record.levelno == level
    fields = json.loads(record.getMessage())
    assert fields == {
        "security_event": "dlp", "action": "CHAT_MESSAGE",
        "decision": decision.value, "reason": reason,
        "verdict": "test_verdict", "username": "alice",
    }
    assert all(getattr(record, key) == value for key, value in fields.items())


def test_detector_content_is_not_logged_by_dlp(caplog, capsys):
    marker = "CONTROLLED_SENSITIVE_TEST_CONTENT"
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        result = DLPService([lambda content: content == marker]).evaluate(marker)
        log_dlp_decision(result, "alice")
    assert marker not in caplog.text
    assert marker not in str([record.__dict__ for record in caplog.records])
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
