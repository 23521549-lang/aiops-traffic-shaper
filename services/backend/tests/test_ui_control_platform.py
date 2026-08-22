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
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    client.post("/ui/login", data={"id_token": token})
    return client


def test_control_platform_shows_all_tenants(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="Globex", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "Globex" in resp.text


def test_control_platform_shows_ceiling_warning_banner(dynamo_resource, cognito_test_keys):
    from services.backend.core.tables import UsageCountersTable
    from services.backend.core.usage import _today
    client = _client(dynamo_resource, cognito_test_keys)
    UsageCountersTable(dynamo_resource).put(date=_today(), total_requests=999999, estimated_gb_seconds=0.0)
    resp = client.get("/admin/ui")
    assert "approaching today" in resp.text


def test_suspend_tenant_via_ui(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.post("/admin/ui/tenants/t-1/suspend")
    assert resp.status_code == 200
    assert "Suspended" in resp.text
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "suspended"


def test_agents_partial_filters_by_status(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1", status="active",
                                      last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0")
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-2", status="stale",
                                      last_seen_at="2026-08-20T00:00:00Z", agent_version="0.1.0")

    resp_stale = client.get("/admin/ui/agents", params={"status": "stale"})
    assert "a-2" in resp_stale.text
    assert "a-1" not in resp_stale.text

    resp_active = client.get("/admin/ui/agents", params={"status": "active"})
    assert "a-1" in resp_active.text
    assert "a-2" not in resp_active.text


def test_non_admin_cannot_reach_control_platform(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    client.post("/ui/login", data={"id_token": token})

    resp = client.get("/admin/ui", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"
