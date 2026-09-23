"""Signing in the way a person expects to.

The login screen asked for a pasted 1074-character JWT, and the template
said so itself: "Known v1 gap". Anyone who can obtain that token already has
AWS CLI access; the person the screen exists for cannot get past it.

It also blocked tenant creation outright — `AdminCreateUser` emails a
temporary password, and the product gave the recipient no way to spend one.

Cognito is faked here rather than reached: `boto3.client("cognito-idp")` is
behind a FastAPI dependency so a test can substitute it. Nothing in this
file talks to AWS.
"""
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.cognito_login import get_cognito_client
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


class FakeCognito:
    """Records what was asked and answers what the test set up."""

    def __init__(self, auth_result=None, challenge=None, error=None):
        self._auth_result = auth_result
        self._challenge = challenge
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    def _maybe_raise(self):
        if self._error:
            raise ClientError({"Error": {"Code": self._error}}, "InitiateAuth")

    def initiate_auth(self, **kw):
        self.calls.append(("initiate_auth", kw))
        self._maybe_raise()
        if self._challenge:
            return {"ChallengeName": self._challenge, "Session": "opaque-session"}
        return {"AuthenticationResult": {"IdToken": self._auth_result}}

    def respond_to_auth_challenge(self, **kw):
        self.calls.append(("respond_to_auth_challenge", kw))
        self._maybe_raise()
        return {"AuthenticationResult": {"IdToken": self._auth_result}}


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    yield TestClient(app, base_url="https://testserver")
    app.dependency_overrides.pop(get_cognito_client, None)


def _use(cognito):
    app.dependency_overrides[get_cognito_client] = lambda: cognito
    return cognito


def _token(keys, claims):
    return sign_test_token(keys["private_pem"], claims)


# --- the happy path -----------------------------------------------------

def test_a_tenant_signs_in_with_email_and_password(client, cognito_test_keys):
    _use(FakeCognito(auth_result=_token(cognito_test_keys,
                                        {"custom:tenant_id": "t-1"})))

    resp = client.post("/ui/login",
                       data={"email": "owner@acme.test", "password": "hunter2hunter2"},
                       follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard/ui"
    assert "id_token" in resp.cookies


def test_an_admin_lands_on_the_control_platform(client, cognito_test_keys):
    _use(FakeCognito(auth_result=_token(cognito_test_keys,
                                        {"cognito:groups": ["admin"]})))

    resp = client.post("/ui/login",
                       data={"email": "ops@example.test", "password": "hunter2hunter2"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/admin/ui"


def test_the_password_never_reaches_a_url(client, cognito_test_keys):
    """It goes in the request body, which is why this is the one POST that
    still needs the signing shim. A password in a query string lands in
    CloudFront access logs, Referer headers and browser history."""
    cognito = _use(FakeCognito(auth_result=_token(cognito_test_keys,
                                                  {"custom:tenant_id": "t-1"})))
    resp = client.post("/ui/login",
                       data={"email": "owner@acme.test", "password": "s3cret-value"},
                       follow_redirects=False)

    assert "s3cret-value" not in str(resp.url)
    assert "s3cret-value" not in resp.headers.get("location", "")
    assert cognito.calls[0][1]["AuthParameters"]["PASSWORD"] == "s3cret-value"


# --- first sign-in ------------------------------------------------------

def test_a_temporary_password_leads_to_choosing_a_real_one(client):
    """Every user this product has was created by an administrator, so
    NEW_PASSWORD_REQUIRED is the normal first sign-in, not an edge case."""
    _use(FakeCognito(challenge="NEW_PASSWORD_REQUIRED"))

    resp = client.post("/ui/login",
                       data={"email": "new@acme.test", "password": "Temp-1234abcd"})

    assert resp.status_code == 200
    assert "Choose a password" in resp.text
    assert "opaque-session" in resp.text


def test_setting_the_new_password_signs_you_in(client, cognito_test_keys):
    _use(FakeCognito(auth_result=_token(cognito_test_keys,
                                        {"custom:tenant_id": "t-1"})))

    resp = client.post("/ui/login/new-password", data={
        "email": "new@acme.test", "session": "opaque-session",
        "new_password": "CorrectHorse12", "confirm_password": "CorrectHorse12",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard/ui"


def test_mismatched_passwords_are_caught_before_cognito(client):
    """Cheap, and it keeps a typo from burning a single-use session handle."""
    cognito = _use(FakeCognito())

    resp = client.post("/ui/login/new-password", data={
        "email": "new@acme.test", "session": "opaque-session",
        "new_password": "CorrectHorse12", "confirm_password": "CorrectHorse13",
    })

    assert resp.status_code == 400
    assert "do not match" in resp.text
    assert cognito.calls == []
    assert "opaque-session" in resp.text, "the session must survive the retry"


def test_a_weak_password_explains_the_policy(client):
    _use(FakeCognito(error="InvalidPasswordException"))

    resp = client.post("/ui/login/new-password", data={
        "email": "new@acme.test", "session": "opaque-session",
        "new_password": "short", "confirm_password": "short",
    })
    assert resp.status_code == 400
    assert "12 characters" in resp.json()["detail"]


# --- failures -----------------------------------------------------------

@pytest.mark.parametrize("code", ["NotAuthorizedException", "UserNotFoundException"])
def test_wrong_password_and_no_such_user_read_identically(client, code):
    """Different messages would make this an account-enumeration oracle:
    an unauthenticated caller could learn which addresses are registered."""
    _use(FakeCognito(error=code))
    resp = client.post("/ui/login",
                       data={"email": "someone@example.test", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "That email and password did not match."


def test_an_empty_form_asks_for_the_fields_rather_than_failing_oddly(client):
    _use(FakeCognito())
    resp = client.post("/ui/login", data={})
    assert resp.status_code == 400
    assert "Enter your email and password" in resp.text


def test_a_challenge_this_console_cannot_do_fails_loudly(client):
    """MFA is not configured on this pool. Pretending the sign-in worked
    would be worse than saying so."""
    _use(FakeCognito(challenge="SOFTWARE_TOKEN_MFA"))
    resp = client.post("/ui/login",
                       data={"email": "a@b.test", "password": "hunter2hunter2"})
    assert resp.status_code == 401
    assert "cannot do yet" in resp.json()["detail"]


# --- the old path still works -------------------------------------------

def test_the_pasted_token_path_survives(client, cognito_test_keys):
    """The agent CLI's published `register --token` workflow hands out an ID
    token. Removing this would break an install flow that is already
    documented — it is kept, just no longer the default on the page."""
    resp = client.post("/ui/login", data={
        "id_token": _token(cognito_test_keys, {"custom:tenant_id": "t-1"}),
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard/ui"


def test_the_login_page_leads_with_email_and_password(client):
    page = client.get("/ui/login").text
    assert 'name="email"' in page
    assert 'autocomplete="current-password"' in page
    # The token field is still there, behind a disclosure rather than first.
    assert "Sign in with an ID token instead" in page
    assert page.index('name="email"') < page.index('name="id_token"')
