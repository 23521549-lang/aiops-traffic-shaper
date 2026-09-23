from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, WhitelistTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _client(dynamo_resource, cognito_test_keys, tenant_id="t-1"):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": tenant_id})
    client.post("/ui/login", data={"id_token": token})
    return client


def _csrf(client) -> dict:
    """Phase 4 / M7: state-changing UI requests carry the double-submit token
    the browser's own script sends."""
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def test_dashboard_shows_own_tenant_mitigations_only(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    MitigationStateTable(dynamo_resource).put(tenant_id="t-1", ip="1.1.1.1", tier=2, score=-0.5,
                                               reason="behavioral_anomaly", expires_at=0)
    MitigationStateTable(dynamo_resource).put(tenant_id="t-2", ip="9.9.9.9", tier=2, score=-0.5,
                                               reason="behavioral_anomaly", expires_at=0)
    resp = client.get("/dashboard/ui")
    assert resp.status_code == 200
    assert "1.1.1.1" in resp.text
    assert "9.9.9.9" not in resp.text  # not vượt quá scope — never another tenant's data


def test_dashboard_empty_state(dynamo_resource, cognito_test_keys):
    """The empty state is the most-viewed screen in this product, and it now
    has to distinguish three kinds of nothing. With no agent ever registered
    it must NOT reassure — that was the defect: a tenant whose agent died
    three days ago saw a screen identical to one that was genuinely safe."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/ui")
    assert resp.status_code == 200
    assert "No agent has connected yet" in resp.text
    assert "You’re protected" not in resp.text


def test_the_model_page_explains_the_wait_instead_of_saying_shadow_mode(
        dynamo_resource, cognito_test_keys):
    """"Still in shadow mode" was internal vocabulary; no customer knows what
    shadow mode is."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/ui/model")
    assert resp.status_code == 200
    assert "Learning what your normal traffic looks like" in resp.text
    assert "shadow mode" not in resp.text


def test_whitelist_add_via_ui_form(dynamo_resource, cognito_test_keys):
    """Values ride in the query string, not a body. That is what keeps this
    request out of the signing shim: CloudFront's OAC only demands a payload
    hash when there IS a body (ADR-005/ADR-007)."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/dashboard/ui/whitelist?ip=203.0.113.4&reason=office",
                       headers=_csrf(client))
    assert resp.status_code == 200
    assert "203.0.113.4" in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is not None


def test_whitelist_add_invalid_ip_shows_error(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/dashboard/ui/whitelist?ip=not-an-ip", headers=_csrf(client))
    assert resp.status_code == 200
    assert "not a valid IP" in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="not-an-ip") is None


def test_whitelist_remove_via_ui(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    WhitelistTable(dynamo_resource).put(tenant_id="t-1", ip="203.0.113.4", added_at="x")
    resp = client.request("DELETE", "/dashboard/ui/whitelist/203.0.113.4",
                          headers=_csrf(client))
    assert resp.status_code == 200
    # The address still appears once, in the confirmation sentence. What must
    # be gone is the row: the Remove button that only a listed IP has.
    assert "Remove 203.0.113.4 from allowed list" not in resp.text
    assert "No IPs on your allowed list" in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is None


def test_dashboard_cannot_reach_admin_scope(dynamo_resource, cognito_test_keys):
    # A tenant-scoped cookie must not grant access to the admin surface —
    # "không vượt quá sự kiểm soát" (dashboard must not overreach its scope).
    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    resp = client.get("/admin/ui", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"


def test_the_degraded_banner_reads_as_a_sentence(dynamo_resource, cognito_test_keys):
    """Caught by reading the rendered page on the live deployment, not by a
    test: the banner said "We haven't heard from your agent in 2 days ago."
    `humanise_age` already returns a phrase ending in "ago", so the template's
    own "in" made it ungrammatical. Copy is part of the product; a security
    warning that reads as broken English undermines the warning."""
    from datetime import datetime, timedelta, timezone

    from services.backend.core.tables import AgentsTable

    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1", agent_label="nginx-01",
                                     registered_at=stale, last_seen_at=stale,
                                     agent_version="unknown", api_key_hash="h", status="active")

    page = client.get("/dashboard/ui").text
    assert "We haven’t heard from your agent." in page
    assert "Last contact was 2 days ago." in page
    assert "in 2 days ago" not in page
