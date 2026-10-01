"""Three places where a value nobody bounded decides how much work happens.

All three were found by the pre-rebuild review, none of them is a UI issue,
and all three are the kind that look fine until the day they do not.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.backend.core.tables import (
    TelemetryEventsTable, TenantsTable, create_all_tables,
)


# --- flag_all_for_ip -----------------------------------------------------

def _buckets(resource, tenant_id, ip, n, start=1_000_000):
    table = TelemetryEventsTable(resource)
    for i in range(n):
        table.add_aggregate(tenant_id, ip, start + i * 5, {
            "request_count": 3, "error_count": 0, "post_count": 0,
            "total_bytes": 300, "total_time": 0.1,
            "distinct_uri_count": 1, "distinct_ua_count": 1,
        })


def test_flagging_an_ip_is_bounded(dynamo_resource):
    """`flag_all_for_ip` looped over EVERY bucket an IP has and issued one
    UpdateItem each, synchronously inside one HTTP request. TelemetryEvents
    retains 25 hours of 5-second buckets, so a continuously-active IP has up
    to ~18,000 of them — 18,000 base writes plus 18,000 GSI writes against a
    table provisioned at 5+5 WCU. That is an hour of throttled writes and a
    certain Lambda timeout.

    It has never fired because the only caller is an admin route reachable
    by curl. Putting a button on it — which the redesign intends to — turns
    a latent bug into a reachable one."""
    create_all_tables(dynamo_resource)
    _buckets(dynamo_resource, "t-1", "203.0.113.4", 40)

    flagged = TelemetryEventsTable(dynamo_resource).flag_all_for_ip(
        "t-1", "203.0.113.4", limit=10)

    assert flagged == 10


def test_flagging_reports_when_it_stopped_early(dynamo_resource):
    """Silently doing 10 of 40 is worse than doing 10 and saying so — the
    caller is trying to undo model poisoning and needs to know it is not
    finished."""
    create_all_tables(dynamo_resource)
    _buckets(dynamo_resource, "t-1", "203.0.113.4", 25)

    table = TelemetryEventsTable(dynamo_resource)
    assert table.flag_all_for_ip("t-1", "203.0.113.4", limit=10) == 10
    assert table.last_flag_truncated is True

    assert table.flag_all_for_ip("t-1", "203.0.113.4", limit=100) == 25
    assert table.last_flag_truncated is False


def test_the_newest_buckets_are_flagged_first(dynamo_resource):
    """When it does truncate, it must keep the RECENT buckets. Those are the
    ones poisoning the next retrain; the oldest age out of the 25-hour
    window on their own."""
    create_all_tables(dynamo_resource)
    _buckets(dynamo_resource, "t-1", "203.0.113.4", 20, start=1_000_000)

    table = TelemetryEventsTable(dynamo_resource)
    table.flag_all_for_ip("t-1", "203.0.113.4", limit=5)

    flagged = [int(b["bucket_start_ts"]) for b in
               table.query_buckets_for_ip("t-1", "203.0.113.4") if b.get("flagged")]
    assert flagged == sorted(flagged)[-5:]
    assert min(flagged) > 1_000_000 + 5 * 5


# --- telemetry batch size -----------------------------------------------

def test_a_telemetry_batch_is_capped(dynamo_resource):
    """`dependencies.py` states "batches carry up to 100 log lines" and uses
    it to justify amortising a read across them. Nothing enforced it:
    `TelemetryBatch.logs` was a bare `list[LogRecord]`. Each distinct IP in a
    batch fans out into its own DynamoDB write plus a GSI mirror, so an
    unbounded batch is an unbounded write amplification on the one path the
    product cannot afford to lose."""
    from services.backend.schemas.telemetry import TelemetryBatch

    assert TelemetryBatch(logs=[]).logs == []
    assert len(TelemetryBatch(logs=[_log() for _ in range(1000)]).logs) == 1000

    with pytest.raises(ValidationError) as exc:
        TelemetryBatch(logs=[_log() for _ in range(1001)])
    # Assert on the REASON. Written first with a wrong field name in the
    # helper, this test passed on a missing-field error instead - green, and
    # proving nothing about the cap it exists to prove.
    assert "too_long" in str(exc.value)


def _log():
    from services.backend.schemas.telemetry import LogRecord
    return LogRecord(remote_addr="203.0.113.4", time_iso8601="2026-09-23T12:00:00Z",
                     request_method="GET", request_uri="/", status=200,
                     body_bytes_sent=100, request_time=0.01, http_user_agent="curl")


# --- tenant status ------------------------------------------------------

def test_only_an_active_tenant_is_served(dynamo_resource):
    """`assert_tenant_active` was a deny-list: it refused exactly
    status == "suspended" and let every other value through. A tenant left
    half-created, or one whose status was mistyped, was served as though
    healthy. An allow-list fails closed, which is the right direction for a
    check whose whole job is refusing service."""
    from services.backend.api.dependencies import assert_tenant_active

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="ok", name="A", status="active",
                                      created_at="2026-09-23T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="half", name="B", status="provisioning",
                                      created_at="2026-09-23T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="typo", name="C", status="Active",
                                      created_at="2026-09-23T00:00:00Z")

    assert_tenant_active(dynamo_resource, "ok")

    for tenant_id in ("half", "typo", "missing"):
        with pytest.raises(HTTPException) as exc:
            assert_tenant_active(dynamo_resource, tenant_id)
        assert exc.value.status_code == 403
