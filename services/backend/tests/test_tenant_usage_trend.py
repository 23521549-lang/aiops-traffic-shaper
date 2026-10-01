"""The one lever this console has, given something to aim at.

Suspend is the operator's only action. A single day's count cannot tell a
tenant that has always been busy apart from one that started flooding an hour
ago, and those two call for opposite decisions.

The counters have been written on every accepted batch since the per-tenant
quota shipped, and `UsageCountersTable.get_many` was built in Phase 0 to read
several rows in one call. Neither had ever been read for this, and the tenant
table printed a bare count on the one screen whose own presenter module
defines `usage_share` to forbid exactly that.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    TenantsTable, UsageCountersTable, create_all_tables,
)
from services.backend.core.usage import (
    _tenant_counter_key, tenant_daily_quota, tenant_requests_over,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

TODAY = "2026-09-24"


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    return dynamo_resource


def _count(resource, tenant_id, date, n):
    UsageCountersTable(resource).update(
        key={"date": _tenant_counter_key(tenant_id, date)},
        update_expression="ADD total_requests :n", expr_values={":n": n})


def test_a_week_of_counts_comes_back_oldest_first(seeded):
    """A chart is read left to right and a trend has a direction."""
    _count(seeded, "t-1", "2026-09-22", 10)
    _count(seeded, "t-1", "2026-09-23", 20)
    _count(seeded, "t-1", TODAY, 30)

    week = tenant_requests_over(seeded, ["t-1"], days=7, today=TODAY)

    assert week["t-1"][-3:] == [10, 20, 30]
    assert len(week["t-1"]) == 7


def test_a_day_with_no_row_is_a_zero_and_not_a_gap(seeded):
    """Unlike telemetry, a missing day here genuinely means no requests: the
    counter is written on every accepted batch, so its absence is itself a
    measurement."""
    _count(seeded, "t-1", TODAY, 5)

    week = tenant_requests_over(seeded, ["t-1"], days=7, today=TODAY)

    assert week["t-1"] == [0, 0, 0, 0, 0, 0, 5]


def test_a_tenant_that_has_never_sent_anything_is_a_week_of_zeroes(seeded):
    """Not an empty list and not a missing key: the caller draws a row per
    tenant and a shorter list would silently misalign the days."""
    week = tenant_requests_over(seeded, ["t-new"], days=7, today=TODAY)

    assert week["t-new"] == [0] * 7


def test_several_tenants_come_back_in_one_call(seeded, monkeypatch):
    """One GetItem per tenant per day is seventy round trips for ten tenants
    on a page load. get_many exists for this and had no caller."""
    calls = []
    real = UsageCountersTable.get_many

    def counting(self, keys):
        calls.append(len(keys))
        return real(self, keys)

    monkeypatch.setattr(UsageCountersTable, "get_many", counting)
    _count(seeded, "t-1", TODAY, 5)
    _count(seeded, "t-2", TODAY, 9)

    week = tenant_requests_over(seeded, ["t-1", "t-2"], days=7, today=TODAY)

    assert week["t-1"][-1] == 5
    assert week["t-2"][-1] == 9
    assert calls == [14]


def test_the_batch_is_bounded_rather_than_silently_truncated(seeded):
    """BatchGetItem takes 100 keys. Seven days times fifteen tenants crosses
    it, and a page that quietly showed the first fourteen would be a billing
    screen lying by omission."""
    many = [f"t-{i}" for i in range(20)]

    with pytest.raises(ValueError):
        tenant_requests_over(seeded, many, days=7, today=TODAY)


def test_a_smaller_window_allows_more_tenants(seeded):
    """The bound is on keys, not on tenants, so the caller has a real choice
    rather than a hard tenant limit."""
    many = [f"t-{i}" for i in range(20)]

    week = tenant_requests_over(seeded, many, days=1, today=TODAY)

    assert len(week) == 20


# --- on the screen ---------------------------------------------------------


@pytest.fixture
def client(seeded, cognito_test_keys):
    TenantsTable(seeded).put(tenant_id="t-busy", name="Busy", status="active",
                             created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"cognito:groups": ["admin"], "email": "ops@example.com"})})
    return c


def test_the_tenant_row_states_the_denominator(client, seeded):
    """presenters.usage_share exists for this and its docstring reads "Never
    a bare number". The screen that ignored it is the screen that module was
    written for."""
    page = client.get("/admin/ui/tenants").text

    assert f"{tenant_daily_quota():,}" in page


def test_the_row_shows_a_share_and_not_only_a_count(client, seeded):
    page = client.get("/admin/ui/tenants").text

    assert "%" in page


def test_the_row_shows_the_week_behind_today(client, seeded):
    """The distinction the lever needs: a tenant at 60% that has been at 60%
    all week is a customer, and one at 60% that was at 2% yesterday is an
    incident."""
    page = client.get("/admin/ui/tenants").text

    assert "c-spark" in page


def test_the_trend_is_drawn_without_an_inline_style(client, seeded):
    """CSP is style-src 'self', which blocks the style attribute and not
    only style blocks. SVG geometry attributes are not CSS and are fine."""
    page = client.get("/admin/ui/tenants").text

    assert "style=" not in page


def test_the_operations_console_draws_no_sigma_axis(client, seeded):
    """Not a style choice. z is normalised against each tenant's own
    training distribution, so no cross-tenant sigma exists to draw, and
    drawing one would need N queries on a page that is already a Scan plus
    two GSIs."""
    page = client.get("/admin/ui/tenants").text

    assert "c-axis" not in page
    assert "sigma" not in page.lower()
