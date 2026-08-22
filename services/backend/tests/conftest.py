import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from moto import mock_aws


@pytest.fixture
def dynamo_resource():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="ap-southeast-1")


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
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk_dict = jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = "test-key-1"
    return {"private_pem": private_pem, "jwks": {"keys": [jwk_dict]}}


def sign_test_token(private_pem: bytes, claims: dict) -> str:
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test-key-1"})


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
