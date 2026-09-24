"""A tenant inside its own quota can still be unprotected.

`enforce_usage_ceiling` refuses telemetry PLATFORM-WIDE at the global
ceiling, so a tenant well inside its 25% share stops being measured because
someone else filled the day. Today the agent sees a 429 and the console says
nothing, so the screen and the agent disagree about whether the customer is
protected - which is the single worst thing a security product can do.

Two keys on one table, so one BatchGetItem: ~1 RCU, one round trip.
"""
import pytest

from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import (
    _DAILY_REQUEST_CEILING, _today, protection_status, record_tenant_ingest,
    tenant_daily_quota,
)


@pytest.fixture
def resource(dynamo_resource):
    create_all_tables(dynamo_resource)
    return dynamo_resource


def _fill_the_day(resource, requests: int) -> None:
    """Seed the global counter directly, the way test_usage_throttle.py does.
    `add_invocation` takes no count, and calling it a million times is not a
    test, it is a wait."""
    UsageCountersTable(resource).put(
        date=_today(), total_requests=requests, estimated_gb_seconds=0.0)


def test_a_quiet_day_is_not_throttled(resource):
    status = protection_status(resource, "t-1")

    assert status["throttled"] is False
    assert status["throttled_reason"] is None


def test_the_tenants_own_share_is_reported_with_its_denominator(resource):
    """Never a bare number - a count with no denominator cannot tell anyone
    whether to worry, which is the rule presenters.py already sets and the
    publisher's tenant table already breaks."""
    record_tenant_ingest(resource, "t-1", count=100)

    status = protection_status(resource, "t-1")

    assert status["tenant_used"] == 100
    assert status["tenant_ceiling"] == tenant_daily_quota()


def test_the_global_ceiling_throttles_a_tenant_inside_its_own_share(resource):
    """The case nobody has designed for, and the one that makes a customer
    unprotected through no fault of their own."""
    # +1 because the ceiling is 1_000_000/30 = 33333.33, so int() of it is
    # still under. The comparison here is the same `>=` against the same
    # float that `is_over_ceiling` already uses, deliberately: the console
    # must say "throttled" at exactly the moment the agent is refused, not
    # one request either side of it.
    _fill_the_day(resource, int(_DAILY_REQUEST_CEILING) + 1)

    status = protection_status(resource, "t-1")

    assert status["throttled"] is True
    assert status["throttled_reason"] == "global"


def test_a_tenant_over_its_own_share_is_named_as_such(resource):
    """Two different sentences for the customer: one they can act on by
    sending less, one they cannot act on at all."""
    record_tenant_ingest(resource, "t-1", count=tenant_daily_quota() + 1)

    status = protection_status(resource, "t-1")

    assert status["throttled"] is True
    assert status["throttled_reason"] == "tenant"


def test_one_tenants_usage_is_not_anothers(resource):
    record_tenant_ingest(resource, "t-2", count=tenant_daily_quota() + 1)

    assert protection_status(resource, "t-1")["throttled"] is False


def test_both_keys_are_fetched_in_one_round_trip(resource, monkeypatch):
    """One BatchGetItem, not two GetItems. The table is provisioned at 2 RCU
    and this runs on every page."""
    calls = []
    original = UsageCountersTable.get_many

    def counting(self, keys):
        calls.append(tuple(keys))
        return original(self, keys)

    monkeypatch.setattr(UsageCountersTable, "get_many", counting)
    protection_status(resource, "t-1")

    assert len(calls) == 1
    assert len(calls[0]) == 2
