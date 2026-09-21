from services.backend.core.tables import TenantsTable, create_all_tables
from services.backend.ml import registry
from services.backend.ml.feature_engineering import record_batch
from services.backend.ml.training import MIN_TRAINING_SAMPLES
from services.backend.retrain_handler import retrain_all_tenants


class _Log:
    def __init__(self, remote_addr):
        self.remote_addr = remote_addr
        self.status = "200"
        self.body_bytes_sent = "512"
        self.request_time = "0.05"
        self.request_uri = "/a"
        self.request_method = "GET"
        self.http_user_agent = "ua-1"


def _seed_enough_telemetry(dynamo_resource, tenant_id: str, ip: str, bucket_seconds: int = 5) -> None:
    # MIN_TRAINING_SAMPLES counts distinct telemetry BUCKETS (one training
    # vector per bucket item), not raw request count — putting many
    # requests into a single bucket only ever yields ONE vector. Spread
    # across enough distinct buckets (5s apart) to clear the threshold.
    for i in range(MIN_TRAINING_SAMPLES + 5):
        now = 1000.0 + i * bucket_seconds
        record_batch(dynamo_resource, tenant_id, [_Log(ip)] * 3, bucket_seconds=bucket_seconds, now=now)


def test_retrain_all_tenants_trains_each_tenant_separately(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="A", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="B", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    _seed_enough_telemetry(dynamo_resource, "t-1", "1.1.1.1")
    _seed_enough_telemetry(dynamo_resource, "t-2", "2.2.2.2")

    results = retrain_all_tenants(dynamo_resource)

    assert results["t-1"] is not None
    assert results["t-2"] is not None
    assert registry.model_exists(dynamo_resource, "t-1", stage="production")
    assert registry.model_exists(dynamo_resource, "t-2", stage="production")


def test_retrain_skips_tenant_with_insufficient_data(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="A", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    record_batch(dynamo_resource, "t-1", [_Log("1.1.1.1")] * 5, bucket_seconds=5, now=1000.0)

    results = retrain_all_tenants(dynamo_resource)

    assert results["t-1"] is None
    assert not registry.model_exists(dynamo_resource, "t-1", stage="production")


def test_retrain_no_tenants_returns_empty(dynamo_resource):
    create_all_tables(dynamo_resource)
    assert retrain_all_tenants(dynamo_resource) == {}


def test_lambda_handler_return_value_survives_json_serialisation(dynamo_resource, monkeypatch):
    """The Lambda Python runtime JSON-serialises whatever a handler returns.
    retrain_all_tenants returns ModelMetadata dataclasses, which json cannot
    encode, so the real function failed at the very last step on every run:

        Runtime.MarshalError: Unable to marshal response:
        Object of type ModelMetadata is not JSON serializable

    Found by invoking the deployed retrain Lambda on 2026-09-21. Every earlier
    test called retrain_all_tenants() directly and read the returned dict,
    which is exactly the step that never goes through json - so the nightly
    retrain would have errored every night, fired the retrain-failed alarm
    every night, and left nobody able to tell from the invocation result
    whether a model had actually been promoted.
    """
    import json

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="A", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    _seed_enough_telemetry(dynamo_resource, "t-1", "1.1.1.1")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    result = retrain_handler.handler({"source": "aws.events"}, None)

    encoded = json.dumps(result)  # the step the Lambda runtime performs
    decoded = json.loads(encoded)
    assert decoded["tenants"]["t-1"]["stage"] == "production"
    assert decoded["tenants"]["t-1"]["version"]


def test_lambda_handler_reports_skipped_tenants_explicitly(dynamo_resource, monkeypatch):
    """A tenant without enough data is a normal outcome, not an error, and it
    must be distinguishable from one that trained - null, not a missing key."""
    import json

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-new", name="N", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    decoded = json.loads(json.dumps(retrain_handler.handler({}, None)))
    assert decoded["tenants"] == {"t-new": None}
