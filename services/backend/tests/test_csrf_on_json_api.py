"""The JSON API's CSRF exemption rested on a claim that is not true.

`ui/csrf.py` documented the exemption like this:

    Deliberately NOT applied to the JSON API (`/dashboard/v1/*`,
    `/admin/v1/*`, `/agent/v1/*`): those authenticate on an
    `Authorization: Bearer` header, which a cross-site form also cannot
    set, so they are not CSRF-reachable.

They do not only authenticate that way. `dashboard_auth` and `admin_auth`
(api/cognito_auth.py) both take `id_token: str | None = Cookie(...)` and
feed it to the same `_extract_token`. A browser holding the login cookie
set by `/ui/login` authenticates against the JSON API perfectly well, so
`POST /admin/v1/tenants/{id}/suspend` IS reachable from a cross-site form —
defended only by `SameSite=Lax`, which is verbatim the thing csrf.py was
written because it did not want to rely on:

    `SameSite=Lax` blocks the cross-site form POST in current browsers,
    but it was the ONLY thing standing in the way.

So the HTML UI got defence in depth and the JSON API — same cookie, same
privileges, wider blast radius — got a comment asserting it did not need
any.

The fix keeps every legitimate client working. A caller presenting a
credential in a HEADER (`Authorization` or `X-Id-Token`) is not a browser
form and is left alone: the agent CLI, curl, and the signed fetches the UI
makes all still work. A caller authenticating by COOKIE alone must prove
the request came from our own page.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def admin_browser(dynamo_resource, cognito_test_keys):
    """A browser session: the login cookie, and nothing else."""
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    client.post("/ui/login", data={"id_token": token})
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-09-23T00:00:00Z")
    return client, dynamo_resource, token


def _status(resource) -> str:
    return TenantsTable(resource).get(tenant_id="t-1")["status"]


def test_a_cookie_only_post_to_the_json_api_is_refused(admin_browser):
    """The cross-site form's shape: the browser attaches the cookie, the
    attacker's page cannot read it to echo it back in a header."""
    client, resource, _ = admin_browser
    resp = client.post("/admin/v1/tenants/t-1/suspend")
    assert resp.status_code == 403
    assert _status(resource) == "active", "the tenant must not have been suspended"


def test_the_same_post_succeeds_when_the_page_proves_it_sent_it(admin_browser):
    client, resource, _ = admin_browser
    resp = client.post("/admin/v1/tenants/t-1/suspend",
                       headers={"X-CSRF-Token": client.cookies["csrf_token"]})
    assert resp.status_code == 200
    assert _status(resource) == "suspended"


def test_a_header_credential_needs_no_csrf_token(admin_browser):
    """The agent CLI, curl, and any other non-browser client. A cross-site
    form cannot set Authorization, so the original reasoning holds for
    exactly these callers — it just was not the only kind of caller."""
    client, resource, token = admin_browser
    client.cookies.clear()
    resp = client.post("/admin/v1/tenants/t-1/suspend",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert _status(resource) == "suspended"


def test_x_id_token_also_counts_as_a_header_credential(admin_browser):
    """ADR-005: CloudFront OAC replaces `Authorization`, so the portal's own
    fetches carry the token in X-Id-Token. Treating that as a browser-form
    credential would break the deployed product."""
    client, resource, token = admin_browser
    client.cookies.clear()
    resp = client.post("/admin/v1/tenants/t-1/suspend", headers={"X-Id-Token": token})
    assert resp.status_code == 200
    assert _status(resource) == "suspended"


def test_a_mismatched_token_is_refused(admin_browser):
    client, resource, _ = admin_browser
    resp = client.post("/admin/v1/tenants/t-1/suspend",
                       headers={"X-CSRF-Token": "not-the-right-value"})
    assert resp.status_code == 403
    assert _status(resource) == "active"


def test_the_tenant_json_api_is_protected_too(dynamo_resource, cognito_test_keys):
    """Lower blast radius than the admin routes, same exposure."""
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    client.post("/ui/login", data={"id_token": token})

    refused = client.post("/dashboard/v1/whitelist", json={"ip": "203.0.113.4", "reason": ""})
    assert refused.status_code == 403

    allowed = client.post("/dashboard/v1/whitelist", json={"ip": "203.0.113.4", "reason": ""},
                          headers={"X-CSRF-Token": client.cookies["csrf_token"]})
    assert allowed.status_code == 200


def test_reads_are_not_gated(admin_browser):
    """CSRF is about state change. Gating GETs would break plain navigation
    and buys nothing — a cross-site GET cannot alter anything here."""
    client, _, _ = admin_browser
    assert client.get("/admin/v1/tenants").status_code == 200
