"""The table that makes the product able to remember anything.

Until now it could not. `MitigationState` is keyed `(tenant_id, ip)`, so a
second decision on the same IP overwrites the first; TTL then deletes what
survives; whitelisting deletes it too; and `query_active` hides whatever is
left once it expires. Four independent mechanisms erasing the same fact.

The consequence is not cosmetic. PRD US-6's second acceptance criterion —
"view recent mitigation history" — has been unmeetable against the schema
since the schema was written, and nothing flagged it. And a product that
forgets everything it has done cannot draw a trend, cannot show a
before/after, and cannot say "we stopped 412 things for you this week",
which is the only sentence that justifies a security product's existence.

One table, three item types distinguished by a sort-key prefix:

    mit#{hour:010d}#{ip}   one mitigation EPISODE per (ip, hour)
    agg#{hour:010d}        one traffic rollup per hour
    read#                  the unread marker, one per tenant

Episodes are hourly, not per-decision, and that is the load-bearing choice.
`MitigationState.put` fires on every scoring pass for an IP that is already
blocked, and the agent flushes every 5 seconds, so per-decision rows would
be up to 720 per attacking IP per hour. Hourly is both far cheaper and the
better artefact: "203.0.113.7, 14:00-15:00, 17 decisions, escalated to a
block" is what a person wants to read.

Sort keys zero-pad the epoch to 10 digits so lexicographic order equals
chronological order — unpadded epochs sort wrong the moment the digit count
changes.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

HOUR = 3600
H1 = 1_790_000_000 // HOUR * HOUR
H2 = H1 + HOUR
H3 = H1 + 2 * HOUR


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


# --- episodes ------------------------------------------------------------

def test_repeated_decisions_in_one_hour_are_one_episode(history):
    """The whole reason the table is keyed this way. Seventeen scoring
    passes against one IP inside one hour is one thing that happened, not
    seventeen."""
    for _ in range(17):
        history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                                now=H1 + 60, score=-0.12, z=-4.3)

    rows = history.query_episodes("t-1", H1, H1 + HOUR)
    assert len(rows) == 1
    assert rows[0]["ip"] == "203.0.113.7"
    assert int(rows[0]["tier1_count"]) == 17


def test_an_escalation_is_visible_within_the_episode(history):
    """Per-tier counters rather than a single max_tier: strictly more
    information for the same single write, and DynamoDB has no MAX in an
    update expression anyway."""
    for _ in range(12):
        history.record_decision("t-1", "198.51.100.7", hour_start=H1, tier=1,
                                now=H1 + 10, score=-0.11, z=-4.2)
    for _ in range(3):
        history.record_decision("t-1", "198.51.100.7", hour_start=H1, tier=2,
                                now=H1 + 900, score=-0.21, z=-5.8)

    ep = history.query_episodes("t-1", H1, H1 + HOUR)[0]
    assert int(ep["tier1_count"]) == 12
    assert int(ep["tier2_count"]) == 3
    assert history.max_tier(ep) == 2


def test_the_episode_remembers_when_it_started_and_last_fired(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 30, score=-0.12, z=-4.3)
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 2400, score=-0.13, z=-4.4)

    ep = history.query_episodes("t-1", H1, H1 + HOUR)[0]
    assert int(ep["first_ts"]) == H1 + 30
    assert int(ep["last_ts"]) == H1 + 2400


def test_the_same_ip_in_a_later_hour_is_a_separate_episode(history):
    for hour in (H1, H2, H3):
        history.record_decision("t-1", "203.0.113.7", hour_start=hour, tier=1,
                                now=hour + 5, score=-0.12, z=-4.3)

    assert len(history.query_episodes("t-1", H1, H3 + HOUR)) == 3


def test_episodes_come_back_newest_first(history):
    for hour in (H1, H2, H3):
        history.record_decision("t-1", f"203.0.113.{hour % 100}", hour_start=hour,
                                tier=1, now=hour + 5, score=-0.12, z=-4.3)

    hours = [int(e["hour_start"]) for e in history.query_episodes("t-1", H1, H3 + HOUR)]
    assert hours == [H3, H2, H1]


def test_the_most_recent_hour_is_not_dropped(history):
    """The upper bound has to reach past the last hour's IP suffix. `#`
    sorts below the digits, so a naive "mit#{until}#" bound excludes every
    IP in the final hour — silently losing exactly the hour the customer
    opened the page to look at."""
    history.record_decision("t-1", "203.0.113.7", hour_start=H2, tier=2,
                            now=H2 + 10, score=-0.2, z=-5.5)

    assert len(history.query_episodes("t-1", H1, H2)) == 1


def test_an_unmeasurable_score_is_recorded_as_such(history):
    """z is None when the model had no usable spread — the same contract
    MitigationState.z carries. Recording 0.0 would be a lie about the
    decision that was taken."""
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 5, score=-0.15, z=None)

    ep = history.query_episodes("t-1", H1, H1 + HOUR)[0]
    assert ep.get("last_z") is None


def test_history_is_scoped_to_its_tenant(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 5, score=-0.12, z=-4.3)
    history.record_decision("t-2", "9.9.9.9", hour_start=H1, tier=1,
                            now=H1 + 5, score=-0.12, z=-4.3)

    ips = {e["ip"] for e in history.query_episodes("t-1", H1, H1 + HOUR)}
    assert ips == {"203.0.113.7"}


def test_episodes_expire_on_their_own(history):
    """30 days, keyed off hour_start so the value is constant for the item's
    whole life and a repeated SET is a no-op."""
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 5, score=-0.12, z=-4.3)

    ep = history.query_episodes("t-1", H1, H1 + HOUR)[0]
    assert int(ep["ttl"]) == H1 + TenantHistoryTable.RETENTION_SECONDS


# --- hourly rollup -------------------------------------------------------

def test_a_rollup_accumulates_across_batches(history):
    for _ in range(5):
        history.record_traffic("t-1", hour_start=H1, requests=100,
                               tier1=2, tier2=0)

    series = history.query_series("t-1", H1, H1 + HOUR)
    assert len(series) == 1
    assert int(series[0]["requests"]) == 500
    assert int(series[0]["batches"]) == 5
    assert int(series[0]["tier1_decisions"]) == 10


def test_the_series_comes_back_oldest_first(history):
    """A chart reads left to right. Episodes read newest first; a series
    does not."""
    for hour in (H3, H1, H2):
        history.record_traffic("t-1", hour_start=hour, requests=10, tier1=0, tier2=0)

    hours = [int(p["hour_start"]) for p in history.query_series("t-1", H1, H3 + HOUR)]
    assert hours == [H1, H2, H3]


def test_hours_with_no_traffic_are_filled_with_zeroes(history):
    """DynamoDB has no row for an hour nothing happened. A chart that skips
    those hours draws a flat line across an outage instead of a hole."""
    history.record_traffic("t-1", hour_start=H1, requests=10, tier1=0, tier2=0)
    history.record_traffic("t-1", hour_start=H3, requests=20, tier1=1, tier2=0)

    # `until_ts` is inclusive of the hour it falls in, so H1..H3 is three
    # hours. Passing H3 + HOUR would correctly yield a fourth, empty one.
    series = history.query_series("t-1", H1, H3, fill=True)
    assert [int(p["hour_start"]) for p in series] == [H1, H2, H3]
    assert int(series[1]["requests"]) == 0


def test_the_fill_range_covers_every_hour_asked_for(history):
    """Pinning the boundary the test above got wrong the first time: the
    window is inclusive of the hour `until_ts` falls in, so asking past the
    last data point yields empty hours rather than silently stopping."""
    history.record_traffic("t-1", hour_start=H1, requests=10, tier1=0, tier2=0)

    series = history.query_series("t-1", H1, H3 + HOUR, fill=True)
    assert [int(p["hour_start"]) for p in series] == [H1, H2, H3, H3 + HOUR]
    assert [int(p["requests"]) for p in series] == [10, 0, 0, 0]


def test_rollups_do_not_mix_with_episodes(history):
    """Both live in one table under different sort-key prefixes; a range
    that catches the wrong prefix would silently corrupt a chart."""
    history.record_decision("t-1", "203.0.113.7", hour_start=H1, tier=1,
                            now=H1 + 5, score=-0.12, z=-4.3)
    history.record_traffic("t-1", hour_start=H1, requests=10, tier1=1, tier2=0)

    assert len(history.query_episodes("t-1", H1, H1 + HOUR)) == 1
    assert len(history.query_series("t-1", H1, H1 + HOUR)) == 1


# --- unread marker -------------------------------------------------------

def test_everything_is_unread_until_it_is_marked(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=H2, tier=1,
                            now=H2 + 5, score=-0.12, z=-4.3)

    assert history.unread_since("t-1", default_ts=H1) == H1


def test_marking_read_moves_the_line_forward(history):
    history.mark_read("t-1", H2)
    assert history.unread_since("t-1", default_ts=H1) == H2


def test_marking_read_never_moves_backwards(history):
    """Two tabs, or a retried request. Going backwards would re-announce
    things the customer has already seen."""
    history.mark_read("t-1", H3)
    history.mark_read("t-1", H1)
    assert history.unread_since("t-1", default_ts=0) == H3


def test_a_first_visit_does_not_present_a_month_as_new(history):
    """Absent marker means the tenant has never looked. Treating that as
    epoch 0 would flag 30 days of history as unread on the debut of the
    notification system — a system crying wolf the first time it speaks."""
    for hour in (H1, H2, H3):
        history.record_decision("t-1", "203.0.113.7", hour_start=hour, tier=1,
                                now=hour + 5, score=-0.12, z=-4.3)

    since = history.unread_since("t-1", default_ts=H3)
    unread = [e for e in history.query_episodes("t-1", H1, H3 + HOUR)
              if int(e["hour_start"]) >= since]
    assert len(unread) == 1


def test_two_audited_actions_in_the_same_second_are_two_rows_in_order(dynamo_resource):
    """The settings ledger claims to be append-only. Its sort key was the
    timestamp at one-second resolution, so two changes inside one second were
    one PutItem overwriting the other - and the pair this most affects is a
    whitelist add followed immediately by its removal, which is exactly the
    sequence a dispute turns on.

    Separating them is only half of it. They have to come back the way they
    happened: "allowed, then removed" and "removed, then allowed" are
    different events.
    """
    create_all_tables(dynamo_resource)
    table = TenantHistoryTable(dynamo_resource)
    now = 1_700_000_000

    # Twenty changes inside the SAME second, milliseconds apart, which is
    # what a person clicking through a list of sources actually produces.
    for i in range(20):
        table.record_setting("t-1", actor="ops@example.com", what=f"step-{i:02d}",
                             old=None, new=str(i), now=now + i * 0.001)

    rows = table.query_settings("t-1", now - 5, now)

    assert [r["what"] for r in rows] == [f"step-{i:02d}" for i in range(20)]
    assert all(int(r["at"]) == now for r in rows)


def test_the_settings_ledger_still_cannot_see_the_other_two_prefixes(dynamo_resource):
    """`agg#` < `mit#` < `set#`, and the widened upper bound must not have
    reached past the prefix into anything else."""
    create_all_tables(dynamo_resource)
    table = TenantHistoryTable(dynamo_resource)
    now = 1_700_000_000
    hour = TenantHistoryTable.hour_of(now)

    table.record_traffic("t-1", hour, requests=5)
    table.record_decision("t-1", ip="10.0.0.1", tier=1, score=-0.2, z=-4.4,
                          hour_start=hour, now=now)
    table.record_setting("t-1", actor="ops@example.com", what="tier1_z",
                         old=-4.0, new=-4.5, now=now)

    rows = table.query_settings("t-1", now - 86_400, now + 86_400)

    assert [r["what"] for r in rows] == ["tier1_z"]
