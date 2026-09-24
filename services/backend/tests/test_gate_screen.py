"""The screen a customer opens, and the one job it has to finish.

"Is anything broken right now, and is it me or you" is the most frequent
question this product is asked, and answering it took three pages: Protection
knew agent health, Agents knew the fleet, Model knew whether anything was
enforcing. The user assembled the answer themselves.

It is now one page, and the quiet case is the PRIMARY case rather than an
empty state: a quiet screen carries a live count and a worst-observed sigma,
and if either cannot be computed it is not a quiet screen, it is a dead one
wearing a quiet screen's clothes.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    UsageCountersTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


def _hour_now() -> int:
    return TenantHistoryTable.hour_of(int(datetime.now(timezone.utc).timestamp()))


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _live_agent(resource, label="web-01"):
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(resource).put(tenant_id="t-1", agent_id="a-1",
                              agent_label=label, registered_at=now,
                              last_seen_at=now, agent_version="1.4.0",
                              api_key_hash="h", status="active")


def _blocked(resource, ip="203.0.113.7", tenant="t-1"):
    MitigationStateTable(resource).put(
        tenant_id=tenant, ip=ip, tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)


def test_the_axis_is_on_the_page(client, dynamo_resource):
    _live_agent(dynamo_resource)

    assert "c-axis" in client.get("/dashboard/ui").text


def test_a_dead_agent_draws_no_marks_at_all(client, dynamo_resource):
    """The rule, asserted on rendered markup: no reading on screen, not a
    reading with a warning beside it."""
    _blocked(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert "c-mark" not in page
    assert 'data-plot="outline"' in page


def test_a_fed_axis_does_draw_its_marks(client, dynamo_resource):
    _live_agent(dynamo_resource)
    _blocked(dynamo_resource)

    assert "c-mark" in client.get("/dashboard/ui").text


def test_a_dead_agent_and_a_quiet_one_do_not_look_the_same(client, dynamo_resource):
    """The most damaging thing a security product can do with a blank
    screen."""
    dead = client.get("/dashboard/ui").text
    _live_agent(dynamo_resource)
    quiet = client.get("/dashboard/ui").text

    assert 'data-plot="outline"' in dead
    assert 'data-plot="live"' in quiet


def test_the_density_below_the_gate_is_drawn(client, dynamo_resource):
    _live_agent(dynamo_resource)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", _hour_now(), requests=10, bins={"n300": 40, "n350": 12})

    assert "c-density" in client.get("/dashboard/ui").text


def test_the_page_costs_the_same_bytes_for_a_huge_tenant(client, dynamo_resource):
    """Density is why. Ten thousand sources below the gate must not be ten
    thousand elements."""
    _live_agent(dynamo_resource)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", _hour_now(), requests=10, bins={"n300": 10_000})

    page = client.get("/dashboard/ui").text

    assert page.count("c-density") <= 13


def test_a_throttled_tenant_is_not_told_to_restart_its_agent(client, dynamo_resource):
    """Today this case renders as a dead agent and sends the customer to fix
    a healthy process at 3am."""
    from services.backend.core.usage import (
        _tenant_counter_key, _today, tenant_daily_quota,
    )

    _live_agent(dynamo_resource)
    UsageCountersTable(dynamo_resource).put(
        date=_tenant_counter_key("t-1", _today()),
        total_requests=tenant_daily_quota() + 1)

    page = client.get("/dashboard/ui").text

    assert "will not help" in page
    assert 'data-plot="frozen"' in page


def test_the_axis_carries_a_paired_table(client, dynamo_resource):
    """Position on an axis is unreadable to a screen reader. The table is a
    summary, not a matrix."""
    _live_agent(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert "sr-only" in page
    assert "Region" in page


def test_no_other_tenants_address_reaches_the_page(client, dynamo_resource):
    _live_agent(dynamo_resource)
    _blocked(dynamo_resource, ip="198.51.100.9", tenant="t-2")

    assert "198.51.100.9" not in client.get("/dashboard/ui").text


def test_the_old_strip_is_gone_from_both_surfaces(client, dynamo_resource):
    """The axis supersedes it. Leaving both would mean two things drawing
    the same scale from two code paths, which is how they drift."""
    _live_agent(dynamo_resource)

    assert "sigma-bands" not in client.get("/dashboard/ui").text
    assert "sigma-bands" not in client.get("/").text
