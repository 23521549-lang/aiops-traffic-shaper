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
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/ui")
    assert resp.status_code == 200
    assert "No active mitigations" in resp.text
    assert "shadow mode" in resp.text


def test_whitelist_add_via_ui_form(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/dashboard/ui/whitelist", data={"ip": "203.0.113.4", "reason": "office"})
    assert resp.status_code == 200
    assert "203.0.113.4" in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is not None


def test_whitelist_add_invalid_ip_shows_error(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/dashboard/ui/whitelist", data={"ip": "not-an-ip"})
    assert resp.status_code == 200
    assert "not a valid IP" in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="not-an-ip") is None


def test_whitelist_remove_via_ui(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    WhitelistTable(dynamo_resource).put(tenant_id="t-1", ip="203.0.113.4", added_at="x")
    resp = client.request("DELETE", "/dashboard/ui/whitelist/203.0.113.4")
    assert resp.status_code == 200
    assert "203.0.113.4" not in resp.text
    assert WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4") is None


def test_dashboard_cannot_reach_admin_scope(dynamo_resource, cognito_test_keys):
    # A tenant-scoped cookie must not grant access to the admin surface —
    # "không vượt quá sự kiểm soát" (dashboard must not overreach its scope).
    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    resp = client.get("/admin/ui", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"
