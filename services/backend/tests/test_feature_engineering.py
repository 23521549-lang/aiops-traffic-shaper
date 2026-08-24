from services.backend.core.tables import create_all_tables
from services.backend.ml.feature_engineering import (
    collect_training_vectors, compute_features_for_ip, record_batch,
)


class _Log:
    def __init__(self, remote_addr, status="200", body_bytes_sent="512",
                 request_time="0.05", request_uri="/a", request_method="GET",
                 http_user_agent="ua-1"):
        self.remote_addr = remote_addr
        self.status = status
        self.body_bytes_sent = body_bytes_sent
        self.request_time = request_time
        self.request_uri = request_uri
        self.request_method = request_method
        self.http_user_agent = http_user_agent


def test_record_batch_issues_one_write_per_unique_ip_not_per_log_line(dynamo_resource, monkeypatch):
    create_all_tables(dynamo_resource)
    from services.backend.core import tables as tables_mod
    call_count = {"n": 0}
    original = tables_mod.TelemetryEventsTable.add_aggregate

    def _counting_add_aggregate(self, *a, **kw):
        call_count["n"] += 1
        return original(self, *a, **kw)

    monkeypatch.setattr(tables_mod.TelemetryEventsTable, "add_aggregate", _counting_add_aggregate)

    logs = [_Log("1.2.3.4") for _ in range(5)] + [_Log("9.9.9.9")]
    touched = record_batch(dynamo_resource, "t-1", logs, bucket_seconds=5)
    assert touched == {"1.2.3.4", "9.9.9.9"}
    assert call_count["n"] == 2


def test_compute_features_below_threshold_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_batch(dynamo_resource, "t-1", [_Log("1.2.3.4")], bucket_seconds=5)
    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3)
    assert vector is None


def test_compute_features_above_threshold(dynamo_resource):
    create_all_tables(dynamo_resource)
    logs = [_Log("1.2.3.4", status="500")] * 2 + [_Log("1.2.3.4", status="200")]
    record_batch(dynamo_resource, "t-1", logs, bucket_seconds=5)
    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3)
    assert vector is not None
    assert vector.sample_size == 3
    assert round(vector.error_ratio, 3) == round(2 / 3, 3)


def test_split_burst_across_bucket_boundary_still_detected(dynamo_resource):
    create_all_tables(dynamo_resource)
    logs_prev = [_Log("1.2.3.4") for _ in range(20)]
    record_batch(dynamo_resource, "t-1", logs_prev, bucket_seconds=5, now=1004.9)
    logs_curr = [_Log("1.2.3.4") for _ in range(20)]
    record_batch(dynamo_resource, "t-1", logs_curr, bucket_seconds=5, now=1005.1)

    vector = compute_features_for_ip(dynamo_resource, "t-1", "1.2.3.4",
                                      bucket_seconds=5, min_requests_threshold=3,
                                      now=1005.1)
    assert vector is not None
    assert vector.sample_size > 30


def test_collect_training_vectors_across_ips_and_buckets(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_batch(dynamo_resource, "t-1", [_Log("1.2.3.4")] * 5, bucket_seconds=5, now=1000.0)
    record_batch(dynamo_resource, "t-1", [_Log("9.9.9.9")] * 4, bucket_seconds=5, now=2000.0)
    record_batch(dynamo_resource, "t-2", [_Log("5.5.5.5")] * 10, bucket_seconds=5, now=1000.0)

    vectors = collect_training_vectors(dynamo_resource, "t-1", bucket_seconds=5, min_requests_threshold=3)
    # Two buckets across two different IPs for t-1 — t-2's data must not leak in.
    assert len(vectors) == 2
    for v in vectors:
        assert len(v) == 7  # matches FEATURE_NAMES length


def test_collect_training_vectors_filters_below_threshold(dynamo_resource):
    create_all_tables(dynamo_resource)
    record_batch(dynamo_resource, "t-1", [_Log("1.2.3.4")], bucket_seconds=5, now=1000.0)  # only 1 request
    vectors = collect_training_vectors(dynamo_resource, "t-1", bucket_seconds=5, min_requests_threshold=3)
    assert vectors == []


def test_collect_training_vectors_empty_tenant(dynamo_resource):
    create_all_tables(dynamo_resource)
    assert collect_training_vectors(dynamo_resource, "no-such-tenant") == []
