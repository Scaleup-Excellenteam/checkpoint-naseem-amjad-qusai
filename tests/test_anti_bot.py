import logging

import pytest

from server.anti_bot import AntiBotService, ReputationResult, UnconfiguredReputationProvider
from server.dlp import DLPService
from server import reason_codes as reasons
from server.security_decision import Decision
from server.security_pipeline import SecurityPipeline


class FakeReputationProvider:
    """Synthetic evidence supplied by tests, never a production address list."""

    def __init__(self, malicious=False):
        self.malicious = malicious
        self.seen = []

    def check(self, address):
        self.seen.append(address)
        return ReputationResult(
            "malicious_address" if self.malicious else "clean_address", self.malicious
        )


@pytest.mark.parametrize("malicious", [False, True])
def test_provider_evidence_maps_to_security_decision(malicious):
    provider = FakeReputationProvider(malicious)
    evidence = provider.check("synthetic-peer")
    assert evidence.malicious is malicious
    assert evidence.verdict == ("malicious_address" if malicious else "clean_address")
    provider.seen.clear()
    result = AntiBotService(provider).evaluate("synthetic-peer")
    assert provider.seen == ["synthetic-peer"]
    assert result.decision is (Decision.BLOCK if malicious else Decision.ALLOW)
    assert result.reason == (reasons.MALICIOUS_ADDRESS if malicious else None)
    assert result.verdict == evidence.verdict


def test_unconfigured_provider_is_explicitly_neutral():
    assert UnconfiguredReputationProvider().check("synthetic-peer") == ReputationResult(
        "reputation_not_configured", False
    )
    result = AntiBotService().evaluate("synthetic-peer")
    assert result.decision is Decision.ALLOW
    assert result.verdict == "reputation_not_configured"


def test_missing_address_does_not_query_provider():
    provider = FakeReputationProvider()
    result = AntiBotService(provider).evaluate(None)
    assert provider.seen == []
    assert result.decision is Decision.ALLOW
    assert result.verdict == "address_unavailable"


def test_reputation_block_stops_before_dlp(caplog):
    def forbidden_detector(content):
        pytest.fail("DLP ran after malicious reputation")

    pipeline = SecurityPipeline(AntiBotService(FakeReputationProvider(True)),
                                DLPService([forbidden_detector]))
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        result = pipeline.evaluate("SENSITIVE_TEST_CONTENT", "synthetic-peer", "alice")
    assert result.decision is Decision.BLOCK
    assert result.reason == reasons.MALICIOUS_ADDRESS == "MALICIOUS_ADDRESS"
    record, = caplog.records
    assert record.security_event == "anti_bot"
    assert record.decision == "BLOCK"
    assert record.address == "synthetic-peer"
    assert record.username == "alice"
    assert record.levelno == logging.WARNING
    assert "SENSITIVE_TEST_CONTENT" not in caplog.text
    assert "SENSITIVE_TEST_CONTENT" not in str(record.__dict__)


@pytest.mark.parametrize("dlp_blocks", [False, True])
def test_clean_reputation_continues_to_dlp(dlp_blocks, caplog):
    seen = []

    def detector(content):
        seen.append(content)
        return dlp_blocks

    provider = FakeReputationProvider()
    pipeline = SecurityPipeline(AntiBotService(provider), DLPService([detector]))
    with caplog.at_level(logging.INFO, logger="tspo.security"):
        result = pipeline.evaluate("  TEST_CONTENT  ", "synthetic-peer", "alice")
    assert provider.seen == ["synthetic-peer"]
    assert seen == ["  TEST_CONTENT  "]
    assert result.decision is (Decision.BLOCK if dlp_blocks else Decision.ALLOW)
    assert result.reason == (reasons.DLP_SENSITIVE_CONTENT if dlp_blocks else None)
    assert [record.security_event for record in caplog.records] == ["anti_bot", "dlp"]
    assert caplog.records[0].decision == "ALLOW"
    assert caplog.records[0].verdict == "clean_address"
    assert caplog.records[0].levelno == logging.INFO
    assert "TEST_CONTENT" not in caplog.text


@pytest.mark.parametrize("address,verdict", [
    (None, "address_unavailable"), ("synthetic-peer", "reputation_not_configured")
])
def test_final_allow_retains_incomplete_reputation_status(address, verdict):
    pipeline = SecurityPipeline(AntiBotService(), DLPService([lambda content: False]))
    result = pipeline.evaluate("hello", address, "alice")
    assert result.decision is Decision.ALLOW
    assert result.verdict == verdict
