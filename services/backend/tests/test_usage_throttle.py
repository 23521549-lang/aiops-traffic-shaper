"""PRD US-4 AC3 — "cơ chế giới hạn/điều tiết khi tổng tải vượt ngưỡng free-tier".

Phase 5 recorded this acceptance criterion as NOT MET: usage was measured and a
ceiling warning was surfaced, but nothing ever limited anything. A warning that
nobody is awake to read does not protect a budget.

What is throttled and what is not is a deliberate split:

* `POST /agent/v1/telemetry` is the only high-frequency write path in the
  system — one aggregate write per distinct IP per batch, plus the mitigation
  writes it produces. It is the cost driver, so it is what gets refused.
* `GET /agent/v1/decisions` keeps serving, so agents already enforcing a block
  keep working through an overload instead of silently going open.
* The dashboard keeps serving, so the operator can see what is happening while
  it is happening.

Refusing must also be cheap: a rejected request must not itself consume the
quota it is protecting.
"""
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable,
    TenantsTable,
    UsageCountersTable,
    create_all_tables,
)
from services.backend.core.usage import _DAILY_REQUEST_CEILING, _today
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

_LOGS = [{
    "time_iso8601": "2026-08-21T00:00:00Z", "remote_addr": "1.2.3.4",
    "request_method": "GET", "request_uri": "/a", "status": "200",
    "body_bytes_sent": "512", "request_time": "0.05", "http_user_agent": "ua-1",
}]


def _client(dynamo_resource, cognito_test_keys, requests_today: int):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key("rawkey"), status="active",
    )
    UsageCountersTable(dynamo_resource).put(
        date=_today(), total_requests=requests_today, estimated_gb_seconds=0.0)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    return TestClient(app, base_url="https://testserver")


_OVER = int(_DAILY_REQUEST_CEILING) + 1_000
_UNDER = 10


def _telemetry(client):
    return client.post("/agent/v1/telemetry", json={"logs": _LOGS},
                       headers={"X-Agent-Key": "t-1.rawkey"})


def test_telemetry_is_accepted_below_the_ceiling(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys, _UNDER)
    assert _telemetry(client).status_code == 200


def test_telemetry_is_refused_once_the_daily_ceiling_is_crossed(dynamo_resource,
                                                                 cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys, _OVER)
    resp = _telemetry(client)
    assert resp.status_code == 429
    assert "ceiling" in resp.json()["detail"].lower()


def test_refusal_tells_the_agent_when_to_come_back(dynamo_resource, cognito_test_keys):
    """Without Retry-After an agent has no basis for a backoff and will simply
    hammer the endpoint it was just refused from."""
    client = _client(dynamo_resource, cognito_test_keys, _OVER)
    resp = _telemetry(client)
    assert int(resp.headers["Retry-After"]) > 0


def test_agents_keep_receiving_existing_decisions_while_throttled(dynamo_resource,
                                                                   cognito_test_keys):
    """An overload must not silently drop protection that is already in force."""
    client = _client(dynamo_resource, cognito_test_keys, _OVER)
    resp = client.get("/agent/v1/decisions", headers={"X-Agent-Key": "t-1.rawkey"})
    assert resp.status_code == 200


def test_dashboard_stays_readable_while_throttled(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys, _OVER)
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    resp = client.get("/dashboard/v1/mitigations",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_a_refused_request_does_not_spend_the_quota_it_protects(dynamo_resource,
                                                                 cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys, _OVER)
    before = int(UsageCountersTable(dynamo_resource).get(date=_today())["total_requests"])
    for _ in range(5):
        assert _telemetry(client).status_code == 429
    after = int(UsageCountersTable(dynamo_resource).get(date=_today())["total_requests"])
    assert after == before
