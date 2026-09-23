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


def test_static_assets_are_served(dynamo_resource, cognito_test_keys):
    # Uses the moto-backed fixture too, even though this route itself
    # doesn't touch DynamoDB — found while writing this test: the
    # usage-tracking middleware (Stage 5) runs on EVERY request including
    # this one, and needs a real (or moto) table to write to.
    client = _client(dynamo_resource, cognito_test_keys)
    for name, marker in [("app.css", "--tier-blocked"),
                         ("htmx.min.js", "htmx"),
                         ("signed-post.js", "x-amz-content-sha256"),
                         ("ui-status.js", "htmx:responseError")]:
        resp = client.get(f"/ui/static/{name}")
        assert resp.status_code == 200, name
        assert marker in resp.text, name


def test_static_assets_are_cacheable(dynamo_resource, cognito_test_keys):
    """ADR-007 adds a CloudFront cache behaviour for /ui/static/*, which
    removes one uncounted Lambda invocation per asset per page view. The
    max-age is the bound on the one thing that buys: a cached asset does NOT
    roll back when the Lambda alias is repointed."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.get("/ui/static/app.css")
    assert "max-age=300" in resp.headers["Cache-Control"]


def test_the_static_route_serves_only_known_files(dynamo_resource, cognito_test_keys):
    """The filename is matched against a dict, never joined onto a path, so
    traversal is not a class of bug that can occur here. This is the test
    that keeps it that way."""
    client = _client(dynamo_resource, cognito_test_keys)
    assert client.get("/ui/static/../dashboard.py").status_code == 404
    assert client.get("/ui/static/nope.js").status_code == 404


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


def test_the_vendored_htmx_is_exactly_the_bytes_that_were_reviewed():
    """The supply-chain control for this repo's only third-party frontend
    dependency.

    `pip-audit` runs in CI but cannot see JavaScript, and there is no
    package.json, no lockfile and no npm anywhere in the tree. Pinning the
    hash in a test that already runs is therefore the strongest check
    available — and it is stronger than what a lockfile would give, because
    it fails on the bytes rather than on a version string.

    Every claim ADR-007 makes about htmx's defaults and hooks was read out
    of exactly this file. If it changes, those claims need re-reading.
    """
    import hashlib
    from pathlib import Path

    blob = (Path(__file__).resolve().parents[1] / "ui" / "static" / "htmx.min.js").read_bytes()
    assert len(blob) == 50_917
    assert hashlib.sha256(blob).hexdigest() == \
        "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"


def test_htmx_is_configured_down_from_its_defaults():
    """Each of these is a security decision (ADR-007), and each default is
    the unsafe direction:

      allowScriptTags  htmx executes <script> in swapped content by default;
                       the innerHTML it replaced did not.
      allowEval        removes the new Function() path so script-src 'self'
                       holds with no 'unsafe-eval'.
      historyEnabled   htmx caches page HTML in localStorage — tenant-scoped
                       markup persisted on a possibly shared machine.
      includeIndicatorStyles
                       htmx injects an inline <style>, which would force
                       style-src back open.
    """
    shell = _static("templates/base.html")
    assert 'name="htmx-config"' in shell
    for setting in ('"allowEval":false', '"allowScriptTags":false',
                    '"includeIndicatorStyles":false', '"historyEnabled":false'):
        assert setting in shell, setting


def test_only_the_login_request_still_needs_signing():
    """ADR-005's payload-hash obligation was spread across two functions
    serving every mutation. It is now one file with one caller, because
    every other interaction moved its arguments into the path or query
    string and carries no body at all.

    If a second file starts hashing, the obligation has spread again."""
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "ui" / "static"
    # Match on the CALL, not the header name: ui-status.js mentions the
    # header in a comment explaining why it does not need it, and a grep
    # that cannot tell those apart would have failed for the wrong reason.
    hashing = [f.name for f in static.glob("*.js")
               if '.digest("SHA-256"' in f.read_text(encoding="utf-8")]
    assert hashing == ["signed-post.js"], hashing
    assert "x-amz-content-sha256" in _static("static/signed-post.js")


def test_a_failed_sign_in_reports_itself_without_replacing_the_document():
    """The old handler did `document.documentElement.innerHTML = html` on a
    401. That is not a navigation: no page-load event, `autofocus` does not
    re-fire, and a live region inserted along with its own document is never
    announced. A screen-reader user pasting a bad token got complete
    silence, at the front door of the product."""
    js = _static("static/signed-post.js")
    # The assignment, not the word — the comment above it records what the
    # old handler did and why it was wrong.
    assert "documentElement.innerHTML" not in js
    assert "box.focus()" in js

    page = _static("templates/login.html")
    assert 'data-login-error' in page
    assert 'role="alert"' in page
    assert 'tabindex="-1"' in page
