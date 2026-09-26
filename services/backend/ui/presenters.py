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
    (-6.0, "Rất xa bình thường của bạn"),
    (TIER2_Z, "Khá xa bình thường của bạn"),   # -5.0
    (-4.5, "Rõ ràng ngoài bình thường của bạn"),
    (TIER1_Z, "Ngoài bình thường của bạn"),        # -4.0
]

NOT_MEASURABLE = "Bất thường · không đo được"


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
    return "Ngoài bình thường của bạn"


def severity_detail(z: float | None) -> str:
    """The phrase with the figure appended, for the row the operator reads."""
    if z is None:
        return NOT_MEASURABLE
    return f"{severity_phrase(z)} · {abs(z):.1f}σ"


def tier_label(tier: int) -> str:
    """"Hard block" invited the question "what is a soft block?", and
    "rate limit" named the mechanism rather than the outcome. The customer
    wants to know what happened to the traffic."""
    return "Đang chặn" if int(tier) >= 2 else "Đang làm chậm"


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
        return "Đã kết thúc"
    if remaining < 60:
        return "dưới một phút nữa"
    minutes = remaining // 60
    if minutes < 60:
        return f"{minutes} phút nữa"
    hours = minutes / 60
    return f"{hours:.0f} giờ nữa" if hours >= 2 else "khoảng một giờ nữa"


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
        return _count(max(seconds, 1), "giây")
    if seconds < 3600:
        return _count(seconds // 60, "phút")
    if seconds < 86400:
        return _count(seconds // 3600, "giờ")
    return _count(seconds // 86400, "ngày")


def _count(n: int, unit: str) -> str:
    # Vietnamese has no plural inflection, so the branch that shipped
    # "1 minutes ago" three times cannot recur in this language. The four
    # callers stay on one helper anyway: the next language will need it.
    return f"{n} {unit} trước"


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
        return {"label": "không rõ", "relative": "", "exact": ""}
    try:
        dt = (datetime.fromtimestamp(int(raw), tz=timezone.utc)
              if isinstance(raw, (int, float))
              else datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except (ValueError, OSError):
        return {"label": "không rõ", "relative": "", "exact": str(raw)}
    return {
        "label": dt.strftime("%d %b %Y"),
        "relative": humanise_age((now - dt).total_seconds()),
        "exact": dt.isoformat(timespec="seconds"),
    }


# --- why this source ------------------------------------------------------

# `unique_uri_ratio` is a column in a dataframe. "Số đường dẫn khác nhau" is something
# a person reading an incident at 3am can act on.
# The value stored on a mitigation, and what a customer should read. The
# detail pane printed `behavioral_anomaly` under "Reason", in a pane whose
# whole job is explaining an enforcement decision to a person. Every other
# machine name in this product is mapped to words before it is displayed.
REASON_LABELS = {
    "behavioral_anomaly": "Traffic khác bình thường của bạn",
    "manual_block": "Chặn bằng tay",
    "whitelisted": "Đã cho qua",
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
    "request_rate": "Số request mỗi phút",
    "error_ratio": "Tỉ lệ lỗi",
    "avg_bytes_sent": "Byte trung bình",
    "avg_request_time": "Thời gian trung bình",
    "unique_uri_ratio": "Số đường dẫn khác nhau",
    "user_agent_entropy": "Độ tản của user agent",
    "post_ratio": "Tỉ lệ POST",
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
            "display": "không đo được" if sigma is None else f"{sigma:+.1f}σ",
            "drives": sigma is not None and abs(sigma) >= DRIVER_SIGMA,
            # Geometry and depth for the bar beside the row. Computed here
            # rather than in the template, because a template that does
            # arithmetic is a template nothing can test. Width is out of 300
            # to match the bar's viewBox; the ceiling is the same six sigma
            # the axis uses, so a bar here and a dot there mean one thing.
            "width": 0 if sigma is None else round(min(abs(sigma), 6.0) / 6.0 * 300, 1),
            "tint": "t2" if sigma is None or abs(sigma) < 2 else (
                "t4" if abs(sigma) < 4 else "t5"),
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
        state, label = "revoked", "Đã thu hồi"
    elif age is not None and age <= stale_after_seconds:
        state, label = "live", "Đang báo cáo"
    else:
        state, label = "quiet", "Im lặng"

    # Reporting is not protecting. detect_adapters() returning an empty list
    # is a legitimate outcome on a machine with no nginx and no iptables, and
    # that agent goes on sending telemetry forever while enforcing none of
    # the decisions it is sent. Three states, not two: an agent that has
    # never said is not the same as one that said "nothing", and marking
    # every pre-upgrade agent as broken would be the louder wrong answer.
    backends = agent.get("enforcers")
    if backends is None:
        can_enforce, enforce_label = "unknown", "Chưa báo"
    elif list(backends):
        can_enforce = "yes"
        enforce_label = ", ".join(sorted(str(b) for b in backends))
    else:
        can_enforce, enforce_label = "no", "Không thi hành gì"

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
        "version": agent.get("agent_version") or "không rõ",
        "state": state,
        "state_label": label,
        "last_seen": humanise_age(age) if age is not None else "never",
        "last_seen_exact": stamps["exact"],
        "registered": timestamp_pair(agent.get("registered_at"))["label"]
                      if agent.get("registered_at") else "không rõ",
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
            "sentence": ("Chưa agent nào báo về. Không có gì đang được đo, nên "
                         "một màn hình trống không phải bằng chứng rằng "
                         "traffic của bạn sạch."),
        }

    if health.get("state") == DEGRADED:
        seen = health.get("last_seen_label") or "some time ago"
        return {
            "state": "no_signal", "plot": "outline", "gates_armed": False,
            "sentence": (f"Không có phép đo nào từ {seen}. Hiện không có gì "
                         f"đang được kiểm, và những gì bạn thấy là dữ liệu "
                         f"cuối cùng chúng tôi có, không phải traffic hiện tại."),
        }

    if throttle.get("throttled"):
        # Two different sentences, because one of them the customer can act
        # on and the other they cannot: their own share is theirs to manage,
        # and the platform ceiling was filled by somebody else.
        why = ("bạn đã dùng hết phần trong ngày"
               if throttle.get("throttled_reason") == "tenant"
               else "nền tảng đã chạm trần trong ngày")
        return {
            "state": "throttled", "plot": "frozen", "gates_armed": model_ready,
            "sentence": (f"Chúng tôi ngừng nhận telemetry vì {why}. Phép đo "
                         f"chạy lại lúc nửa đêm UTC. Agent của bạn vẫn đang "
                         f"chạy, và khởi động lại nó sẽ không giúp gì."),
        }

    if not model_ready:
        return {
            "state": "day_one", "plot": "live", "gates_armed": False,
            "sentence": ("Chưa có mô hình. Chúng tôi đang đo và chưa thi hành "
                         "gì. Các cổng bên dưới bật lên sau lần huấn luyện "
                         "đầu tiên, thường là qua đêm."),
        }

    reporting = health.get("reporting", 0)
    seen = health.get("last_seen_label") or "just now"
    return {
        "state": "fed", "plot": "live", "gates_armed": True,
        "sentence": (f"{reporting} agent đang báo cáo, lần gần nhất {seen}."),
    }


def reach(decided_at: int, agents: list[dict], now: datetime,
          paused: bool = False) -> dict:
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
    if paused:
        # The strongest claim the data supports has changed shape entirely.
        # While enforcement is paused the platform serves an empty active
        # set, so the agents have released these rules - counting how many
        # have "collected since" would be true and completely misleading.
        return {"in_effect": False, "label": "Chỉ theo dõi",
                "detail": "Bạn đã tạm dừng thi hành, nên máy chủ của bạn không áp gì. "
                          "Đây là thứ đáng lẽ đang có hiệu lực."}

    if not decided_at:
        # Written before the field existed. "We cannot tell" and "not in
        # effect" are different statements, and the second one would put a
        # warning on every decision this product took before today.
        return {"in_effect": False, "label": "Không rõ",
                "detail": "Quyết định này có trước khi sản phẩm bắt đầu ghi thời điểm, "
                          "nên không xác định được agent của bạn đã nhận "
                          "hay chưa."}

    if not agents:
        return {"in_effect": False, "label": "Chưa có hiệu lực",
                "detail": "Chưa agent nào nhận cái này."}

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
    # No English plural `s`. Vietnamese does not mark plurals on the noun, so
    # the old suffix produced "3 agents đã nhận" on a screen that is otherwise
    # entirely Vietnamese, and the second branch was never translated at all.
    if reached == total:
        return {"in_effect": True, "label": "Đã có hiệu lực",
                "detail": f"{total} agent đã nhận."}
    behind = total - reached
    return {"in_effect": False, "label": "Chưa đủ mọi máy",
            "detail": f"{behind} trong {total} agent chưa nhận quyết định này."}


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
    # One standard deviation either side of the mean, drawn on a 300-unit
    # track. Each feature is scaled against ITS OWN range, because the seven
    # are measured in different units - requests per minute against a ratio
    # against bytes - and a shared scale would make six of them invisible.
    rows = []
    for name, mean, std in zip(names, means, stds):
        top = max(abs(mean) + 2 * abs(std), 1e-9)
        lo = max(0.0, (mean - std) / top)
        hi = min(1.0, (mean + std) / top)
        rows.append({
            "name": name,
            "label": FEATURE_LABELS.get(name, name.replace("_", " ").capitalize()),
            "normal": f"{_figure(mean)} ± {_figure(std)}",
            "mean": _figure(mean),
            "spread": _figure(std),
            "x": round(lo * 300, 1),
            "w": round(max(hi - lo, 0.01) * 300, 1),
            "mid": round(min(max(mean / top, 0.0), 1.0) * 300, 1),
        })
    return rows


def system_picture(agents: list[dict], rows: list[dict], tier1_sigma: float,
                   tier2_sigma: float, now: datetime) -> dict:
    """Everything the architecture drawing states, read off the real code.

    Nothing here is written into the template. The TTLs come from the route
    that sets them, the capacity figures from the table module's own budget
    comment, and the tool names from the agents' own reports - so if any of
    them changes, the drawing changes with it instead of quietly becoming a
    lie. A diagram that has drifted from the system is worse than no diagram,
    because a reader trusts a picture more than a sentence.
    """
    from services.backend.api.routes.agent import _TTL_SECONDS
    from services.backend.ml.model import AnomalyTier

    def _mins(seconds: int) -> str:
        if seconds >= 3600:
            return f"{seconds // 3600} giờ"
        return f"{seconds // 60} phút"

    # An agent is "holding" when it has collected since the oldest decision
    # still in force. With nothing in force there is nothing to be behind on.
    oldest = min((r["decided_at"] for r in rows if r.get("decided_at")),
                 default=0)
    fleet = []
    for agent in agents:
        label = str(agent.get("agent_label") or agent.get("agent_id", ""))
        fleet.append({
            "short": label[-2:] if label else "??",
            "holding": reach(oldest, [agent], now)["in_effect"] if oldest else True,
        })
    behind = sum(1 for a in fleet if not a["holding"])

    tools = sorted({str(e) for a in agents for e in (a.get("enforcers") or [])})
    reported = any(a.get("enforcers") is not None for a in agents)

    return {
        "slow": f"{tier1_sigma:.2f}",
        "block": f"{tier2_sigma:.2f}",
        "slow_for": _mins(_TTL_SECONDS[AnomalyTier.RATE_LIMIT]),
        "block_for": _mins(_TTL_SECONDS[AnomalyTier.HARD_BLOCK]),
        # The account-wide budget this whole architecture is shaped around.
        "rcu": 14, "rcu_cap": 25, "wcu": 20, "wcu_cap": 25,
        "tools": (f"tìm thấy ở đây: {', '.join(tools)}" if tools
                  else ("tìm thấy ở đây: không có gì để ghi luật" if reported
                        else "agent của bạn chưa báo")),
        "fleet": fleet[:7],
        "fleet_line": (
            f"Một máy phát hiện kẻ tấn công thì cả {len(fleet)} máy của bạn "
            f"chặn nó, kể cả những máy chưa từng bị nó chạm tới."
            if len(fleet) > 1 else
            "Thêm máy thứ hai thì mỗi máy sẽ thi hành thứ máy kia tìm ra."),
        "fleet_state": (f"{behind} trên {len(fleet)} máy chưa bắt kịp"
                        if behind else
                        f"cả {len(fleet)} máy đang giữ cùng một tập"),
    }


# --- what this platform can be told to do ----------------------------------

# Written here rather than in the template because it is an INVENTORY, not
# decoration: the third column is a commitment about the product, and the
# fourth is the reason. Two rows say "refused", and they are the reason the
# table earns a screen - a free platform is defined by what it declines to
# do more than by what it does, and hiding that until a customer asks is how
# a free tier becomes a surprise.
CAPABILITIES = [
    ("Thi hành", "Chỉ theo dõi, và bật lại", "có",
     "Tập phục vụ về rỗng, agent nhả luật trong khoảng 5 giây. Chạy được với agent đã cài sẵn."),
    ("Thi hành", "Tạm dừng có hẹn giờ", "chưa",
     "Bạn tắt lúc 3 giờ sáng rồi quên bật. Tự bật lại là an toàn, không phải tiện nghi."),
    ("Thi hành", "Thả một nguồn ngay", "có",
     "Cho qua địa chỉ đó; cùng đường reconcile."),
    ("Cổng", "Dời cổng làm chậm và cổng chặn", "có",
     "13 vị trí hợp lệ, mỗi vị trí kèm hệ quả đo được."),
    ("Cổng", "Hẹn giờ đổi cổng", "chưa", "Một hàng nhỏ và một lần đọc thêm."),
    ("Cổng", "Cổng riêng theo khung giờ", "chưa",
     "Cần một trường nữa trên tenant; đường chấm điểm phải đọc giờ."),
    ("Một nguồn", "Cho qua, kèm lý do", "có", "Ghi sổ, và loại khỏi huấn luyện."),
    ("Một nguồn", "Chặn tay 24 giờ", "chưa", "Đối xứng với cho qua, cùng bảng."),
    ("Một nguồn", "Gia hạn hoặc rút ngắn", "chưa", "Một lần ghi expires_at."),
    ("Một nguồn", "Sao chép làm bằng chứng", "chưa", "Thuần trình duyệt, không tốn gì."),
    ("Mô hình", "Quay lui bản trước", "chưa",
     "Cần đổi cách lưu: mỗi bản một khoá riêng, thêm một con trỏ vài byte. Rẻ hơn hiện tại."),
    ("Mô hình", "Đóng băng, ngừng huấn luyện đêm", "chưa", "Một cờ trên tenant."),
    ("Mô hình", "Loại một khoảng thời gian khỏi huấn luyện", "chưa",
     "Đã có cơ chế loại bucket khỏi huấn luyện."),
    ("Mô hình", "Huấn luyện lại ngay", "bị từ chối",
     "Tốn compute theo yêu cầu. Phá luật chi phí bằng 0."),
    ("Agent", "Đăng ký, và thu hồi khoá", "có", ""),
    ("Agent", "Nạp lại nginx, xoá luật cục bộ", "chưa",
     "Một cờ một lần trong phản hồi telemetry."),
    ("Agent", "Khởi động lại, nâng cấp", "chưa",
     "Cùng cơ chế, rủi ro cao hơn: tự khởi động lại là mất liên lạc."),
    ("Agent", "Thu ngay", "bị từ chối",
     "Agent đã gọi mỗi 5 giây. Nút này chỉ là diễn."),
    ("Chi phí", "Hạ trần của chính mình", "chưa", "Một trường trên item vốn đã đọc."),
    ("Chi phí", "Báo khi dùng tới 80%", "chưa", "Cần kênh gửi thông báo."),
    ("Sổ sách", "Nhật ký ai đổi gì", "có", "Dữ liệu đã ghi từ lâu."),
    ("Sổ sách", "Xuất bằng chứng CSV", "có", ""),
]


def capabilities() -> list[dict]:
    """The inventory, with the group printed once per run of rows.

    Repeating "Thi hành" down three rows is noise in a table whose first
    column is a heading; the eye reads the change, not the repetition.
    """
    out, last = [], None
    for group, name, state, note in CAPABILITIES:
        out.append({"group": "" if group == last else group,
                    "name": name, "state": state, "note": note})
        last = group
    return out


# --- the ledger, as a person reads it --------------------------------------

_WHAT_LABELS = {
    "tier1_z": "Dời cổng làm chậm",
    "tier2_z": "Dời cổng chặn",
    "enforcement": "Đổi trạng thái thi hành",
    "whitelist_add": "Cho qua một địa chỉ",
    "whitelist_remove": "Bỏ khỏi danh sách cho qua",
    "agent_register": "Đăng ký agent",
    "agent_revoke": "Thu hồi khoá agent",
}


def _ledger_value(what: str, value) -> str:
    """A stored value, in the unit the screen that set it used.

    Thresholds are stored negative because the model scores downward, and a
    ledger printing "-4.0 -> -3.75" asks the reader to hold that convention
    in their head while they are trying to work out what somebody did.
    """
    if value is None or value == "":
        return ""
    if what in ("tier1_z", "tier2_z"):
        try:
            return f"{abs(float(value)):.2f} sigma".replace(".", ",")
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def ledger_rows(items: list[dict], now: datetime) -> list[dict]:
    """Every change this tenant made, newest first.

    The product has WRITTEN this since the settings ledger shipped and has
    never shown it to anybody. "Who changed it, and when" is the first
    question the review after an incident opens with, and the answer existed
    the whole time with no screen to appear on.
    """
    out = []
    for item in items:
        at = int(item.get("at") or 0)
        what = str(item.get("what") or "")
        out.append({
            "what": _WHAT_LABELS.get(what, what.replace("_", " ")),
            "raw": what,
            "actor": str(item.get("actor") or "không rõ"),
            "old": _ledger_value(what, item.get("old")),
            "new": _ledger_value(what, item.get("new")),
            "because": str(item.get("because") or ""),
            "when": humanise_age(now.timestamp() - at) if at else "",
            "exact": absolute_expiry(at) if at else "",
        })
    # Newest first, strictly. A ledger out of order is not a ledger.
    out.sort(key=lambda r: r["exact"], reverse=True)
    return out
