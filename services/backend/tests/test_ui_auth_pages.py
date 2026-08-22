from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    # https base_url so the login cookie's Secure attribute round-trips in
    # tests the same way it does in a real (always-HTTPS Lambda) deployment.
    return TestClient(app, base_url="https://testserver")


def test_login_page_renders(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/ui/login")
    assert resp.status_code == 200
    assert "Sign in" in resp.text


def test_login_tenant_token_redirects_to_dashboard(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    resp = client.post("/ui/login", data={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard/ui"
    assert "id_token" in resp.cookies


def test_login_admin_token_redirects_to_control_platform(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    resp = client.post("/ui/login", data={"id_token": token}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/ui"


def test_login_token_with_neither_claim_shows_error(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"sub": "user-1"})
    resp = client.post("/ui/login", data={"id_token": token})
    assert resp.status_code == 401
    assert "neither an admin group nor a tenant_id" in resp.text


def test_login_invalid_token_shows_error(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/ui/login", data={"id_token": "not-a-real-jwt"})
    assert resp.status_code == 401
    assert "Invalid or expired token" in resp.text


def test_dashboard_ui_unauthenticated_redirects_to_login(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/ui", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"


def test_admin_ui_unauthenticated_redirects_to_login(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/admin/ui", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"


def test_api_json_routes_unaffected_by_ui_redirect_handler(dynamo_resource, cognito_test_keys):
    # The exception handler only touches /dashboard/ui and /admin/ui paths
    # — the JSON API underneath (/dashboard/v1/*) must keep returning a
    # plain JSON 401 body, not a redirect, for non-browser clients.
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/dashboard/v1/mitigations", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["detail"]


def test_logout_clears_cookie_and_redirects(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    client.post("/ui/login", data={"id_token": token})
    resp = client.get("/ui/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"
    # Cookie cleared -> a subsequent dashboard visit is unauthenticated again
    resp2 = client.get("/dashboard/ui", follow_redirects=False)
    assert resp2.status_code == 302


def test_interactions_js_is_served(dynamo_resource, cognito_test_keys):
    # Uses the moto-backed fixture too, even though this route itself
    # doesn't touch DynamoDB — found while writing this test: the
    # usage-tracking middleware (Stage 5) runs on EVERY request including
    # this one, and needs a real (or moto) table to write to.
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/ui/static/interactions.js")
    assert resp.status_code == 200
    assert "hx-get" in resp.text
