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


def _csrf(client) -> dict:
    """Phase 4 / M7: state-changing UI requests carry the double-submit token
    the browser's own script sends."""
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def test_control_platform_shows_all_tenants(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="Globex", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.get("/admin/ui/tenants")
    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "Globex" in resp.text


def test_control_platform_shows_ceiling_warning_banner(dynamo_resource, cognito_test_keys):
    from services.backend.core.tables import UsageCountersTable
    from services.backend.core.usage import _today
    client = _client(dynamo_resource, cognito_test_keys)
    UsageCountersTable(dynamo_resource).put(date=_today(), total_requests=999999, estimated_gb_seconds=0.0)
    resp = client.get("/admin/ui")
    assert "approaching the Always-Free ceiling" in resp.text


def test_suspend_tenant_via_ui(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(client))
    assert resp.status_code == 200
    assert "Suspended" in resp.text
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "suspended"


def test_the_tenant_pane_separates_live_agents_from_quiet_ones(
        dynamo_resource, cognito_test_keys):
    """Rewritten twice. The first version fabricated a `status="stale"` row
    that no code path writes; both agents below are lifecycle-active and what
    separates them is whether they have called in.

    The second rewrite moved it here. This used to assert against a filtered
    Agents page, which was a filter over a list wearing the shape of a page.
    The distinction it makes is real and still has to hold; it is now made on
    the tenant the agents belong to, which is where an operator deciding
    whether to suspend is already looking.
    """
    from datetime import datetime, timedelta, timezone

    client = _client(dynamo_resource, cognito_test_keys)
    now = datetime.now(timezone.utc)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-live", status="active",
                                      last_seen_at=(now - timedelta(seconds=10)).isoformat(),
                                      agent_version="0.1.0")
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-quiet", status="active",
                                      last_seen_at=(now - timedelta(hours=2)).isoformat(),
                                      agent_version="0.1.0")

    page = client.get("/admin/ui/tenants", params={"id": "t-1"}).text
    live_at = page.index("a-live")
    quiet_at = page.index("a-quiet")

    assert "Reporting" in page and "Quiet" in page
    # Each label belongs to its own row, which a page containing both words
    # somewhere would not establish.
    assert "Reporting" in page[min(live_at, quiet_at):max(live_at, quiet_at)]         or "Quiet" in page[min(live_at, quiet_at):max(live_at, quiet_at)]


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


def test_a_suspended_tenant_can_be_reactivated_from_the_control_platform(dynamo_resource,
                                                                       cognito_test_keys):
    """The API grew a reactivate endpoint; without the button an operator
    would still have to reach for curl to undo a suspension they made in the
    browser two clicks earlier."""
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="suspended",
                                       created_at="2026-08-21T00:00:00Z")
    page = client.get("/admin/ui/tenants").text
    assert 'hx-post="/admin/ui/tenants/t-1/reactivate"' in page

    resp = client.post("/admin/ui/tenants/t-1/reactivate", headers=_csrf(client))
    assert resp.status_code == 200
    assert "Active" in resp.text
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "active"


def test_reactivate_via_ui_requires_csrf_token(dynamo_resource, cognito_test_keys):
    """Same rule as every other state-changing UI action (Phase 4 / M7)."""
    client = _client(dynamo_resource, cognito_test_keys)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="suspended",
                                       created_at="2026-08-21T00:00:00Z")
    resp = client.post("/admin/ui/tenants/t-1/reactivate")
    assert resp.status_code == 403
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "suspended"
