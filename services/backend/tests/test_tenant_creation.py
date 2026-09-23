"""The route that did not exist.

There was no create-tenant endpoint anywhere in the product. Every tenant
that has ever existed was written straight into DynamoDB by hand — which is
exactly why the one in production carries a `note` instead of a `name` and
500'd both admin pages the first time anyone loaded them with a real admin
session.

The ordering under test is the part worth getting right: **DynamoDB first,
Cognito second, `provisioning` in between.** Cognito-first would leave a
user whose `custom:tenant_id` points at nothing, and `assert_tenant_active`
fails closed on a missing tenant — so that user would be silently inert with
no row anywhere for an operator to find.
"""
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.cognito_login import get_cognito_client
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


class FakeCognito:
    def __init__(self, error=None):
        self._error = error
        self.created: list[dict] = []

    def admin_create_user(self, **kw):
        if self._error:
            raise ClientError({"Error": {"Code": self._error}}, "AdminCreateUser")
        self.created.append(kw)
        return {"User": {"Username": kw["Username"]}}


@pytest.fixture
def admin(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})})
    yield client
    app.dependency_overrides.pop(get_cognito_client, None)


def _use(cognito):
    app.dependency_overrides[get_cognito_client] = lambda: cognito
    return cognito


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _create(client, **over):
    body = {"name": "Acme Storefront", "contact_email": "owner@acme.test",
            "tenant_id": "acme-1"}
    body.update(over)
    return client.post("/admin/v1/tenants", json=body, headers=_csrf(client))


# --- the happy path -----------------------------------------------------

def test_a_tenant_and_its_first_user_are_created(admin, dynamo_resource):
    cognito = _use(FakeCognito())

    resp = _create(admin)

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"
    assert body["password_delivery"] == "cognito_email"

    row = TenantsTable(dynamo_resource).get(tenant_id="acme-1")
    assert row["name"] == "Acme Storefront"
    assert row["status"] == "active"
    assert len(cognito.created) == 1


def test_the_tenant_id_claim_is_set_at_creation(admin):
    """`custom:tenant_id` is `mutable = false` and absent from the client's
    write_attributes, so creation is the ONLY moment it can ever be set.
    There is no repair path for a user created without it — only
    delete-and-recreate."""
    cognito = _use(FakeCognito())
    _create(admin)

    attrs = {a["Name"]: a["Value"] for a in cognito.created[0]["UserAttributes"]}
    assert attrs["custom:tenant_id"] == "acme-1"


def test_the_email_is_marked_verified(admin):
    """The pool sets account recovery to verified_email and verifies nobody,
    so a user created without this cannot ever reset their password."""
    cognito = _use(FakeCognito())
    _create(admin)

    attrs = {a["Name"]: a["Value"] for a in cognito.created[0]["UserAttributes"]}
    assert attrs["email_verified"] == "true"


def test_the_temporary_password_never_appears_in_the_response(admin):
    """Cognito emails it. Returning it would put a live credential in a
    response body, a browser history entry and any log that records one."""
    _use(FakeCognito())
    body = _create(admin).json()
    assert "password" not in str(body).lower().replace("password_delivery", "")


# --- the failures that matter -------------------------------------------

def test_creating_over_an_existing_tenant_is_refused(admin, dynamo_resource):
    """`put()` would silently replace it, which for this table means
    detaching every agent and every history row from the owner they belong
    to."""
    _use(FakeCognito())
    TenantsTable(dynamo_resource).put(tenant_id="acme-1", name="Someone Else",
                                      status="active", created_at="2026-01-01T00:00:00Z")

    resp = _create(admin)

    assert resp.status_code == 409
    assert TenantsTable(dynamo_resource).get(tenant_id="acme-1")["name"] == "Someone Else"


def test_a_cognito_failure_leaves_a_findable_row(admin, dynamo_resource):
    """The reason for DynamoDB-first. A half-created tenant must be visible
    to an operator, not invisible — and `provisioning` is not `active`, so
    `assert_tenant_active` already refuses to serve it."""
    _use(FakeCognito(error="InvalidParameterException"))

    resp = _create(admin)

    assert resp.status_code == 502
    row = TenantsTable(dynamo_resource).get(tenant_id="acme-1")
    assert row is not None
    assert row["status"] == "provisioning"


def test_retrying_a_half_created_tenant_finishes_it(admin, dynamo_resource):
    """The retry path the `provisioning` state exists for."""
    _use(FakeCognito(error="InvalidParameterException"))
    _create(admin)

    _use(FakeCognito())
    resp = _create(admin)

    assert resp.status_code == 201
    assert TenantsTable(dynamo_resource).get(tenant_id="acme-1")["status"] == "active"


def test_a_user_that_already_exists_is_treated_as_success(admin, dynamo_resource):
    """A retry whose first attempt died after Cognito succeeded."""
    _use(FakeCognito(error="UsernameExistsException"))

    resp = _create(admin)

    assert resp.status_code == 201
    assert TenantsTable(dynamo_resource).get(tenant_id="acme-1")["status"] == "active"


def test_a_provisioning_tenant_is_not_served(admin, dynamo_resource):
    from fastapi import HTTPException

    from services.backend.api.dependencies import assert_tenant_active

    _use(FakeCognito(error="InvalidParameterException"))
    _create(admin)

    with pytest.raises(HTTPException) as exc:
        assert_tenant_active(dynamo_resource, "acme-1")
    assert exc.value.status_code == 403


def test_a_malformed_email_is_rejected_before_anything_is_written(admin, dynamo_resource):
    cognito = _use(FakeCognito())
    resp = _create(admin, contact_email="not-an-email")

    assert resp.status_code == 422
    assert TenantsTable(dynamo_resource).get(tenant_id="acme-1") is None
    assert cognito.created == []


def test_a_nameless_tenant_cannot_be_created_from_here(admin):
    """`Tenant.name` stays optional for rows written before this route
    existed — including the one in production. Nothing created from here
    should ever be nameless again."""
    _use(FakeCognito())
    assert _create(admin, name="").status_code == 422


# --- authorisation -------------------------------------------------------

def test_a_tenant_user_cannot_create_tenants(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})

    resp = client.post("/admin/v1/tenants",
                       json={"name": "X", "contact_email": "a@b.test"},
                       headers=_csrf(client))
    assert resp.status_code == 403


def test_creation_needs_the_csrf_token(admin):
    _use(FakeCognito())
    resp = admin.post("/admin/v1/tenants",
                      json={"name": "X", "contact_email": "a@b.test"})
    assert resp.status_code == 403


# --- the form ------------------------------------------------------------

def test_the_form_carries_a_server_minted_id(admin):
    """Idempotency without an idempotency-key table: a double submit
    collides on attribute_not_exists and reads as "already exists" rather
    than quietly making a second tenant."""
    page = admin.get("/admin/ui/tenants/new").text
    assert 'name="tenant_id"' in page
    import re
    assert re.search(r'name="tenant_id" value="[0-9a-f]{32}"', page)


def test_creating_from_the_form_shows_the_new_tenant(admin):
    _use(FakeCognito())
    resp = admin.post(
        "/admin/ui/tenants?name=Acme&contact_email=owner@acme.test&tenant_id=acme-1",
        headers=_csrf(admin))
    assert resp.status_code == 200
    assert "acme-1" in resp.text
    assert "temporary password has been emailed" in resp.text
