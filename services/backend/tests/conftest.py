import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import json

import jwt
from jwt.algorithms import RSAAlgorithm
from moto import mock_aws


@pytest.fixture
def dynamo_resource():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="ap-southeast-1")


TEST_REGION = "ap-southeast-1"
TEST_POOL_ID = "ap-southeast-1_testpool"
TEST_CLIENT_ID = "test-client-id"
TEST_ISSUER = f"https://cognito-idp.{TEST_REGION}.amazonaws.com/{TEST_POOL_ID}"


@pytest.fixture(autouse=True)
def _cognito_settings():
    """Phase 4 (H1): auth now fails closed when the Cognito pool/app-client
    are unconfigured, so tests must run against a CONFIGURED deployment —
    otherwise every auth test would pass for the wrong reason. The one test
    that checks fail-closed behaviour blanks these deliberately."""
    from services.backend.core.config import settings
    saved = (settings.cognito_user_pool_id, settings.cognito_region,
             settings.cognito_app_client_id)
    settings.cognito_user_pool_id = TEST_POOL_ID
    settings.cognito_region = TEST_REGION
    settings.cognito_app_client_id = TEST_CLIENT_ID
    yield settings
    (settings.cognito_user_pool_id, settings.cognito_region,
     settings.cognito_app_client_id) = saved


@pytest.fixture
def cognito_test_keys():
    """A real, locally generated RSA keypair + JWKS — not a stub. Tests
    override `get_jwks` (services/backend/api/cognito_auth.py) with the
    public half here so `jwt.decode(...)` inside `_decode_and_verify` runs
    its real signature-checking logic against a real signed token, instead
    of mocking verification away and only testing the code around it."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict["kid"] = "test-key-1"
    jwk_dict["alg"] = "RS256"
    return {"private_pem": private_pem, "jwks": {"keys": [jwk_dict]}}


def sign_test_token(private_pem: bytes, claims: dict) -> str:
    """Defaults mirror what a real Cognito ID token always carries
    (token_use/aud/iss). Callers override any of them explicitly to build the
    negative cases — an access token, a foreign app client, a foreign pool."""
    full_claims = {"token_use": "id", "aud": TEST_CLIENT_ID, "iss": TEST_ISSUER, **claims}
    return jwt.encode(full_claims, private_pem, algorithm="RS256", headers={"kid": "test-key-1"})


@pytest.fixture(autouse=True)
def _clear_fastapi_dependency_overrides():
    """`services.backend.main.app` is a module-level singleton shared by
    every test that imports it. A test that sets
    `app.dependency_overrides[get_dynamo_resource] = ...` (the correct way
    to inject a moto-backed resource per docs/PLAN.md Stage 4) would leak
    that override into every later test otherwise — this fixture resets it
    after each test regardless of which test file runs."""
    yield
    try:
        from services.backend.main import app
        app.dependency_overrides.clear()
    except ImportError:
        pass  # main.py doesn't exist yet in earlier stages' test runs
