"""A gauge that shows a constant is a lie with a number on it.

`record_invocation` has one caller and it has never passed
`estimated_gb_seconds`, so the operations overview has displayed 0.00 since
the day it shipped.

`dynamodb_consumed_rcu` and `dynamodb_consumed_wcu` are worse: they are
declared on `UsageReport`, served by `GET /admin/v1/usage`, and nothing in
this codebase writes them anywhere. A permanent zero in an API response is a
lie to a machine as well as to a person.

Principle 1.4 gives two options and no third. GB-seconds is measurable with
nothing new - the middleware already wraps every request and Lambda publishes
its memory size - so it is measured. Consumed capacity would need
`ReturnConsumedCapacity` on every call and an aggregation write per request,
on the very write budget the gauge exists to protect, so it goes.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    TenantsTable, UsageCountersTable, create_all_tables,
)
from services.backend.core.usage import (
    get_usage_report, lambda_memory_gb, record_invocation,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    return dynamo_resource


def test_the_figure_scales_with_the_configured_memory(monkeypatch):
    """GB-seconds is memory times duration. A function configured at 512MB
    bills half of what one at 1024MB bills for the same wall time."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1024")
    assert lambda_memory_gb() == 1.0

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "512")
    assert lambda_memory_gb() == 0.5


def test_an_unset_memory_size_falls_back_to_the_deployed_value(monkeypatch):
    """Locally there is no Lambda environment. The fallback is the size this
    product is actually deployed at, so a figure read on a laptop is in the
    same units as one read in production."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", raising=False)

    assert lambda_memory_gb() > 0


def test_an_unreadable_memory_size_does_not_crash_a_request(monkeypatch):
    """This runs inside the middleware on every authenticated request. A
    malformed environment variable must not turn metering into a 500."""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "not a number")

    assert lambda_memory_gb() > 0


def test_a_recorded_invocation_carries_the_work_it_did(seeded):
    record_invocation(seeded, estimated_gb_seconds=0.25)

    assert get_usage_report(seeded, None).estimated_gb_seconds == 0.25


def test_the_recorded_total_accumulates_across_requests(seeded):
    record_invocation(seeded, estimated_gb_seconds=0.25)
    record_invocation(seeded, estimated_gb_seconds=0.75)

    assert get_usage_report(seeded, None).estimated_gb_seconds == 1.0


# The probe has to be an AUTHENTICATED request that matched a route, because
# that is the only thing metering is defined over now. `/no-such-path` used to
# serve here, on the basis that any miss which was not "/" got metered - and
# that was the defect: a 404 for a path no route claims never reached the
# application, so an anonymous caller could spend the day's write budget on
# junk URLs. Unmatched requests are unmetered, and this probe has to earn its
# write the way a real one does.
METERED = "/dashboard/ui"


@pytest.fixture
def metered_client(seeded, cognito_test_keys):
    """A signed-in tenant. Every GET through this client is metered work."""
    TenantsTable(seeded).put(tenant_id="t-1", name="Acme", status="active",
                             created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return client


def test_a_real_request_records_a_figure_that_is_not_zero(seeded, metered_client):
    """The end that was broken. Every piece above can be right and the
    middleware can still call record_invocation with its default, which is
    exactly what it did."""
    metered_client.get(METERED)

    assert get_usage_report(seeded, None).estimated_gb_seconds > 0


def test_the_figure_accumulates_rather_than_being_set(seeded, metered_client):
    """Two requests bill more than one. An assignment where an ADD belongs
    would show the last request's cost as the day's total."""
    metered_client.get(METERED)
    one = get_usage_report(seeded, None).estimated_gb_seconds
    metered_client.get(METERED)
    two = get_usage_report(seeded, None).estimated_gb_seconds

    assert two > one


def test_the_figure_is_a_measurement_and_not_a_placeholder(seeded, metered_client):
    """A real elapsed time on a local request is small and is not zero. A
    figure of exactly 1.0, or one larger than any plausible request, would
    mean something is being stood in for rather than timed."""

    metered_client.get(METERED)

    billed = get_usage_report(seeded, None).estimated_gb_seconds

    assert 0 < billed < lambda_memory_gb() * 30


def test_consumed_capacity_is_gone_from_the_report(seeded):
    """Removed rather than left at zero. It was in a public API response,
    where a permanent zero is a lie to a machine as well as a person."""
    report = get_usage_report(seeded, None)

    assert not hasattr(report, "dynamodb_consumed_rcu")
    assert not hasattr(report, "dynamodb_consumed_wcu")


def test_consumed_capacity_is_gone_from_the_screen():
    from pathlib import Path

    ui = Path(__file__).resolve().parents[1] / "ui"
    for path in ui.rglob("*.html"):
        text = path.read_text(encoding="utf-8").lower()
        assert "consumed_rcu" not in text, path
        assert "consumed_wcu" not in text, path


def test_nothing_is_left_writing_a_field_nobody_reads(seeded):
    """The counter row must not keep accumulating an attribute no screen and
    no API can show."""
    record_invocation(seeded, estimated_gb_seconds=0.25)
    from services.backend.core.usage import _today

    item = UsageCountersTable(seeded).get(date=_today()) or {}

    assert "dynamodb_consumed_rcu" not in item


def test_a_path_no_route_claims_is_not_metered(seeded):
    """The whole point of the prefix list, generalised.

    `/ui/static/../main.py` is normalised to `/ui/main.py` before the
    middleware sees it, so it escaped every entry in
    `_UNMETERED_PATH_PREFIXES` and billed a DynamoDB write for a 404. The
    Lambda Function URL is public and has no edge rate limiting, so that is a
    lever an anonymous caller can pull as fast as they can open sockets, on
    the exact write budget this product is built around.

    It also made the suite depend on ambient AWS credentials: with no
    dependency override, the middleware reached for the real resource, which
    passed on a developer machine holding credentials and failed in CI with
    NoCredentialsError. A test that needs the developer's AWS account to pass
    is not testing what it claims to.
    """
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    client = TestClient(app, base_url="https://testserver")

    before = get_usage_report(seeded, None).total_requests
    assert client.get("/ui/static/../main.py").status_code == 404
    assert client.get("/no-such-path").status_code == 404

    assert get_usage_report(seeded, None).total_requests == before
