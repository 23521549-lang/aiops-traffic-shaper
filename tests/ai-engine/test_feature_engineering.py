import math
import pytest

from services.ai_engine.ml.feature_engineering import (
    FeatureVector,
    _compute_features,
    _shannon_entropy,
)


def make_record(
    remote_addr: str = "1.2.3.4",
    status: str = "200",
    body_bytes_sent: str = "1024",
    request_time: str = "0.05",
    request_uri: str = "/api/test",
    http_user_agent: str = "Mozilla/5.0",
    request_method: str = "GET",
) -> dict:
    return {
        "remote_addr":     remote_addr,
        "status":          status,
        "body_bytes_sent": body_bytes_sent,
        "request_time":    request_time,
        "request_uri":     request_uri,
        "http_user_agent": http_user_agent,
        "request_method":  request_method,
        "time_iso8601":    "2026-01-01T00:00:00+07:00",
    }


class TestShannonEntropy:
    def test_empty_list_returns_zero(self):
        assert _shannon_entropy([]) == 0.0

    def test_single_value_returns_zero(self):
        assert _shannon_entropy(["Mozilla"]) == 0.0

    def test_identical_values_return_zero(self):
        assert _shannon_entropy(["Mozilla", "Mozilla", "Mozilla"]) == 0.0

    def test_uniform_distribution_max_entropy(self):
        values = ["A", "B", "C", "D"]
        entropy = _shannon_entropy(values)
        expected = math.log2(4)
        assert abs(entropy - expected) < 0.001

    def test_two_values_equal_split(self):
        values = ["A", "B"]
        entropy = _shannon_entropy(values)
        assert abs(entropy - 1.0) < 0.001


class TestComputeFeatures:
    def test_returns_none_below_threshold(self):
        records = [make_record()] * 2
        result = _compute_features(records, window_seconds=5)
        assert result is None

    def test_returns_feature_vector_above_threshold(self):
        records = [make_record()] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert isinstance(result, FeatureVector)

    def test_request_rate_calculation(self):
        records = [make_record()] * 10
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.request_rate == pytest.approx(2.0, rel=1e-3)

    def test_error_ratio_all_success(self):
        records = [make_record(status="200")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.error_ratio == pytest.approx(0.0, abs=1e-6)

    def test_error_ratio_all_errors(self):
        records = [make_record(status="404")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.error_ratio == pytest.approx(1.0, rel=1e-3)

    def test_error_ratio_mixed(self):
        records = (
            [make_record(status="200")] * 3 +
            [make_record(status="500")] * 2
        )
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.error_ratio == pytest.approx(0.4, rel=1e-3)

    def test_avg_bytes_sent(self):
        records = [make_record(body_bytes_sent="1000")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.avg_bytes_sent == pytest.approx(1000.0, rel=1e-3)

    def test_avg_request_time(self):
        records = [make_record(request_time="0.1")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.avg_request_time == pytest.approx(0.1, rel=1e-3)

    def test_unique_uri_ratio_all_same(self):
        records = [make_record(request_uri="/api/test")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.unique_uri_ratio == pytest.approx(0.2, rel=1e-3)

    def test_unique_uri_ratio_all_different(self):
        records = [make_record(request_uri=f"/api/{i}") for i in range(5)]
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.unique_uri_ratio == pytest.approx(1.0, rel=1e-3)

    def test_post_ratio_all_get(self):
        records = [make_record(request_method="GET")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.post_ratio == pytest.approx(0.0, abs=1e-6)

    def test_post_ratio_all_post(self):
        records = [make_record(request_method="POST")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.post_ratio == pytest.approx(1.0, rel=1e-3)

    def test_to_list_returns_7_features(self):
        records = [make_record()] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        feature_list = result.to_list()
        assert len(feature_list) == 7
        assert all(isinstance(f, float) for f in feature_list)

    def test_user_agent_entropy_single_ua(self):
        records = [make_record(http_user_agent="bot/1.0")] * 5
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.user_agent_entropy == pytest.approx(0.0, abs=1e-6)

    def test_user_agent_entropy_rotating_ua(self):
        records = [
            make_record(http_user_agent=f"agent-{i}") for i in range(5)
        ]
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.user_agent_entropy > 2.0

    def test_ddos_pattern_high_rate(self):
        records = [make_record()] * 100
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.request_rate == pytest.approx(20.0, rel=1e-3)

    def test_scanner_pattern_many_uris(self):
        records = [make_record(request_uri=f"/scan/{i}") for i in range(10)]
        result = _compute_features(records, window_seconds=5)
        assert result is not None
        assert result.unique_uri_ratio == pytest.approx(1.0, rel=1e-3)
