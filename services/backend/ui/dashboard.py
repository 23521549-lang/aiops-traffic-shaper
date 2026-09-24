"""Tenant-facing pages.

Split into real routes (ADR-007). The sidebar previously linked three
`#anchors` on one page and hardcoded which one was "active", so two of the
three nav items were mislabelled at all times and the agent filter's state
could not be bookmarked or shared.

Routes also read less. The single page fanned out to three independent
DynamoDB read paths on every load, so a customer opening the portal purely
to check their allowed list paid for a mitigation query and a model-metadata
GetItem too. Each route now reads only what it renders.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from services.backend.api.cognito_auth import (
    actor_of, dashboard_auth, dashboard_claims,
)
from services.backend.api.routes.agent import register_agent
from services.backend.api.routes.dashboard import (
    add_whitelist, list_history, list_mitigations, list_own_agents, list_series,
    list_whitelist, mark_history_read, model_status, remove_whitelist,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.api.routes.agent import _try_history
from services.backend.core.tables import (
    AgentsTable, TenantHistoryTable, TenantsTable,
)
from services.backend.core.usage import (
    protection_status as usage_protection_status,
)
from services.backend.ml.model import TIER1_Z, TIER2_Z, ModelManager
from services.backend.ui.charts import (
    FEATURE_TRACK_HEIGHT, FEATURE_TRACK_WIDTH, build_axis, feature_runs,
    feature_track, gate_curve, history_grid,
)
from services.backend.schemas.agent_register import AgentRegisterRequest
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.hx import hx_return
from services.backend.ui.presenters import (
    absolute_expiry, agent_health, agent_state, axis_state, decompose,
    reason_label, relative_expiry, severity_detail,
    tier_css, tier_label,
    timestamp_pair,
)
from services.backend.ui.templates_env import templates

router = APIRouter()


def _rows(mitigations, now):
    return [{
        "ip": m.ip,
        "tier": m.tier,
        "tier_label": tier_label(m.tier),
        "tier_css": tier_css(m.tier),
        "severity": severity_detail(m.z),
        # The column is scanned, not read: a phrase belongs in the detail
        # pane, a figure belongs in the table.
        "sigma_short": f"{abs(m.z):.1f}σ" if m.z is not None else "n/a",
        "expires_label": relative_expiry(m.expires_at, now),
        "expires_exact": absolute_expiry(m.expires_at),
        "score": m.score,
        "z": m.z,
        # Words, not the stored value. The pane exists to explain a
        # decision to a person, and it was printing behavioral_anomaly.
        "reason": reason_label(m.reason),
        "features": m.features,
    } for m in mitigations]


def _whitelist_rows(whitelist):
    """The form has always asked for a reason. Nothing displayed it, so the
    field hint promised a column that did not exist."""
    return [{
        "ip": e.get("ip", ""),
        "reason": e.get("reason", ""),
        "added": timestamp_pair(e.get("added_at"))["label"],
        "added_exact": timestamp_pair(e.get("added_at"))["exact"],
    } for e in whitelist.entries]


def _shell(request, tenant_id, active, **extra):
    """Context every tenant page needs: who this is, the nav counts that make
    the sidebar carry state instead of merely pointing at it, and the CSRF
    token rendered into an hx-headers attribute.

    Server-rendering the token is what lets `csrf_token` become httpOnly: it
    was readable by script only so the old helper could parse
    document.cookie."""
    ctx = {"tenant_id": tenant_id, "role": "tenant", "active": active,
           "csrf_token": request.cookies.get(CSRF_COOKIE_NAME, ""),
           # The console defaults to dark: it is read at 3am and it is a
           # different place from the marketing site. The toggle still
           # works, and both themes carry measured contrast.
           "theme": request.cookies.get("theme") or "dark"}
    ctx.update(extra)
    return ctx


@router.get("/dashboard/ui", response_class=HTMLResponse)
def protection_status(request: Request, ip: str | None = None,
                      feature: str | None = None,
                      allowed: str = "", skipped: str = "",
                      tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)):
    """The list and the selected source, side by side.

    `?ip=` selects a row and `?ip=&feature=` narrows that to one of the seven
    dimensions. Real URLs rather than client-side toggles, so both deep-link,
    survive a refresh, work with the back button, and need no JavaScript at
    all. A decision exists to be pasted into a ticket, and only a URL does
    that.
    """
    now = datetime.now(timezone.utc)
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    # One Query on the base table's own partition key — no GSI, no scan.
    # Without it an empty mitigation list is indistinguishable from a dead
    # agent, and the page reassures the customer either way.
    health = agent_health(AgentsTable(resource).query_by_tenant(tenant_id), now)
    rows = _rows(mitigations, now)
    selected = next((r for r in rows if r["ip"] == ip), None)
    stats = None
    if selected:
        # The statistics travel with the model that is already cached for
        # scoring, so opening a detail pane costs no extra read.
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        stats = mgr.stats
        selected["features_breakdown"] = decompose(
            selected["features"] or [],
            getattr(stats, "feature_means", None),
            getattr(stats, "feature_stds", None),
        )
    # The model is loaded here anyway for the detail pane, and its cached
    # stats carry this tenant's own gate. Bands drawn from the module
    # constants would show every tenant somebody else's threshold.
    if not selected:
        mgr = ModelManager()
        mgr.load(resource, tenant_id)
        stats = mgr.stats
    # The gate of RECORD is on Tenants, not on the model: save_model rewrites
    # the Models item every night and would silently revert the operator. The
    # console reads it from there rather than taking the copy that rode in on
    # the stats, because a control whose own screen still shows the old
    # position has not visibly done anything. One small GetItem on a page
    # that already costs a Query and a model load.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    enforced_sigma = abs(getattr(stats, "tier1_z", TIER1_Z))
    tier1_sigma = abs(float(tenant.get("tier1_z", -enforced_sigma)))
    tier2_sigma = abs(float(tenant.get("tier2_z",
                                       getattr(stats, "tier2_z", TIER2_Z))))

    # The shape below the gate. One Query for the last 24 hours, and the
    # same rows carry the request count, so they are two renderings of one
    # read rather than two reads.
    now_ts = int(now.timestamp())
    series = TenantHistoryTable(resource).query_series(
        tenant_id, now_ts - 86_400, now_ts, fill=False)
    bins: dict[str, int] = {}
    measured = 0
    for row in series:
        measured += int(row.get("requests", 0))
        for key, value in row.items():
            if key.startswith("n") and key[1:].isdigit():
                bins[key] = bins.get(key, 0) + int(value)

    # Two keys on one table in one BatchGetItem. The global ceiling matters
    # as much as this tenant's own: ingest is refused platform-wide, so a
    # tenant inside its share can still be unmeasured.
    throttle = usage_protection_status(resource, tenant_id)
    state = axis_state(health, throttle, model_ready=stats is not None)

    # No feed, no reading. The marks are withheld here rather than hidden in
    # CSS, so there is genuinely nothing on the page to misread.
    axis = build_axis(tier1_sigma, tier2_sigma, bins,
                      rows if state["plot"] == "live" else [],
                      selected_ip=selected["ip"] if selected else None)

    # What each of the thirteen legal gates would have caught. The sources
    # already past the current gate count at every line below it too - they
    # are not in `bins`, because a source that is acted on is stored by
    # address and never folded into the anonymous shape.
    curve = gate_curve(bins, tier1_sigma,
                       len([r for r in rows if r["z"] is not None]))

    # Zoom 2. Reachable only from a selected source, and only for one of the
    # seven real names: `feature` is used to INDEX a vector, so a value that
    # is not on the list must never reach that far.
    zoom = _feature_zoom(resource, tenant_id, selected, feature, stats, now_ts)
    # Stored now, enforced when the nightly retrain copies it onto the model.
    # Between those two moments the screen is showing a number that is not
    # yet the number being applied, and it has to say which is which.
    pending = (state["gates_armed"]
               and abs(enforced_sigma - tier1_sigma) > 1e-9)

    return templates.TemplateResponse(request, "dashboard_status.html", _shell(
        request, tenant_id, "status",
        mitigations=rows, health=health, selected=selected,
        detail_open=selected is not None,
        axis=axis, axis_state=state, measured=measured,
        curve=curve, enforced_sigma=enforced_sigma, pending=pending,
        zoom=zoom, track_width=FEATURE_TRACK_WIDTH,
        track_height=FEATURE_TRACK_HEIGHT,
        active_count=len(mitigations),
        outcome=_bulk_outcome(allowed, skipped),
    ))


def _feature_zoom(resource, tenant_id: str, selected, feature, stats,
                  now_ts: int) -> dict | None:
    """One feature of the selected source, over the retained window.

    Costs a 7-day episode Query, and pays it only when `feature` is set -
    which is never on a default page load. The alternative, holding a track
    for all seven on every load, would be seven times the work for a screen
    nobody has asked for yet.
    """
    from services.backend.ml.feature_engineering import FEATURE_NAMES

    if not (selected and feature and feature in FEATURE_NAMES):
        return None
    row = next((r for r in selected.get("features_breakdown") or []
                if r["name"] == feature), None)
    if row is None:
        # No decomposition means the model carries no per-feature statistics,
        # so there is no baseline to measure this dimension against and
        # nothing honest to draw.
        return None

    index = FEATURE_NAMES.index(feature)
    means = getattr(stats, "feature_means", None) or []
    stds = getattr(stats, "feature_stds", None) or []
    episodes = [e for e in list_history(since=now_ts - 7 * 86_400, until=now_ts,
                                        tenant_id=tenant_id, resource=resource)
                if e.ip == selected["ip"]]
    points = feature_track(episodes, index,
                           float(means[index]) if index < len(means) else 0.0,
                           float(stds[index]) if index < len(stds) else 0.0)
    return {"f": row, "runs": feature_runs(points)}


def _bulk_outcome(allowed: str, skipped: str) -> dict | None:
    """What the bulk action did, carried back from the redirect.

    Declared as strings and parsed here on purpose. `int` in the signature
    would make a hand-edited URL a 422 on the main screen of the product,
    and this value is reflected into the page, so it is checked on the way
    out rather than trusted on the way in.
    """
    try:
        ok, bad = int(allowed), int(skipped)
    except ValueError:
        return None
    if ok < 0 or bad < 0 or (ok == 0 and bad == 0):
        return None
    return {"allowed": ok, "skipped": bad}


@router.get("/dashboard/ui/agents", response_class=HTMLResponse)
def agents_page(request: Request, id: str | None = None,
                tenant_id: str = Depends(dashboard_auth),
                resource=Depends(get_dynamo_resource)):
    """The fleet, and the selected machine beside it.

    Until now a customer whose protection had stopped was told only that no
    telemetry had arrived. Which of their servers had gone quiet was not
    shown anywhere in the product, on a screen they would be reading during
    the incident it caused.

    `?id=` selects, like `?ip=` on Protection: a real URL, so it deep-links
    into a ticket, survives a refresh, and needs no script. The lookup is
    done over the rows already fetched, never by id against the table, so an
    id guessed from another tenant finds nothing rather than finding
    somebody else's machine.
    """
    return _agents_page(request, tenant_id, resource, selected_id=id)


def _agents_page(request, tenant_id, resource, selected_id=None,
                 created=None, error=None, label=""):
    now = datetime.now(timezone.utc)
    rows = [agent_state(a.model_dump(), now)
            for a in list_own_agents(tenant_id=tenant_id, resource=resource)]
    selected = next((r for r in rows if r["agent_id"] == selected_id), None)
    live = sum(1 for r in rows if r["state"] == "live")

    return templates.TemplateResponse(request, "dashboard_agents.html", _shell(
        request, tenant_id, "agents",
        agents=rows, selected=selected, detail_open=selected is not None,
        live_count=live, quiet_count=sum(1 for r in rows if r["state"] == "quiet"),
        agent_count=len(rows), created=created, error=error,
        new_label=label,
        # The command on screen has to point at THIS deployment. Read from
        # the request rather than configured, so it is right on the laptop,
        # on the Lambda URL and behind CloudFront without three settings.
        backend_url=str(request.base_url).rstrip("/"),
    ))


# One synchronous write each against a table on the account-wide 25 WCU
# pool. `flag_all_for_ip` taught the rest: a caller over the cap is refused,
# never served the first fifty in silence.
BULK_ALLOW_LIMIT = 50


# Registered BEFORE /whitelist/{ip}, and that ordering is load-bearing:
# FastAPI matches in declaration order, so the other way round this reads as
# an attempt to allow an address named "bulk" and loses the whole selection
# behind a success-shaped response.
@router.post("/dashboard/ui/whitelist/bulk", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def bulk_allow_ui(ip: list[str] = Query(default=[]),
                  tenant_id: str = Depends(dashboard_auth),
                  claims: dict = Depends(dashboard_claims),
                  resource=Depends(get_dynamo_resource)):
    """Allow several sources in one action.

    The addresses ride in the query string, repeated, so this POST still has
    no body and still needs no CloudFront payload hash (ADR-005). The same
    reason the single-source button posts to a path.

    The outcome travels home as two integers rather than a rendered message:
    the caller posts with hx-swap="none" from a page it is about to leave,
    so a body would be discarded. Integers are also the only thing safe to
    reflect back into a page from a request.
    """
    wanted = list(dict.fromkeys(ip))  # a duplicated checkbox is one allowance
    if len(wanted) > BULK_ALLOW_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=(f"Select at most {BULK_ALLOW_LIMIT} sources at a time. "
                    f"Nothing was changed."))

    allowed = 0
    for address in wanted:
        try:
            # `claims` is passed explicitly because this calls the API
            # route as a plain function, not through FastAPI's dependency
            # injection — an unfilled Depends() default would arrive here as
            # the marker object itself.
            add_whitelist(WhitelistRequest(ip=address, reason="allowed in bulk"),
                          tenant_id=tenant_id, claims=claims, resource=resource)
            allowed += 1
        except ValidationError:
            # Counted, not raised. Two malformed rows must not cost the
            # operator the eighteen that were fine.
            continue

    return Response(status_code=200, headers={
        "HX-Redirect": f"/dashboard/ui?allowed={allowed}&skipped={len(wanted) - allowed}",
    })


@router.post("/dashboard/ui/agents", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_agent_ui(request: Request, label: str = "",
                 tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    """Mint an agent from the console.

    This replaces an instruction that pointed at nothing. The landing page
    told new customers to "get the token from Detection model in the
    console", and no page in the console has ever shown a token, so there
    was no path from signing up to being protected.

    A token was never the right answer anyway: it is a bearer credential for
    the whole account, it would have to be read off a screen and pasted
    through a shell history, and it expires within the hour. The console is
    already authenticated as this tenant, so it calls the same registration
    route the CLI calls and hands back the one credential the agent needs.

    Nothing new is minted and nothing new is stored: `register_agent` is the
    existing route, unchanged, including the suspended-tenant check that
    stops a session outliving its suspension.
    """
    label = label.strip()
    error = None
    created = None
    if not label:
        # The only thing that will ever tell its owner which machine went
        # quiet. A blank one produces a fleet of indistinguishable rows.
        error = "Give the machine a name you will recognise later."
    else:
        try:
            created = register_agent(AgentRegisterRequest(agent_label=label),
                                     tenant_id=tenant_id, resource=resource)
        except ValidationError:
            error = "That name cannot be used."
        except HTTPException as e:
            error = str(e.detail)

    return _agents_page(request, tenant_id, resource, created=created,
                        error=error, label=label)


@router.post("/dashboard/ui/gate", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def move_gate(tier: int = 1, sigma: float = 0.0,
              tenant_id: str = Depends(dashboard_auth),
              claims: dict = Depends(dashboard_claims),
              resource=Depends(get_dynamo_resource)):
    """Move one gate to one of its thirteen legal positions.

    Body-less: the values ride in the query string, the same shape the theme
    toggle uses, because a form-encoded body would need CloudFront's payload
    hash (ADR-005) and the whole point of this control is that it needs no
    script at all.

    The position is checked against the thirteen the curve offers. Accepting
    4.1 would set a gate whose consequence the screen cannot show, because no
    bin measures it, and a control that can be put somewhere it cannot report
    on is a control that lies.
    """
    if tier not in (1, 2) or sigma not in TenantHistoryTable.NEAR_BINS:
        raise HTTPException(status_code=400,
                            detail="That is not one of the available positions.")

    tenants = TenantsTable(resource)
    tenant = tenants.get(tenant_id=tenant_id) or {}
    other = float(tenant.get("tier2_z" if tier == 1 else "tier1_z",
                             TIER2_Z if tier == 1 else TIER1_Z))
    # The slow gate sits closer to normal than the block gate. The other way
    # round, every source past the block line is blocked without ever being
    # slowed, and the middle band is empty and meaningless.
    if (tier == 1 and -sigma <= other) or (tier == 2 and -sigma >= other):
        raise HTTPException(
            status_code=400,
            detail=("The slow gate has to sit closer to your normal than the "
                    "block gate."))

    which = "tier1_z" if tier == 1 else "tier2_z"
    old = tenants.set_threshold(tenant_id, which, -sigma)
    # Reporting, so it is wrapped: a throttled audit write must not lose the
    # threshold change the customer just made.
    _try_history(TenantHistoryTable(resource).record_setting,
                 tenant_id, actor=actor_of(claims), what=which,
                 old=old, new=-sigma, now=time.time())

    # Back to the screen they moved it from. The consequence of the move is
    # the thing they were reading.
    return Response(status_code=200, headers={"HX-Redirect": "/dashboard/ui"})


@router.get("/dashboard/ui/whitelist", response_class=HTMLResponse)
def whitelist_page(request: Request, tenant_id: str = Depends(dashboard_auth),
                   resource=Depends(get_dynamo_resource)):
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "dashboard_whitelist.html", _shell(
        request, tenant_id, "whitelist",
        whitelist=_whitelist_rows(whitelist),
        whitelist_count=len(whitelist.whitelisted_ips),
    ))


@router.get("/dashboard/ui/model", response_class=HTMLResponse)
def model_page(request: Request, tenant_id: str = Depends(dashboard_auth),
               resource=Depends(get_dynamo_resource)):
    model = model_status(tenant_id=tenant_id, resource=resource)
    thresholds = None
    if model.model_ready and model.score_std:
        # The thresholds expressed in this tenant's own units. "Mean score
        # -0.015" told a customer nothing; "we slow at -0.073 for your
        # traffic" is the same two numbers doing something useful.
        from services.backend.ml.model import TIER1_Z, TIER2_Z
        thresholds = {
            "slow": model.score_mean + TIER1_Z * model.score_std,
            "block": model.score_mean + TIER2_Z * model.score_std,
        }
    return templates.TemplateResponse(request, "dashboard_model.html", _shell(
        request, tenant_id, "model", model=model, thresholds=thresholds,
    ))


# --- mutations, swapped in place by htmx ---------------------------------

@router.post("/dashboard/ui/whitelist", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_form(request: Request, ip: str = "", reason: str = "",
                       tenant_id: str = Depends(dashboard_auth),
                       claims: dict = Depends(dashboard_claims),
                       resource=Depends(get_dynamo_resource)):
    """The add form. Its values ride in the query string (see the
    data-params-in-url hook in ui-status.js), so this POST has no body and
    therefore needs no CloudFront payload hash."""
    # Keyword arguments deliberately: this call sat one positional ahead
    # of the signature the moment `back` was added, and it would have
    # passed the tenant id in as the return destination.
    return add_whitelist_ui(request, ip=ip, reason=reason,
                            tenant_id=tenant_id, claims=claims, resource=resource)


_WHITELIST_RETURNS = {"status": "/dashboard/ui"}


@router.post("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_ui(request: Request, ip: str, reason: str = "", back: str = "",
                     because: str = "",
                     tenant_id: str = Depends(dashboard_auth),
                     claims: dict = Depends(dashboard_claims),
                     resource=Depends(get_dynamo_resource)):
    """The IP travels in the path, not a form body.

    That is what keeps this request out of signed-post.js: CloudFront's OAC
    only demands x-amz-content-sha256 when there IS a body (ADR-005), so a
    body-less POST goes through plain htmx with no hashing at all. It also
    lets the mitigation table offer a one-click "Allow this IP" instead of
    making the customer retype an address out of the table above.

    `because` is the line of evidence the appeal was made from, sent by the
    button that sits on that line. Checked BEFORE the whitelist write, not
    after: a 400 that has already let the source through is worse than no
    check at all.
    """
    from services.backend.ml.feature_engineering import FEATURE_NAMES

    if because and because not in FEATURE_NAMES:
        raise HTTPException(status_code=400,
                            detail="That is not one of the measured features.")

    error = None
    message = None
    try:
        add_whitelist(WhitelistRequest(ip=ip, reason=reason, because=because),
                      tenant_id=tenant_id, claims=claims, resource=resource)
        message = f"{ip} added to your allowed list. Any block on it has been lifted."
    except ValidationError:
        error = f"“{ip}” is not a valid IP address."
    # Posted from the mitigation table, where hx-swap="none" discards the
    # body: the only way to show the operator that anything happened is to
    # send them back to the list the source has just left.
    if not error:
        sent_back = hx_return(back, _WHITELIST_RETURNS)
        if sent_back is not None:
            return sent_back
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": _whitelist_rows(whitelist), "error": error, "message": message,
    })


@router.delete("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse,
               dependencies=[Depends(verify_csrf)])
def remove_whitelist_ui(request: Request, ip: str, tenant_id: str = Depends(dashboard_auth),
                        claims: dict = Depends(dashboard_claims),
                        resource=Depends(get_dynamo_resource)):
    # `claims` passed explicitly, never left to the Depends() default: this
    # calls the API route as a plain function, so the default arrives as the
    # marker object rather than the resolved claims.
    remove_whitelist(ip, tenant_id=tenant_id, claims=claims, resource=resource)
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": _whitelist_rows(whitelist),
        "message": f"{ip} removed. It will be checked like any other IP from now on.",
    })


# --- history -------------------------------------------------------------

def _episode_rows(episodes, now):
    return [{
        "ip": e.ip,
        "hour": datetime.fromtimestamp(e.hour_start, tz=timezone.utc).strftime("%d %b %H:00"),
        "hour_start": e.hour_start,
        "span": _span(e.first_ts, e.last_ts),
        "tier": e.max_tier,
        "tier_label": tier_label(e.max_tier),
        "tier_css": tier_css(e.max_tier),
        "severity": severity_detail(e.last_z),
        # The column is scanned, not read. The phrase belongs in a detail
        # pane; a figure belongs in a table.
        "sigma_short": f"{abs(e.last_z):.1f}σ" if e.last_z is not None else "n/a",
        "decisions": e.decisions,
        "tier1": e.tier1_count,
        "tier2": e.tier2_count,
        "is_new": e.is_new,
        "score": e.last_score,
        "z": e.last_z,
    } for e in episodes]


def _span(first_ts: int, last_ts: int) -> str:
    a = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%H:%M")
    b = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%H:%M")
    return a if a == b else f"{a} to {b}"


@router.get("/dashboard/ui/history", response_class=HTMLResponse)
def history_page(request: Request, days: int = 1, ip: str | None = None,
                 tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    """What the product did, over time.

    The chart and the table come from the same query window so they can
    never disagree — a chart showing a spike the table below it does not
    list is worse than no chart.

    `?ip=` narrows the table to one source. The detail pane has linked here
    with that parameter since it was written and this route did not have it,
    so the operator landed on the unfiltered list and read the wrong object
    with nothing on the screen saying so. The CHART is deliberately left
    unfiltered: it plots the worst deviation per hour across the whole
    tenant, and narrowing it to one source would be a different quantity
    under the same caption.
    """
    days = days if days in (1, 7) else 1
    now = datetime.now(timezone.utc)
    until = int(now.timestamp())
    since = until - days * 86_400

    episodes = list_history(since=since, until=until, tenant_id=tenant_id, resource=resource)
    series = list_series(since=since, until=until, tenant_id=tenant_id, resource=resource)

    # The same ruler as every other screen, rotated. The deviation chart this
    # replaces plotted one number per hour - the worst sigma seen - which
    # answers "was there a spike" and cannot answer "where did my traffic
    # sit", the question the gate control is for. Both come out of the
    # thirteen bins the hourly row already carries, so this is a second
    # reading of the Query above rather than a second Query.
    #
    # The tenant's own gates, from Tenants: bands drawn from the module
    # constants would show every tenant somebody else's threshold, cutting
    # through their own history.
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    grid = history_grid(series, episodes,
                        abs(float(tenant.get("tier1_z", TIER1_Z))),
                        abs(float(tenant.get("tier2_z", TIER2_Z))))
    # No feed, no reading. Principle 1.2: an instrument that has lost its
    # signal does not render a reading with a warning beside it.
    has_feed = any(p.batches for p in series) or bool(episodes)

    shown = [e for e in episodes if e.ip == ip] if ip else episodes
    selected, breakdown, drifted = _historic_why(resource, tenant_id, shown)         if ip else (None, [], False)

    return templates.TemplateResponse(request, "dashboard_history.html", _shell(
        request, tenant_id, "history",
        grid=grid, has_feed=has_feed,
        empty_message=_history_empty_message(series),
        episodes=_episode_rows(shown, now), days=days,
        filter_ip=ip, selected_episode=selected,
        breakdown=breakdown, drifted=drifted,
        unread=sum(1 for e in episodes if e.is_new),
        total_blocked=sum(e.tier2_count for e in shown),
        total_slowed=sum(e.tier1_count for e in shown),
    ))


def _historic_why(resource, tenant_id: str, shown):
    """The most recent episode for this source, broken out across the seven.

    The vector is frozen at decision time and the baseline is read live, so a
    retrain between the two makes the two halves of the explanation describe
    different models. `stats_version` is on the episode for exactly this, and
    this is the only place in the product able to notice. An episode that
    never recorded a version claims nothing: "we cannot tell" and "it moved"
    are different statements, and asserting the second would put a warning on
    every episode written before Phase 0.
    """
    if not shown:
        return None, [], False
    episode = max(shown, key=lambda e: e.last_ts)
    # Already cached for scoring in a warm container, so this is free on the
    # common path and one GetItem on a cold one.
    mgr = ModelManager()
    mgr.load(resource, tenant_id)
    stats = mgr.stats
    breakdown = decompose(episode.last_features,
                          getattr(stats, "feature_means", None),
                          getattr(stats, "feature_stds", None))
    live = getattr(stats, "version", None)
    drifted = bool(episode.stats_version and live
                   and episode.stats_version != live)
    return episode, breakdown, drifted


def _history_empty_message(series) -> str:
    if not any(p.batches for p in series):
        return "No telemetry in this window."
    return "Nothing crossed your threshold in this window."


@router.post("/dashboard/ui/history/mark-read", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def mark_read_ui(request: Request, tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    mark_history_read(tenant_id=tenant_id, resource=resource)
    return HTMLResponse('<p class="field-hint" data-live>Marked as read.</p>')
