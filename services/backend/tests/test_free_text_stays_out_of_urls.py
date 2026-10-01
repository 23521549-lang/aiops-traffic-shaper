"""Spec 12.8: no long, free-form or secret input outside the login form.

The reason is written on the file that exists because of it. `signed-post.js`
says a token in a query string "lands in CloudFront access logs, Referer
headers and browser history", and that is true of anything else put there.

`data-params-in-url` moves EVERY value of a marked form into the query string.
Its own comment takes "never a credential" as the bar, which is lower than the
one the spec sets, and three forms were over it: a customer's contact email on
tenant creation, an operator's free-text reason on the allowed list, and a
machine label on agent creation.

The email is the one that matters most and the one fixed structurally: it is
another person's personal data, written into the platform's access logs by the
operator who was trying to onboard them. It now travels as a request body,
hashed by the mechanism ADR-005 built for exactly this.

The other two are bounded instead of moved, and that is a deliberate,
narrower call: a machine label and a note about your own IP are strings a
customer writes about their own infrastructure, so capping them answers the
"long" and "free-form" half of 12.8 without giving `signed-post.js` three more
callers and a document-replacement path that broke screen readers once
already.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.cognito_login import get_cognito_client
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    TenantsTable, WhitelistTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

TEMPLATES = Path(__file__).resolve().parents[1] / "ui" / "templates"


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


class _FakeCognito:
    """Creating a tenant creates its first Cognito user. These tests are
    about where the address travels, not about the pool."""

    def __init__(self):
        self.created: list[dict] = []

    def admin_create_user(self, **kw):
        self.created.append(kw)
        return {"User": {"Username": kw["Username"]}}


@pytest.fixture
def ops(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    app.dependency_overrides[get_cognito_client] = lambda: _FakeCognito()
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"cognito:groups": ["admin"], "email": "ops@example.com"})})
    yield c
    app.dependency_overrides.pop(get_cognito_client, None)


@pytest.fixture
def customer(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "owner@acme.example"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


# --- the email, moved out of the URL entirely ------------------------------


def test_a_customers_email_does_not_ride_in_a_query_string():
    """Another person's personal data, written into the platform's own access
    logs by the operator onboarding them."""
    form = (TEMPLATES / "admin_tenant_new.html").read_text(encoding="utf-8")

    assert "data-params-in-url" not in form
    assert "data-signed-post" in form


def test_the_tenant_form_posts_a_real_body():
    form = (TEMPLATES / "admin_tenant_new.html").read_text(encoding="utf-8")

    assert 'method="post"' in form
    assert 'action="/admin/ui/tenants"' in form
    assert "hx-post" not in form


def test_creating_a_tenant_still_works(ops, seeded):
    response = ops.post("/admin/ui/tenants",
                        data={"name": "Globex",
                              "contact_email": "owner@globex.example",
                              "tenant_id": "t-globex"},
                        headers=_csrf(ops), follow_redirects=False)

    assert response.status_code == 303
    assert TenantsTable(seeded).get(tenant_id="t-globex") is not None


def test_the_confirmation_survives_the_redirect(ops, seeded):
    """A redirect and not a fragment, so the values stay in the body. The
    tenant id carries the confirmation back, and a tenant id is already in
    every URL on this screen."""
    ops.post("/admin/ui/tenants",
             data={"name": "Globex", "contact_email": "owner@globex.example",
                   "tenant_id": "t-globex"},
             headers=_csrf(ops), follow_redirects=False)

    page = ops.get("/admin/ui/tenants?created=t-globex").text

    assert "t-globex" in page
    assert "created" in page.lower()


def test_the_redirect_carries_no_email(ops, seeded):
    """The whole point. A confirmation that put the address in the location
    header would have moved the leak rather than closed it."""
    response = ops.post("/admin/ui/tenants",
                        data={"name": "Globex",
                              "contact_email": "owner@globex.example",
                              "tenant_id": "t-globex"},
                        headers=_csrf(ops), follow_redirects=False)

    assert "globex.example" not in response.headers.get("location", "")


def test_a_rejected_tenant_answers_in_plain_text(ops, seeded):
    """signed-post.js writes a non-redirect response straight into the error
    box as text. Returning HTML there would print markup at the operator."""
    response = ops.post("/admin/ui/tenants",
                        data={"name": "", "contact_email": "not-an-email",
                              "tenant_id": "t-bad"},
                        headers=_csrf(ops), follow_redirects=False)

    assert response.status_code == 400
    assert "<" not in response.text


# --- the two that are bounded rather than moved ----------------------------


def test_an_allowed_reason_is_capped(customer, seeded):
    """Answers the "long" half of 12.8. A note about your own IP is not a
    credential and not a third party's data, but an unbounded one in a URL is
    still an unbounded one in an access log."""
    customer.post("/dashboard/ui/allowed?ip=10.0.0.9&reason=" + "x" * 500,
                  headers=_csrf(customer))

    entry = WhitelistTable(seeded).get(tenant_id="t-1", ip="10.0.0.9")

    assert entry is None or len(entry.get("reason", "")) <= 120


def test_an_agent_label_is_capped(customer, seeded):
    response = customer.post("/dashboard/ui/agents?label=" + "x" * 500,
                             headers=_csrf(customer))

    assert response.status_code == 200
    assert "x" * 500 not in response.text


def test_the_cap_is_stated_where_it_is_typed(customer, seeded):
    """A field that silently truncates is worse than one that says its
    limit."""
    page = customer.get("/dashboard/ui/allowed").text

    assert "maxlength" in page


# --- the rule itself -------------------------------------------------------


def test_only_bounded_fields_are_left_riding_in_urls():
    """The check that keeps this from coming back. Any form marked
    data-params-in-url must declare a maxlength on every text input it
    carries, so nothing unbounded can reach a URL by being added later.
    """
    import re

    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for form in re.findall(r"<form[^>]*data-params-in-url.*?</form>",
                               text, re.S):
            for field in re.findall(r'<input[^>]*type="text"[^>]*>', form):
                if "maxlength" not in field:
                    offenders.append((path.name, field[:70]))

    assert offenders == []
