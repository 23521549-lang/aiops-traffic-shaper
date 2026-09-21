"""Phase 7: the readiness probe.

/health answers "the process is up". It has always answered 200 from a
deployment with no DynamoDB table behind it and no Cognito pool configured —
which is exactly the state a first deploy is in, and exactly the state
Phase 4's H1 finding made *silent*: auth fails closed when the pool is unset,
so such a deployment authenticates nobody while looking perfectly healthy.
These tests are named after that failure, not after the mechanism.
"""
from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _today
from services.backend.main import app


def _client(dynamo_resource):
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver")


def test_ready_when_tables_exist_and_cognito_is_configured(dynamo_resource):
    create_all_tables(dynamo_resource)
    resp = _client(dynamo_resource).get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready",
                           "checks": {"dynamodb": True, "cognito_config": True}}


def test_not_ready_when_the_tables_have_never_been_created(dynamo_resource):
    """create_all_tables() runs from application code, not from Terraform, so
    a deployment can be serving before any table exists."""
    resp = _client(dynamo_resource).get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["dynamodb"] is False


def test_not_ready_when_cognito_is_unconfigured(dynamo_resource, _cognito_settings):
    """Phase 4 / H1: without the pool id and app client id, authentication
    fails closed. The deployment is up, answers /health, and can serve no
    human at all. That must not read as healthy."""
    create_all_tables(dynamo_resource)
    _cognito_settings.cognito_user_pool_id = ""
    resp = _client(dynamo_resource).get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["cognito_config"] is False
    assert resp.json()["checks"]["dynamodb"] is True


def test_readiness_probe_does_not_consume_usage_quota(dynamo_resource):
    """A probe polled every few seconds must not spend write capacity, for the
    same reason /health does not (Phase 4 / M8)."""
    create_all_tables(dynamo_resource)
    client = _client(dynamo_resource)
    for _ in range(5):
        client.get("/ready")
    assert UsageCountersTable(dynamo_resource).get(date=_today()) is None


# --- Which version is actually serving? -----------------------------------

def test_health_reports_the_lambda_version_that_answered(dynamo_resource, monkeypatch):
    """Every incident starts with "what is running?", and since the `live`
    alias can be repointed in seconds for a rollback, the answer is no longer
    "whatever was last deployed". The Lambda runtime sets
    AWS_LAMBDA_FUNCTION_VERSION to the resolved version behind the alias -
    this is what makes a rollback observable from outside rather than taken
    on trust from `aws lambda get-alias`."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "7")
    body = _client(dynamo_resource).get("/health").json()
    assert body == {"status": "healthy", "version": "7"}


def test_health_outside_lambda_says_so_instead_of_inventing_a_version(dynamo_resource, monkeypatch):
    """Locally and in tests there is no Lambda version. Reporting a made-up
    one would be worse than reporting none."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_VERSION", raising=False)
    body = _client(dynamo_resource).get("/health").json()
    assert body == {"status": "healthy", "version": "local"}
