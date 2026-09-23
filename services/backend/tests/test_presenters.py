"""The wording is part of the product, so it gets tests.

A customer decides whether to override a security decision based on what
these functions produce. "5.3σ" is not that decision's input; "far outside
your normal, blocked, ends in 47 minutes" is.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.backend.ui.presenters import (
    DEGRADED,
    HEALTHY,
    NEVER_CONNECTED,
    NOT_MEASURABLE,
    agent_health,
    relative_expiry,
    severity_detail,
    severity_phrase,
    tier_label,
    usage_share,
)

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


# --- severity ------------------------------------------------------------

@pytest.mark.parametrize("z,expected", [
    (-7.0, "Far outside your normal"),
    (-6.0, "Far outside your normal"),
    (-5.4, "Well outside your normal"),
    (-5.0, "Well outside your normal"),
    (-4.7, "Clearly outside your normal"),
    (-4.2, "Outside your normal"),
    (-4.0, "Outside your normal"),
])
def test_severity_reads_as_a_comparison_not_a_number(z, expected):
    assert severity_phrase(z) == expected


def test_an_unmeasurable_score_says_so_rather_than_inventing_one():
    """score_std <= 0 makes tiering fall back to absolute thresholds, so
    there is genuinely no z. Printing "0.0σ" would be a lie about the
    decision that was actually taken."""
    assert severity_phrase(None) == NOT_MEASURABLE
    assert severity_detail(None) == NOT_MEASURABLE


def test_the_figure_is_kept_alongside_the_phrase():
    """Disclosure, not deletion — a support ticket needs the number."""
    assert severity_detail(-5.26) == "Well outside your normal · 5.3σ"


def test_the_attack_production_missed_reads_as_severe():
    """The real case from ADR-006: raw score -0.092 went undetected against
    an absolute threshold, and sits 5.26 deviations out."""
    assert severity_detail(-5.26).startswith("Well outside your normal")


def test_the_same_attack_against_a_different_model_reads_the_same():
    """The property the raw score did not have. -0.204 and -0.092 were the
    same attack; as sigma they are 5.9 and 5.3, and a customer comparing
    two rows now sees two comparable things."""
    assert severity_phrase(-5.9) == severity_phrase(-5.26)


def test_tier_labels_name_the_outcome_not_the_mechanism():
    assert tier_label(2) == "Blocked"
    assert tier_label(1) == "Slowed"


# --- expiry --------------------------------------------------------------

def test_expiry_is_relative_because_the_question_is_is_it_still_happening():
    assert relative_expiry(int((NOW + timedelta(minutes=47)).timestamp()), NOW) == "Ends in 47 min"
    assert relative_expiry(int((NOW + timedelta(seconds=30)).timestamp()), NOW) \
        == "Ends in under a minute"


def test_a_tier_one_rate_limit_is_legible_at_its_real_scale():
    """300 seconds. An absolute UTC timestamp to minute precision was being
    shown for something whose whole life is five minutes."""
    assert relative_expiry(int((NOW + timedelta(seconds=300)).timestamp()), NOW) == "Ends in 5 min"


def test_an_hour_long_block_does_not_read_as_sixty_minutes():
    assert relative_expiry(int((NOW + timedelta(minutes=58)).timestamp()), NOW) == "Ends in 58 min"
    assert relative_expiry(int((NOW + timedelta(minutes=75)).timestamp()), NOW) \
        == "Ends in about an hour"


def test_something_already_over_says_so():
    assert relative_expiry(int((NOW - timedelta(minutes=1)).timestamp()), NOW) == "Ended"


def test_no_expiry_renders_as_a_dash():
    assert relative_expiry(0, NOW) == "—"


# --- the three kinds of nothing ------------------------------------------

def test_no_agent_has_ever_connected():
    assert agent_health([], NOW)["state"] == NEVER_CONNECTED


def test_an_agent_reporting_now_is_healthy():
    health = agent_health([{"last_seen_at": (NOW - timedelta(seconds=14)).isoformat()}], NOW)
    assert health["state"] == HEALTHY
    assert health["reporting"] == 1
    assert health["last_seen_label"] == "14 seconds ago"


def test_an_agent_that_went_quiet_is_degraded_not_healthy():
    """The defect this exists for: an empty mitigation list plus a dead
    agent used to render as 'your traffic looks clean'."""
    health = agent_health([{"last_seen_at": (NOW - timedelta(hours=3)).isoformat()}], NOW)
    assert health["state"] == DEGRADED
    assert health["reporting"] == 0
    assert health["last_seen_label"] == "3 hours ago"


def test_one_live_agent_is_enough_to_be_watching():
    health = agent_health([
        {"last_seen_at": (NOW - timedelta(days=2)).isoformat()},
        {"last_seen_at": (NOW - timedelta(seconds=5)).isoformat()},
    ], NOW)
    assert health["state"] == HEALTHY
    assert health["reporting"] == 1
    assert health["total"] == 2


def test_an_unparseable_timestamp_is_not_treated_as_healthy():
    """Fail towards 'we cannot see', never towards reassurance."""
    assert agent_health([{"last_seen_at": "not-a-date"}], NOW)["state"] == NEVER_CONNECTED
    assert agent_health([{}], NOW)["state"] == NEVER_CONNECTED


def test_a_z_suffixed_timestamp_parses():
    """DynamoDB holds whatever was written; some rows use the Z suffix."""
    health = agent_health([{"last_seen_at": "2026-09-23T11:59:50Z"}], NOW)
    assert health["state"] == HEALTHY


# --- usage ---------------------------------------------------------------

def test_usage_always_carries_its_denominator():
    share = usage_share(used=0, ceiling=33333)
    assert share["ceiling"] == 33333
    assert share["pct_label"] == "0.0%"
    assert usage_share(used=26666, ceiling=33333)["pct_label"] == "80%"
