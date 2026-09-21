"""Per-tenant ingest quotas, on top of the global free-tier ceiling.

The global ceiling from Phase 6 protects the bill and nothing else. It counts
every tenant's traffic into one number, so the moment one tenant floods, ingest
is refused for ALL of them - a single noisy customer, or a single attacker
holding one customer's agent key, pauses protection for every other customer
on the platform. The retrospective carried this as open since Phase 6.

A per-tenant quota bounds what any one tenant can take from the shared daily
budget. The global ceiling stays underneath as the backstop for the bill.
"""
from fastapi.testclient import TestClient

from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.core.usage import (
    _DAILY_REQUEST_CEILING,
    _today,
    record_tenant_ingest,
    tenant_daily_quota,
    tenant_requests_today,
)
from services.backend.main import app

_LOGS = [{
    "time_iso8601": "2026-09-21T00:00:00Z", "remote_addr": "1.2.3.4",
    "request_method": "GET", "request_uri": "/a", "status": "200",
    "body_bytes_sent": "512", "request_time": "0.05", "http_user_agent": "ua-1",
}]


def _setup(dynamo_resource, *tenants):
    create_all_tables(dynamo_resource)
    for t in tenants:
        TenantsTable(dynamo_resource).put(tenant_id=t, name=t, status="active",
                                          created_at="2026-09-21T00:00:00Z")
        AgentsTable(dynamo_resource).put(
            tenant_id=t, agent_id=f"a-{t}", registered_at="2026-09-21T00:00:00Z",
            last_seen_at="2026-09-21T00:00:00Z", agent_version="0.1.0",
            api_key_hash=hash_api_key(f"key-{t}"), status="active",
        )
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver")


def _post(client, tenant):
    return client.post("/agent/v1/telemetry", json={"logs": _LOGS},
                       headers={"X-Agent-Key": f"{tenant}.key-{tenant}"})


def _exhaust(dynamo_resource, tenant):
    # One atomic ADD of the whole quota rather than thousands of single
    # increments: the counter semantics are identical and the test stays fast.
    record_tenant_ingest(dynamo_resource, tenant, count=tenant_daily_quota())


def test_one_noisy_tenant_no_longer_pauses_ingest_for_everyone(dynamo_resource):
    """The defect this file exists for. Under the global-only limiter, tenant
    `noisy` exhausting the budget refused telemetry from `quiet` too."""
    client = _setup(dynamo_resource, "noisy", "quiet")
    _exhaust(dynamo_resource, "noisy")

    assert _post(client, "noisy").status_code == 429
    assert _post(client, "quiet").status_code == 200


def test_tenant_throttle_says_it_is_the_tenant_and_carries_retry_after(dynamo_resource):
    """An agent must be able to tell 'my tenant is over quota' from 'the whole
    platform is over its ceiling' - and must have a basis for backing off."""
    client = _setup(dynamo_resource, "noisy")
    _exhaust(dynamo_resource, "noisy")

    resp = _post(client, "noisy")
    assert resp.status_code == 429
    assert "tenant" in resp.json()["detail"].lower()
    assert int(resp.headers["Retry-After"]) > 0


def test_a_successful_ingest_is_counted_against_its_own_tenant(dynamo_resource):
    client = _setup(dynamo_resource, "a", "b")
    assert _post(client, "a").status_code == 200

    assert tenant_requests_today(dynamo_resource, "a") == 1
    assert tenant_requests_today(dynamo_resource, "b") == 0


def test_a_refused_ingest_does_not_consume_quota(dynamo_resource):
    """Refusing must be cheap and must not itself burn the budget it protects
    - the same rule Phase 4 set for unauthenticated traffic."""
    client = _setup(dynamo_resource, "noisy")
    _exhaust(dynamo_resource, "noisy")
    before = tenant_requests_today(dynamo_resource, "noisy")

    _post(client, "noisy")
    assert tenant_requests_today(dynamo_resource, "noisy") == before


def test_tenant_counters_cannot_collide_with_the_global_counter(dynamo_resource):
    """Both live in UsageCounters, keyed on the same `date` attribute. A tenant
    named like a date must not be able to read or inflate the global row."""
    create_all_tables(dynamo_resource)
    record_tenant_ingest(dynamo_resource, _today())
    from services.backend.core.usage import get_usage_report
    assert get_usage_report(dynamo_resource, None).total_requests == 0


def test_quota_is_a_bounded_share_of_the_global_ceiling():
    """One tenant may take a large share of a day, never all of it: the point
    is that others are left room. And the quota must be reachable - a quota
    above the global ceiling would never trigger before the global one did."""
    assert 0 < tenant_daily_quota() < _DAILY_REQUEST_CEILING
