import pytest
from fastapi import HTTPException

from services.backend.api.cognito_auth import admin_auth, dashboard_auth
from services.backend.tests.conftest import sign_test_token


def test_dashboard_auth_extracts_tenant_id(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    tenant_id = dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert tenant_id == "t-1"


def test_dashboard_auth_accepts_cookie_when_no_header(cognito_test_keys):
    # Stage 9: plain browser navigation can't attach a custom Authorization
    # header, only JS/fetch can — the server-rendered UI relies on this
    # cookie fallback to work at all.
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    tenant_id = dashboard_auth(authorization=None, id_token=token, jwks=cognito_test_keys["jwks"])
    assert tenant_id == "t-1"


def test_dashboard_auth_header_takes_precedence_over_cookie(cognito_test_keys):
    header_token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    cookie_token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-2"})
    tenant_id = dashboard_auth(authorization=f"Bearer {header_token}", id_token=cookie_token,
                                jwks=cognito_test_keys["jwks"])
    assert tenant_id == "t-1"


def test_dashboard_auth_missing_header_is_401(cognito_test_keys):
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=None, jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_dashboard_auth_missing_tenant_claim_is_401(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"sub": "user-1"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_dashboard_auth_tampered_signature_is_401(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    tampered = token[:-4] + "abcd"  # corrupt the signature segment
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {tampered}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_dashboard_auth_wrong_signing_key_is_401(cognito_test_keys):
    # Signed with a DIFFERENT keypair than the one in jwks — same kid by
    # coincidence, but the signature must not verify against the wrong key.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = sign_test_token(other_pem, {"custom:tenant_id": "t-1"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_admin_auth_requires_admin_group(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["user"]})
    with pytest.raises(HTTPException) as exc:
        admin_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 403


def test_admin_auth_passes_for_admin_group(cognito_test_keys):
    token = sign_test_token(cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})
    admin_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])  # no raise


# --- Phase 4 findings H1 / L9: token confusion --------------------------
# Each of these fails against the pre-Phase-4 auth code, for a different
# reason the old implementation never checked.

def test_admin_auth_rejects_cognito_access_token(cognito_test_keys):
    """H1: Cognito access tokens are signed by the SAME pool key and DO carry
    `cognito:groups` — so with no `token_use` check they sail straight through
    admin_auth. Access tokens are handed to resource servers and are far more
    exposed than ID tokens; accepting one is an admin-surface bypass."""
    token = sign_test_token(cognito_test_keys["private_pem"],
                            {"token_use": "access", "cognito:groups": ["admin"]})
    with pytest.raises(HTTPException) as exc:
        admin_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_auth_rejects_token_minted_for_another_app_client(cognito_test_keys):
    """H1: a token issued to a different app client in the same user pool."""
    token = sign_test_token(cognito_test_keys["private_pem"],
                            {"custom:tenant_id": "t-1", "aud": "some-other-client"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_auth_rejects_token_from_another_issuer(cognito_test_keys):
    """H1: `iss` was never validated — a token was judged on signature alone."""
    token = sign_test_token(cognito_test_keys["private_pem"],
                            {"custom:tenant_id": "t-1",
                             "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_evil"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_auth_fails_closed_when_cognito_is_not_configured(cognito_test_keys, _cognito_settings):
    """H1 root cause: `cognito_app_client_id` defaults to "" and the old code
    switched audience verification OFF when it was empty — an unconfigured
    deployment authenticated anyone holding any pool-signed token. It must
    refuse to authenticate at all instead."""
    _cognito_settings.cognito_app_client_id = ""
    token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.status_code == 401


def test_auth_error_detail_does_not_leak_library_internals(cognito_test_keys):
    """L9: the old handler returned f"Invalid token: {e}" — the raw jose
    exception text — straight to the caller."""
    token = sign_test_token(cognito_test_keys["private_pem"],
                            {"custom:tenant_id": "t-1", "aud": "wrong"})
    with pytest.raises(HTTPException) as exc:
        dashboard_auth(authorization=f"Bearer {token}", jwks=cognito_test_keys["jwks"])
    assert exc.value.detail == "Invalid token"
