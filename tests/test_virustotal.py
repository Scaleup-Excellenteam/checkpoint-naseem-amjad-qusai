import base64
import io
from urllib.error import HTTPError

from server.anti_bot import AntiBotService, extract_http_urls
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


def test_extracts_unique_http_urls_and_removes_fragments_and_punctuation():
    content = (
        "See https://example.com/a?q=1#section, then http://test.example/path! "
        "Duplicate: https://example.com/a?q=1#other"
    )

    assert extract_http_urls(content) == (
        "https://example.com/a?q=1",
        "http://test.example/path",
    )


def test_malicious_url_report_blocks_with_existing_contract_reason():
    requests = []

    def open_report(request, timeout):
        requests.append(request)
        return JsonResponse(report(malicious=4, suspicious=2))

    provider = VirusTotalReputationProvider("url-key", opener=open_report)
    decision = AntiBotService(provider, provider).evaluate_urls(
        ("https://evil.example/path?q=1",)
    )

    expected_id = base64.urlsafe_b64encode(
        b"https://evil.example/path?q=1"
    ).decode().rstrip("=")
    assert decision.decision is Decision.BLOCK
    assert decision.reason == "MALICIOUS_ADDRESS"
    assert decision.verdict == "virustotal_url_malicious_4_suspicious_2"
    assert requests[0].full_url.endswith(f"/urls/{expected_id}")
    assert requests[0].get_header("X-apikey") == "url-key"


def test_url_reports_are_cached_and_non_public_urls_skip_the_api():
    calls = []

    def open_report(request, timeout):
        calls.append(request.full_url)
        return JsonResponse(report())

    provider = VirusTotalReputationProvider("key", opener=open_report)
    first = provider.check_url("https://example.com/path#one")
    second = provider.check_url("https://example.com/path#two")

    assert first == second
    assert len(calls) == 1
    assert provider.check_url("http://127.0.0.1/admin").verdict == (
        "virustotal_url_non_public"
    )
    assert provider.check_url("http://localhost/admin").verdict == (
        "virustotal_url_non_public"
    )
    assert len(calls) == 1


def test_missing_url_report_is_neutral_evidence():
    def not_found(request, timeout):
        raise HTTPError(request.full_url, 404, "not found", {}, None)

    provider = VirusTotalReputationProvider("key", opener=not_found)

    result = provider.check_url("https://new.example/path")

    assert result == provider.check_url("https://new.example/path")
    assert result.malicious is False
    assert result.verdict == "virustotal_url_not_found"


def test_pipeline_checks_url_before_dlp_and_never_logs_message(caplog):
    marker = "https://malicious.example/SECRET_QUERY"

    def open_report(request, timeout):
        return JsonResponse(report(malicious=2))

    def forbidden_detector(content):
        raise AssertionError("DLP ran after a malicious URL verdict")

    provider = VirusTotalReputationProvider("key", opener=open_report)
    pipeline = SecurityPipeline(
        AntiBotService(provider, provider),
        DLPService([forbidden_detector]),
    )

    result = pipeline.evaluate(f"open {marker}", "127.0.0.1", "alice")

    assert result.decision is Decision.BLOCK
    assert result.reason == "MALICIOUS_ADDRESS"
    assert marker not in caplog.text
