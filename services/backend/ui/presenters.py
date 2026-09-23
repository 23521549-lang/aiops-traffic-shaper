"""Turning stored facts into things a human can act on.

Pure functions, no I/O, so the wording is testable — and the wording here
carries real weight. A customer decides whether to override a security
decision based on what this module produces.
"""
from datetime import datetime, timezone

from services.backend.ml.model import TIER1_Z, TIER2_Z

# The bands, in standard deviations below the tenant's own normal. TIER1_Z
# (-4.0) is where the product starts acting, so nothing milder than that
# reaches this table at all.
_SEVERITY_BANDS = [
    (-6.0, "Far outside your normal"),
    (TIER2_Z, "Well outside your normal"),   # -5.0
    (-4.5, "Clearly outside your normal"),
    (TIER1_Z, "Outside your normal"),        # -4.0
]

NOT_MEASURABLE = "Unusual · not measurable"


def severity_phrase(z: float | None) -> str:
    """Lead with the comparison, not the number.

    ADR-006 made the product tier on standard deviations, but "-5.3" is not
    a sentence a non-statistician can act on, and the raw anomaly score it
    replaced was worse than useless — the same attack scored -0.204 and
    -0.092 against two models of the same tenant.

    `None` is a real case, not a defensive branch: when a model has no
    usable spread the tiering falls back to absolute thresholds and there is
    no z to report. Saying so is more honest than manufacturing one.
    """
    if z is None:
        return NOT_MEASURABLE
    for threshold, phrase in _SEVERITY_BANDS:
        if z <= threshold:
            return phrase
    return "Outside your normal"


def severity_detail(z: float | None) -> str:
    """The phrase with the figure appended, for the row the operator reads."""
    if z is None:
        return NOT_MEASURABLE
    return f"{severity_phrase(z)} · {abs(z):.1f}σ"


def tier_label(tier: int) -> str:
    """"Hard block" invited the question "what is a soft block?", and
    "rate limit" named the mechanism rather than the outcome. The customer
    wants to know what happened to the traffic."""
    return "Blocked" if int(tier) >= 2 else "Slowed"


def tier_css(tier: int) -> str:
    return "badge-blocked" if int(tier) >= 2 else "badge-slowed"


def relative_expiry(expires_at: int, now: datetime | None = None) -> str:
    """"Ends in 4 min", not "2026-09-23 15:12 UTC".

    A tier-1 rate limit lives for 300 seconds. Printing an absolute UTC
    timestamp to minute precision asked the reader to convert a timezone and
    do subtraction in order to answer "is this still happening?" — and since
    the page never refreshed, the answer they computed was usually wrong.
    """
    if not expires_at:
        return "—"
    now = now or datetime.now(timezone.utc)
    remaining = int(expires_at - now.timestamp())
    if remaining <= 0:
        return "Ended"
    if remaining < 60:
        return "Ends in under a minute"
    minutes = remaining // 60
    if minutes < 60:
        return f"Ends in {minutes} min"
    hours = minutes / 60
    return f"Ends in {hours:.0f} h" if hours >= 2 else "Ends in about an hour"


def absolute_expiry(expires_at: int) -> str:
    """Kept for the title attribute, so the exact moment is still available
    on hover and to anyone pasting it into a ticket."""
    if not expires_at:
        return ""
    return datetime.fromtimestamp(expires_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# --- is anyone actually watching? ---------------------------------------

NEVER_CONNECTED = "never"
DEGRADED = "degraded"
HEALTHY = "healthy"


def agent_health(agents: list[dict], now: datetime | None = None,
                 stale_after_seconds: int = 300) -> dict:
    """Which of three completely different kinds of "nothing" the dashboard
    is showing.

    The empty mitigation table used to render a green tick and "your traffic
    looks clean" whenever the list was empty. A tenant whose agent died
    three days ago saw a screen identical, pixel for pixel, to a tenant who
    was genuinely safe. That is bad news wearing good news' clothes, and it
    is the most damaging thing a security product can do with a blank
    screen.
    """
    now = now or datetime.now(timezone.utc)
    if not agents:
        return {"state": NEVER_CONNECTED, "reporting": 0, "last_seen": None,
                "last_seen_label": None}

    seen = []
    for a in agents:
        raw = a.get("last_seen_at")
        if not raw:
            continue
        try:
            seen.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not seen:
        return {"state": NEVER_CONNECTED, "reporting": 0, "last_seen": None,
                "last_seen_label": None}

    latest = max(seen)
    age = (now - latest).total_seconds()
    reporting = sum(1 for s in seen if (now - s).total_seconds() <= stale_after_seconds)
    return {
        "state": HEALTHY if age <= stale_after_seconds else DEGRADED,
        "reporting": reporting,
        "total": len(agents),
        "last_seen": latest,
        "last_seen_label": humanise_age(age),
    }


def humanise_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{max(seconds, 1)} seconds ago"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def usage_share(used: int, ceiling: int) -> dict:
    """Never a bare number. "0 requests today" against an unstated ceiling
    of 33,333 tells the operator nothing about whether to worry."""
    pct = (used / ceiling * 100) if ceiling else 0.0
    return {"used": used, "ceiling": ceiling, "pct": pct,
            "pct_label": f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"}
