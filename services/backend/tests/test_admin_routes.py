from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    return TestClient(app)


def _admin_headers(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    return {"Authorization": f"Bearer {token}"}


def _non_admin_headers(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["user"]})
    return {"Authorization": f"Bearer {token}"}


def test_admin_routes_reject_non_admin(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/admin/v1/tenants", headers=_non_admin_headers(cognito_test_keys))
    assert resp.status_code == 403


def test_list_tenants(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.get("/admin/v1/tenants", headers=_admin_headers(cognito_test_keys))
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["tenant_id"] == "t-1"


def test_list_agents_by_liveness(dynamo_resource, cognito_test_keys):
    """Rewritten: the previous version wrote `status="stale"` into the table
    by hand and asserted the GSI returned it. Nothing in the product ever
    writes that value — status is "active" or "revoked" — so the test proved
    the query worked against data the system cannot produce, and the empty
    Agents card survived every green run. Liveness comes from last_seen_at."""
    from datetime import datetime, timedelta, timezone

    client = _client(dynamo_resource, cognito_test_keys)
    now = datetime.now(timezone.utc)
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-quiet", status="active",
                                      last_seen_at=(now - timedelta(hours=2)).isoformat(),
                                      agent_version="0.1.0")
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-live", status="active",
                                      last_seen_at=(now - timedelta(seconds=10)).isoformat(),
                                      agent_version="0.1.0")

    live = client.get("/admin/v1/agents", headers=_admin_headers(cognito_test_keys))
    assert live.status_code == 200
    assert [a["agent_id"] for a in live.json()] == ["a-live"]

    stale = client.get("/admin/v1/agents", params={"status": "stale"},
                       headers=_admin_headers(cognito_test_keys))
    assert [a["agent_id"] for a in stale.json()] == ["a-quiet"]


def test_suspend_tenant(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.post("/admin/v1/tenants/t-1/suspend", headers=_admin_headers(cognito_test_keys))
    assert resp.status_code == 200
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "suspended"


def test_suspend_missing_tenant_is_404(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/admin/v1/tenants/no-such/suspend", headers=_admin_headers(cognito_test_keys))
    assert resp.status_code == 404


def test_suspend_revokes_existing_agent_keys(dynamo_resource, cognito_test_keys):
    """H3: an issued agent API key outlives the suspension unless it is
    explicitly revoked — "deactivating an account invalidates its API keys"."""
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash="whatever", status="active",
    )
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    resp = client.post("/admin/v1/tenants/t-1/suspend",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    agents = AgentsTable(dynamo_resource).query_by_tenant("t-1")
    assert [a["status"] for a in agents] == ["revoked"]

