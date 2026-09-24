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
        return "no expiry"
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
    """Two of these four branches used to ignore the plural, so "1 minutes
    ago" and "1 seconds ago" shipped on the agents table, the page header
    and the fleet view at once. One helper, every timestamp in the product.
    """
    seconds = int(seconds)
    if seconds < 60:
        return _count(max(seconds, 1), "second")
    if seconds < 3600:
        return _count(seconds // 60, "minute")
    if seconds < 86400:
        return _count(seconds // 3600, "hour")
    return _count(seconds // 86400, "day")


def _count(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def usage_share(used: int, ceiling: int) -> dict:
    """Never a bare number. "0 requests today" against an unstated ceiling
    of 33,333 tells the operator nothing about whether to worry."""
    pct = (used / ceiling * 100) if ceiling else 0.0
    return {"used": used, "ceiling": ceiling, "pct": pct,
            "pct_label": f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"}


def timestamp_pair(raw, now: datetime | None = None) -> dict:
    """Absolute for the ticket, relative for the glance.

    The Tenants table was rendering `2026-09-14T09:20:41.139654+00:00`, five
    times, microseconds included. Nothing about a microsecond offset answers
    a question a person has, and a raw machine timestamp on a customer-facing
    screen reads as "nobody looked at this". `humanise_age` was already in
    this file and already used on another page.

    The exact value survives in a title attribute, because someone pasting a
    row into a ticket does need it.
    """
    now = now or datetime.now(timezone.utc)
    if not raw:
        return {"label": "unknown", "relative": "", "exact": ""}
    try:
        dt = (datetime.fromtimestamp(int(raw), tz=timezone.utc)
              if isinstance(raw, (int, float))
              else datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except (ValueError, OSError):
        return {"label": "unknown", "relative": "", "exact": str(raw)}
    return {
        "label": dt.strftime("%d %b %Y"),
        "relative": humanise_age((now - dt).total_seconds()),
        "exact": dt.isoformat(timespec="seconds"),
    }


# --- why this source ------------------------------------------------------

# `unique_uri_ratio` is a column in a dataframe. "Distinct URLs" is something
# a person reading an incident at 3am can act on.
# The value stored on a mitigation, and what a customer should read. The
# detail pane printed `behavioral_anomaly` under "Reason", in a pane whose
# whole job is explaining an enforcement decision to a person. Every other
# machine name in this product is mapped to words before it is displayed.
REASON_LABELS = {
    "behavioral_anomaly": "Traffic unlike your baseline",
    "manual_block": "Blocked by hand",
    "whitelisted": "On your allowed list",
}


def reason_label(reason: str | None) -> str:
    """Unknown values are made readable rather than hidden.

    A reason this map has not met yet is still better shown than swallowed:
    an operator reading "Rate limit exceeded" can act on it, and an operator
    reading nothing at all cannot tell whether the field is empty or the
    page is broken.
    """
    if not reason:
        return "not recorded"
    return REASON_LABELS.get(reason) or reason.replace("_", " ").capitalize()


FEATURE_LABELS = {
    "request_rate": "Request rate",
    "error_ratio": "Error ratio",
    "avg_bytes_sent": "Average bytes",
    "avg_request_time": "Average time",
    "unique_uri_ratio": "Distinct URLs",
    "user_agent_entropy": "User-agent spread",
    "post_ratio": "POST ratio",
}

# How far out a single feature has to be before it is called a driver. Lower
# than the tiering threshold on purpose: the model fires on the combination,
# so the features that explain it are individually milder than the whole.
DRIVER_SIGMA = 2.5


def decompose(vector, means, stds) -> list[dict]:
    """Break one decision out across the seven features, each in standard
    deviations of THIS tenant's own baseline.

    This is the one thing no competitor can do. A rule engine's best answer
    is a rule id; an IP-reputation service's is an opinion about someone
    else's traffic. Neither has a per-customer baseline to measure against.

    Returns [] rather than guesses when the statistics are missing. Models
    trained before those existed have none, and inventing a plausible reason
    for a real enforcement decision would be worse than admitting there is
    none yet.
    """
    from services.backend.ml.feature_engineering import FEATURE_NAMES

    if not means or not stds:
        return []
    if not (len(vector) == len(means) == len(stds) == len(FEATURE_NAMES)):
        # A short vector compared against a full set of statistics would
        # line the wrong feature up with the wrong baseline, and the result
        # would look entirely plausible.
        return []

    rows = []
    for name, value, mean, std in zip(FEATURE_NAMES, vector, means, stds):
        sigma = (value - mean) / std if std > 0 else None
        rows.append({
            "name": name,
            "label": FEATURE_LABELS.get(name, name.replace("_", " ").capitalize()),
            "value": _figure(value),
            "normal": f"{_figure(mean)} ± {_figure(std)}",
            "sigma": sigma,
            "display": "not measurable" if sigma is None else f"{sigma:+.1f}σ",
            "drives": sigma is not None and abs(sigma) >= DRIVER_SIGMA,
        })
    # An operator reads the top of the list and stops, so the top has to be
    # the strongest signal.
    rows.sort(key=lambda r: abs(r["sigma"]) if r["sigma"] is not None else -1, reverse=True)
    return rows


def _figure(v: float) -> str:
    v = float(v)
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


AGENT_STALE_AFTER_SECONDS = 300


def agent_state(agent: dict, now: datetime | None = None,
                stale_after_seconds: int = AGENT_STALE_AFTER_SECONDS) -> dict:
    """One agent, as a person reads it.

    `status` on the item is lifecycle, not liveness: it says whether the key
    was revoked, and it keeps saying "active" for a machine that has been
    powered off for a week. Liveness is derived from `last_seen_at`, exactly
    as `AgentsTable.query_live` derives it, so the two can never disagree.

    Revoked wins over quiet. A revoked agent is also not reporting, and
    saying so first would send its owner to restart a process that is
    working fine; the key was taken away from it, usually because the tenant
    was suspended.
    """
    now = now or datetime.now(timezone.utc)
    raw = agent.get("last_seen_at")
    seen = None
    if raw:
        try:
            seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            seen = None

    age = (now - seen).total_seconds() if seen else None
    if agent.get("status") == "revoked":
        state, label = "revoked", "Revoked"
    elif age is not None and age <= stale_after_seconds:
        state, label = "live", "Reporting"
    else:
        state, label = "quiet", "Quiet"

    # Reporting is not protecting. detect_adapters() returning an empty list
    # is a legitimate outcome on a machine with no nginx and no iptables, and
    # that agent goes on sending telemetry forever while enforcing none of
    # the decisions it is sent. Three states, not two: an agent that has
    # never said is not the same as one that said "nothing", and marking
    # every pre-upgrade agent as broken would be the louder wrong answer.
    backends = agent.get("enforcers")
    if backends is None:
        can_enforce, enforce_label = "unknown", "Not reported"
    elif list(backends):
        can_enforce = "yes"
        enforce_label = ", ".join(sorted(str(b) for b in backends))
    else:
        can_enforce, enforce_label = "no", "Enforcing nothing"

    stamps = timestamp_pair(raw) if raw else {"label": "never", "exact": ""}
    return {
        "agent_id": agent.get("agent_id", ""),
        "can_enforce": can_enforce,
        "enforce_label": enforce_label,
        "live": state == "live",
        "tenant_id": agent.get("tenant_id", ""),
        # The name its owner gave the machine. A uuid does not tell anyone
        # which box to walk over to.
        "label": agent.get("agent_label") or agent.get("agent_id", ""),
        "version": agent.get("agent_version") or "unknown",
        "state": state,
        "state_label": label,
        "last_seen": humanise_age(age) if age is not None else "never",
        "last_seen_exact": stamps["exact"],
        "registered": timestamp_pair(agent.get("registered_at"))["label"]
                      if agent.get("registered_at") else "unknown",
        "registered_exact": timestamp_pair(agent.get("registered_at"))["exact"]
                            if agent.get("registered_at") else "",
    }


def axis_state(health: dict, throttle: dict, model_ready: bool) -> dict:
    """What the axis is allowed to show, and the sentence that says why.

    An instrument with no feed must not render a reading. `plot` is the
    state of the PLOT AREA, not something added beside it: at "outline" the
    bands are hairlines and no mark is drawn at all, so there is no reading
    on screen to misread. A status tile would have left the reading up and
    put a warning next to it, which is the failure this replaces.

    The order is not arbitrary. A dead agent outranks a quota, because
    nothing is arriving to be refused and naming the quota would send the
    customer to the wrong problem. A quota outranks a healthy feed, because
    the agent is reporting and being refused, and the screen has to show the
    refusal rather than the reporting.
    """
    if health.get("state") == NEVER_CONNECTED:
        return {
            "state": "no_signal", "plot": "outline", "gates_armed": False,
            "sentence": ("No agent has ever reported. Nothing is being "
                         "measured, so an empty scale is not evidence that "
                         "your traffic is clean."),
        }

    if health.get("state") == DEGRADED:
        seen = health.get("last_seen_label") or "some time ago"
        return {
            "state": "no_signal", "plot": "outline", "gates_armed": False,
            "sentence": (f"No measurement since {seen}. Nothing is being "
                         f"checked right now, and this scale is showing the "
                         f"last data we had, not current traffic."),
        }

    if throttle.get("throttled"):
        # Two different sentences, because one of them the customer can act
        # on and the other they cannot: their own share is theirs to manage,
        # and the platform ceiling was filled by somebody else.
        why = ("you reached your daily share"
               if throttle.get("throttled_reason") == "tenant"
               else "the platform reached its daily limit")
        return {
            "state": "throttled", "plot": "frozen", "gates_armed": model_ready,
            "sentence": (f"We stopped accepting your telemetry because {why}. "
                         f"Measurement resumes at midnight UTC. Your agent is "
                         f"running, and restarting it will not help."),
        }

    if not model_ready:
        return {
            "state": "day_one", "plot": "live", "gates_armed": False,
            "sentence": ("No model yet. We are measuring and enforcing "
                         "nothing. The gates below switch on after the first "
                         "training run, usually overnight."),
        }

    reporting = health.get("reporting", 0)
    seen = health.get("last_seen_label") or "just now"
    return {
        "state": "fed", "plot": "live", "gates_armed": True,
        "sentence": (f"{reporting} agent{'' if reporting == 1 else 's'} "
                     f"reporting, last seen {seen}."),
    }


def reach(decided_at: int, agents: list[dict], now: datetime) -> dict:
    """Has every agent collected decisions since this one was made.

    Principle 1.5: decided and in effect are two states, permanently. The
    agent is asynchronous by architecture, so a decision written here has not
    changed the customer's nginx until the agent next polls - and there is no
    acknowledgement channel, so this must not claim one.

    `last_seen_at` is touched by `agent_auth` on every authenticated agent
    request, including the decisions poll, so "has collected since" is a real
    measurement of the strongest claim the stored data supports. It says the
    agent collected. It does not say nginx applied it, and the wording keeps
    that distinction, because the product cannot see past the collection.

    A fleet is protected at the pace of its slowest member, so this is an
    ALL and not a majority. An unreadable or missing timestamp counts as not
    reached: a parse failure that reports a customer protected is the worst
    available way to be wrong.
    """
    if not decided_at:
        # Written before the field existed. "We cannot tell" and "not in
        # effect" are different statements, and the second one would put a
        # warning on every decision this product took before today.
        return {"in_effect": False, "label": "Unknown",
                "detail": "This decision predates the record of when it was "
                          "taken, so whether your agents have it cannot be "
                          "established."}

    if not agents:
        return {"in_effect": False, "label": "Not in effect",
                "detail": "No agent has collected this yet."}

    reached = 0
    for agent in agents:
        try:
            seen = datetime.fromisoformat(str(agent.get("last_seen_at") or ""))
        except ValueError:
            continue
        # Every timestamp this product writes is UTC. Reading a naive one as
        # local time would shift it by hours and report a decision collected
        # that was not, depending only on where the reader happens to be.
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if int(seen.timestamp()) >= int(decided_at):
            reached += 1

    total = len(agents)
    if reached == total:
        return {"in_effect": True, "label": "In effect",
                "detail": f"Collected by {total} agent"
                          f"{'' if total == 1 else 's'}."}
    behind = total - reached
    return {"in_effect": False, "label": "Not everywhere yet",
            "detail": f"{behind} of {total} agent"
                      f"{'' if total == 1 else 's'} has not collected this yet."}


def baseline_rows(names, means, stds) -> list[dict]:
    """The shape of this tenant's normal, one row per feature.

    The same three numbers `decompose` compares a single source against,
    without the source: this is the baseline on its own, which is the one
    sentence this product exists to be able to say and which nothing has ever
    printed.

    Returns [] rather than guesses when the statistics are missing. Models
    trained before per-feature statistics existed have none, and a row of
    invented figures under the heading "your normal" would be worse than an
    empty screen.
    """
    if not (names and means and stds):
        return []
    if not (len(names) == len(means) == len(stds)):
        # A short list against a full one lines the wrong figure up with the
        # wrong feature, and the result reads as entirely plausible.
        return []
    return [{
        "name": name,
        "label": FEATURE_LABELS.get(name, name.replace("_", " ").capitalize()),
        "normal": f"{_figure(mean)} ± {_figure(std)}",
        "mean": _figure(mean),
        "spread": _figure(std),
    } for name, mean, std in zip(names, means, stds)]
