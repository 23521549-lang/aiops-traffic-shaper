import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def dynamo_resource():
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="ap-southeast-1")


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
