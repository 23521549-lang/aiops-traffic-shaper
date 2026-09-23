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


# --- ADR-005: CloudFront OAC does not sign request bodies -----------------

def _static(name: str) -> str:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "ui"
    return (root / name).read_text()


def test_login_form_is_marked_for_signed_submission():
    """A plain <form method="post"> is built and sent by the browser, which
    cannot add x-amz-content-sha256 - so through CloudFront it is rejected at
    the edge with a 403 nobody can debug from the application logs. The marker
    is what routes it through fetch instead."""
    assert 'data-signed-post' in _static("templates/login.html")


def test_ui_helper_hashes_request_bodies():
    js = _static("static/interactions.js")
    assert "x-amz-content-sha256" in js
    assert "SHA-256" in js


def test_signed_form_handler_rebinds_after_replacing_the_document():
    """The error path rewrites the whole document, which drops every event
    listener. Without a rebind the second sign-in attempt degrades into an
    unsigned plain form post and fails at the edge - a bug that would only
    appear on someone's second try with a bad token."""
    js = _static("static/interactions.js")
    handler = js[js.index("function bindSignedForm"):js.index("function bindAll")]
    assert "documentElement.innerHTML" in handler
    assert "bindAll(document)" in handler


# --- the site root -------------------------------------------------------

def test_the_site_root_leads_to_the_login_page(dynamo_resource, cognito_test_keys):
    """Every documented entry point was a sub-path (/ui/login, /dashboard/ui,
    /admin/ui) and nothing answered "/". A person handed the CloudFront URL
    opened it and got {"detail":"Not Found"} — FastAPI's JSON 404, on what is
    for all practical purposes the product's front door."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/login"


def test_the_root_redirect_costs_no_dynamodb_write(dynamo_resource, cognito_test_keys):
    """The root is public, unauthenticated and the most likely target of
    drive-by traffic, so metering it would hand an anonymous caller the same
    lever on the free-tier write quota that M8 closed for /health and
    /ui/login. A 302 is not in the (401, 403, 429) set the metering middleware
    already skips, so this needs its own exemption."""
    from services.backend.tests.test_perf_budget import _OpCounter

    client = _client(dynamo_resource, cognito_test_keys)
    counter = _OpCounter(dynamo_resource)
    client.get("/", follow_redirects=False)

    assert counter.counts.get("UpdateItem", 0) == 0, counter.counts
    assert counter.counts.get("PutItem", 0) == 0, counter.counts
