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
