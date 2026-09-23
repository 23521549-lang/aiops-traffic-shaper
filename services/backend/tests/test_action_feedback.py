"""An action the operator takes has to change what the operator sees.

`ui-status.js` exists because of exactly one failure mode, recorded in its
own header comment: "an operator could click Suspend on a tenant, have the
request 403, and see the table sit unchanged - indistinguishable from
success". It fixed that for failures and left the success path with the same
shape.

The Allow button in the mitigation table posts with hx-swap="none", so htmx
discards the response by design. The server answered with a rendered
whitelist table that nothing was ever going to display. The source stayed in
the list, still labelled Blocked, until the operator thought to reload - and
the most likely reading of a button that appears to do nothing is to press
it again.

The same route still serves the allowed-list page, where swapping the table
in place IS the right answer. The two differ by where the operator is
standing, so the caller says so, and it says so from a fixed set: a
free-text return path on a POST is an open redirect.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="9.9.9.9", tier=2, score=-0.7, z=-6.4,
        reason="behavioral_anomaly", expires_at=0)
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf_token", "")}


def test_allowing_from_the_mitigation_table_sends_the_operator_back_to_it(client):
    """hx-swap="none" throws the body away, so the instruction has to be in
    a header or the click produces no visible effect at all."""
    r = client.post("/dashboard/ui/whitelist/9.9.9.9?back=status",
                    headers=_csrf(client))
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/dashboard/ui"


def test_and_the_source_is_gone_when_they_land(client):
    client.post("/dashboard/ui/whitelist/9.9.9.9?back=status", headers=_csrf(client))
    assert "9.9.9.9" not in client.get("/dashboard/ui").text


def test_the_allowed_list_page_still_swaps_its_table_in_place(client):
    """No redirect there: the operator is already looking at the thing that
    changed, and a full page load would lose their place in it."""
    r = client.post("/dashboard/ui/whitelist/9.9.9.9", headers=_csrf(client))
    assert "HX-Redirect" not in r.headers
    assert "9.9.9.9" in r.text


def test_a_return_path_the_product_did_not_choose_is_ignored(client):
    """The alternative design threads the destination through the query
    string, which turns every action button into an open redirect."""
    r = client.post("/dashboard/ui/whitelist/9.9.9.9?back=https://evil.example",
                    headers=_csrf(client))
    assert "HX-Redirect" not in r.headers


def test_suspending_a_tenant_from_its_detail_pane_returns_to_the_pane(
        admin_client):
    """Same rule on the publisher side. The pane shows the status it just
    changed, so it has to be re-read, not left displaying the old value."""
    r = admin_client.post("/admin/ui/tenants/t-1/suspend?back=detail",
                          headers=_csrf(admin_client))
    assert r.headers.get("HX-Redirect") == "/admin/ui/tenants?id=t-1"


@pytest.fixture
def admin_client(dynamo_resource, cognito_test_keys):
    from services.backend.core.tables import TenantsTable
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active", created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})})
    return c
