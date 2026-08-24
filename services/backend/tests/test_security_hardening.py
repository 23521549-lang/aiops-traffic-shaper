"""Phase 4 findings M6 (security headers) and M8 (free-tier burn)."""
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _today
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    return TestClient(app, base_url="https://testserver")


def _requests_today(dynamo_resource) -> int:
    row = UsageCountersTable(dynamo_resource).get(date=_today())
    return int(row["total_requests"]) if row else 0


# --- M6: security headers ------------------------------------------------

def test_ui_page_carries_security_headers(dynamo_resource, cognito_test_keys):
    """M6: the app shipped no security headers at all — no CSP, no
    clickjacking defence, no MIME-sniffing defence."""
    resp = _client(dynamo_resource, cognito_test_keys).get("/ui/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "max-age=" in resp.headers["Strict-Transport-Security"]


def test_json_api_also_carries_security_headers(dynamo_resource, cognito_test_keys):
    resp = _client(dynamo_resource, cognito_test_keys).get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_csp_allows_the_pages_own_inline_styles_and_external_script(dynamo_resource,
                                                                    cognito_test_keys):
    """The UI uses an inline <style> block and an external /ui/static script;
    a CSP that forbids either would silently break the pages."""
    csp = _client(dynamo_resource, cognito_test_keys).get("/ui/login").headers["Content-Security-Policy"]
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


# --- M8: unauthenticated traffic must not burn the free-tier ceiling -----

def test_unauthenticated_request_does_not_consume_usage_quota(dynamo_resource,
                                                               cognito_test_keys):
    """M8: track_usage recorded a DynamoDB write for EVERY request, including
    ones rejected as unauthenticated. Since the whole product is built to a
    ~0-cost free-tier ceiling and the Lambda Function URL has no AWS-native
    rate limiting (ADR-002's accepted residual risk), anyone could burn the
    tenant's quota with unauthenticated junk traffic."""
    client = _client(dynamo_resource, cognito_test_keys)
    before = _requests_today(dynamo_resource)
    for _ in range(5):
        assert client.get("/dashboard/v1/mitigations").status_code == 401
    assert _requests_today(dynamo_resource) == before


def test_health_check_does_not_consume_usage_quota(dynamo_resource, cognito_test_keys):
    """Liveness probes run constantly; metering them measures nothing useful
    and spends real write capacity."""
    client = _client(dynamo_resource, cognito_test_keys)
    before = _requests_today(dynamo_resource)
    for _ in range(5):
        client.get("/health")
    assert _requests_today(dynamo_resource) == before


def test_authenticated_request_still_consumes_quota(dynamo_resource, cognito_test_keys):
    """The mitigation must not gut the metering it is protecting: real,
    authenticated work is still counted."""
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    before = _requests_today(dynamo_resource)
    assert client.get("/dashboard/v1/mitigations",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _requests_today(dynamo_resource) == before + 1


def test_unauthenticated_ui_page_visit_does_not_consume_quota(dynamo_resource,
                                                               cognito_test_keys):
    """UI paths turn 401 into a 302 redirect to the login page, so a naive
    status-code check would miss them — the redirect must not be metered."""
    client = _client(dynamo_resource, cognito_test_keys)
    before = _requests_today(dynamo_resource)
    for _ in range(5):
        client.get("/dashboard/ui", follow_redirects=False)
    assert _requests_today(dynamo_resource) == before


# --- M7: CSRF double-submit token on state-changing UI requests ---------

def _logged_in(dynamo_resource, cognito_test_keys, claims):
    client = _client(dynamo_resource, cognito_test_keys)
    client.post("/ui/login",
                data={"id_token": sign_test_token(cognito_test_keys["private_pem"], claims)})
    return client


def test_login_issues_a_js_readable_csrf_cookie(dynamo_resource, cognito_test_keys):
    """Double-submit needs the token readable by the page's own script, unlike
    the id_token cookie which stays httpOnly."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/ui/login",
                       data={"id_token": sign_test_token(cognito_test_keys["private_pem"],
                                                         {"custom:tenant_id": "t-1"})},
                       follow_redirects=False)
    assert "csrf_token" in resp.cookies
    set_cookie = [h for h in resp.headers.get_list("set-cookie") if h.startswith("csrf_token=")][0]
    assert "HttpOnly" not in set_cookie
    assert "Secure" in set_cookie


def test_ui_state_change_without_csrf_header_is_rejected(dynamo_resource, cognito_test_keys):
    """M7: the UI's state-changing endpoints authenticated purely on a cookie.
    SameSite=Lax blocks the classic cross-site form POST, but that was the
    only thing standing between an attacker and these endpoints — no token,
    no Origin check, nothing the application itself verified."""
    client = _logged_in(dynamo_resource, cognito_test_keys, {"custom:tenant_id": "t-1"})
    resp = client.post("/dashboard/ui/whitelist", data={"ip": "203.0.113.4"})
    assert resp.status_code == 403


def test_ui_state_change_with_mismatched_csrf_token_is_rejected(dynamo_resource,
                                                                 cognito_test_keys):
    client = _logged_in(dynamo_resource, cognito_test_keys, {"custom:tenant_id": "t-1"})
    resp = client.post("/dashboard/ui/whitelist", data={"ip": "203.0.113.4"},
                       headers={"X-CSRF-Token": "not-the-right-token"})
    assert resp.status_code == 403


def test_ui_state_change_with_matching_csrf_token_succeeds(dynamo_resource, cognito_test_keys):
    client = _logged_in(dynamo_resource, cognito_test_keys, {"custom:tenant_id": "t-1"})
    resp = client.post("/dashboard/ui/whitelist", data={"ip": "203.0.113.4"},
                       headers={"X-CSRF-Token": client.cookies["csrf_token"]})
    assert resp.status_code == 200
    assert "203.0.113.4" in resp.text


def test_admin_ui_suspend_requires_csrf_token(dynamo_resource, cognito_test_keys):
    """The single most destructive UI action must not be the unprotected one."""
    client = _logged_in(dynamo_resource, cognito_test_keys, {"cognito:groups": ["admin"]})
    assert client.post("/admin/ui/tenants/t-1/suspend").status_code == 403


def test_json_api_is_not_subject_to_csrf_checks(dynamo_resource, cognito_test_keys):
    """The JSON API authenticates with a Bearer header, which a cross-site
    form cannot set — adding a token requirement there would break the agent
    CLI and every other non-browser client for no security gain."""
    client = _client(dynamo_resource, cognito_test_keys)
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    resp = client.post("/dashboard/v1/whitelist", json={"ip": "203.0.113.9"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
