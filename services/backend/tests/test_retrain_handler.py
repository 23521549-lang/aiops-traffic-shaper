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
    Returning ModelMetadata dataclasses made the deployed function fail with

        Runtime.MarshalError: Unable to marshal response:
        Object of type ModelMetadata is not JSON serializable

    on every run, after the training had finished. Found by invoking the
    deployed retrain Lambda on 2026-09-21; every earlier test read the Python
    return value and never went through json."""
    import json

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="A", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    _seed_enough_telemetry(dynamo_resource, "t-1", "1.1.1.1")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    decoded = json.loads(json.dumps(retrain_handler.handler({"tenant_id": "t-1"}, None)))
    assert decoded["tenant_id"] == "t-1"
    assert decoded["result"]["stage"] == "production"
    assert decoded["result"]["version"]


def test_worker_reports_a_tenant_with_too_little_data_as_null(dynamo_resource, monkeypatch):
    """Too little data is a normal outcome, not an error: null, and no raise."""
    import json

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-new", name="N", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    decoded = json.loads(json.dumps(retrain_handler.handler({"tenant_id": "t-new"}, None)))
    assert decoded == {"tenant_id": "t-new", "result": None}


def test_worker_retrains_only_its_own_tenant(dynamo_resource, monkeypatch):
    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    for t, ip in (("t-1", "1.1.1.1"), ("t-2", "2.2.2.2")):
        TenantsTable(dynamo_resource).put(tenant_id=t, name=t, status="active",
                                           created_at="2026-08-21T00:00:00Z")
        _seed_enough_telemetry(dynamo_resource, t, ip)
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    retrain_handler.handler({"tenant_id": "t-1"}, None)

    assert registry.model_exists(dynamo_resource, "t-1", stage="production")
    assert not registry.model_exists(dynamo_resource, "t-2", stage="production")


def test_scheduled_trigger_fans_out_one_async_invocation_per_tenant(dynamo_resource, monkeypatch):
    """The serial loop shared one 15-minute timeout across every tenant (~2s
    each, measured in production) and let one tenant's exception stop the
    rest. Fanned out, each tenant gets its own invocation - asynchronous, so
    the dispatcher returns at once however many tenants there are."""
    import json

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    for t in ("t-1", "t-2", "t-3"):
        TenantsTable(dynamo_resource).put(tenant_id=t, name=t, status="active",
                                           created_at="2026-08-21T00:00:00Z")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aiops-traffic-shaper-retrain")
    calls = []
    monkeypatch.setattr(retrain_handler, "_lambda_invoke",
                        lambda: lambda **kw: calls.append(kw))

    result = retrain_handler.handler({"source": "aws.events"}, None)

    assert sorted(result["dispatched"]) == ["t-1", "t-2", "t-3"]
    assert json.loads(json.dumps(result)) == result
    assert len(calls) == 3
    for c in calls:
        assert c["FunctionName"] == "aiops-traffic-shaper-retrain"
        assert c["InvocationType"] == "Event"  # async: never wait on a tenant
    assert sorted(json.loads(c["Payload"])["tenant_id"] for c in calls) == ["t-1", "t-2", "t-3"]


def test_a_worker_failure_propagates_so_lambda_retries_and_alarms(dynamo_resource, monkeypatch):
    """The opposite of the in-process loop, deliberately. A worker that
    swallowed its exception would report success: no Lambda error, no async
    retry, no retrain-failed alarm - a tenant silently stuck on a stale model."""
    import pytest

    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-bad", name="B", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    _seed_enough_telemetry(dynamo_resource, "t-bad", "1.1.1.1")
    monkeypatch.setattr(retrain_handler, "get_dynamo_resource", lambda: dynamo_resource)

    def boom(*a, **kw):
        raise ValueError("corrupt feature vector")
    monkeypatch.setattr(retrain_handler, "train_and_save", boom)

    with pytest.raises(ValueError):
        retrain_handler.handler({"tenant_id": "t-bad"}, None)


def test_one_tenant_failing_does_not_stop_the_others_retraining(dynamo_resource, monkeypatch):
    """The loop had no per-tenant isolation: an exception while training ONE
    tenant propagated out of retrain_all_tenants and every tenant after it in
    the scan went without a retrain that night - silently, because the only
    signal was a single Lambda error with no tenant attached."""
    from services.backend import retrain_handler

    create_all_tables(dynamo_resource)
    for t in ("t-bad", "t-good"):
        TenantsTable(dynamo_resource).put(tenant_id=t, name=t, status="active",
                                           created_at="2026-08-21T00:00:00Z")
    _seed_enough_telemetry(dynamo_resource, "t-bad", "1.1.1.1")
    _seed_enough_telemetry(dynamo_resource, "t-good", "2.2.2.2")

    real_train = retrain_handler.train_and_save

    def train_that_breaks_for_one(resource, tenant_id, vectors, stage):
        if tenant_id == "t-bad":
            raise ValueError("corrupt feature vector")
        return real_train(resource, tenant_id, vectors, stage=stage)

    monkeypatch.setattr(retrain_handler, "train_and_save", train_that_breaks_for_one)

    results = retrain_all_tenants(dynamo_resource)

    assert registry.model_exists(dynamo_resource, "t-good", stage="production")
    assert results["t-good"] is not None
