import io
from urllib.error import HTTPError

from server.anti_bot import AntiBotService
from server.dlp import DLPService
from server.security_decision import Decision
from server.security_pipeline import SecurityPipeline
from server.virustotal import VirusTotalReputationProvider


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def report(malicious=0, suspicious=0):
    return (
        '{"data":{"attributes":{"last_analysis_stats":'
        f'{{"malicious":{malicious},"suspicious":{suspicious}}}'
        '}}}'
    ).encode()


def test_malicious_report_blocks_and_sends_key_in_header():
    requests = []

    def open_report(request, timeout):
        requests.append((request, timeout))
        return JsonResponse(report(malicious=3, suspicious=1))

    provider = VirusTotalReputationProvider("secret-key", opener=open_report)
    decision = AntiBotService(provider).evaluate("8.8.8.8")

    assert decision.decision is Decision.BLOCK
    assert decision.reason == "MALICIOUS_ADDRESS"
    assert decision.verdict == "virustotal_malicious_3_suspicious_1"
    request, timeout = requests[0]
    assert request.full_url.endswith("/ip_addresses/8.8.8.8")
    assert request.get_header("X-apikey") == "secret-key"
    assert timeout == 5.0


def test_threshold_and_suspicious_only_report_remain_allowed():
    provider = VirusTotalReputationProvider(
        "key",
        malicious_threshold=2,
        opener=lambda request, timeout: JsonResponse(report(malicious=1, suspicious=4)),
    )

    result = provider.check("1.1.1.1")

    assert result.malicious is False
    assert result.verdict == "virustotal_suspicious_4"


def test_private_and_invalid_addresses_do_not_consume_api_quota():
    def forbidden_request(request, timeout):
        raise AssertionError("a non-public address reached VirusTotal")

    provider = VirusTotalReputationProvider("key", opener=forbidden_request)

    assert provider.check("127.0.0.1").verdict == "virustotal_non_public_address"
    assert provider.check("172.20.10.3").verdict == "virustotal_non_public_address"
    assert provider.check("not-an-ip").verdict == "virustotal_invalid_address"


def test_report_is_cached_per_address():
    calls = []

    def open_report(request, timeout):
        calls.append(request.full_url)
        return JsonResponse(report())

    provider = VirusTotalReputationProvider("key", opener=open_report)

    first = provider.check("8.8.8.8")
    second = provider.check("8.8.8.8")

    assert first == second
    assert calls == ["https://www.virustotal.com/api/v3/ip_addresses/8.8.8.8"]


def test_api_error_is_neutral_and_does_not_expose_the_key():
    def fail(request, timeout):
        raise HTTPError(request.full_url, 429, "quota", {}, None)

    provider = VirusTotalReputationProvider("do-not-leak", opener=fail)

    result = provider.check("8.8.8.8")

    assert result.malicious is False
    assert result.verdict == "virustotal_unavailable"
    assert "do-not-leak" not in repr(result)


def test_pipeline_retains_incomplete_virustotal_evidence():
    provider = VirusTotalReputationProvider("key")
    pipeline = SecurityPipeline(
        AntiBotService(provider),
        DLPService([lambda content: False]),
    )

    result = pipeline.evaluate("hello", "127.0.0.1", "alice")

    assert result.decision is Decision.ALLOW
    assert result.verdict == "virustotal_non_public_address"
