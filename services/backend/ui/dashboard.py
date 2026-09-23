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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.api.routes.agent import register_agent
from services.backend.api.routes.dashboard import (
    add_whitelist, list_history, list_mitigations, list_own_agents, list_series,
    list_whitelist, mark_history_read, model_status, remove_whitelist,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable
from services.backend.ml.model import ModelManager
from services.backend.ui.charts import deviation_chart, downsample, sigma_strip
from services.backend.schemas.agent_register import AgentRegisterRequest
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.hx import hx_return
from services.backend.ui.presenters import (
    absolute_expiry, agent_health, agent_state, decompose, reason_label,
    relative_expiry, severity_detail,
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
                      allowed: str = "", skipped: str = "",
                      tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)):
    """The list and the selected source, side by side.

    `?ip=` selects a row. A real URL rather than a client-side toggle, so it
    deep-links, survives a refresh, works with the back button, and needs no
    JavaScript at all.
    """
    now = datetime.now(timezone.utc)
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    # One Query on the base table's own partition key — no GSI, no scan.
    # Without it an empty mitigation list is indistinguishable from a dead
    # agent, and the page reassures the customer either way.
    health = agent_health(AgentsTable(resource).query_by_tenant(tenant_id), now)
    rows = _rows(mitigations, now)
    selected = next((r for r in rows if r["ip"] == ip), None)
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
    worst = min((r["z"] for r in rows if r["z"] is not None), default=None)

    return templates.TemplateResponse(request, "dashboard_status.html", _shell(
        request, tenant_id, "status",
        mitigations=rows, health=health, selected=selected,
        detail_open=selected is not None,
        strip=sigma_strip(worst),
        active_count=len(mitigations),
        outcome=_bulk_outcome(allowed, skipped),
    ))


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
            add_whitelist(WhitelistRequest(ip=address, reason="allowed in bulk"),
                          tenant_id=tenant_id, resource=resource)
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
                       resource=Depends(get_dynamo_resource)):
    """The add form. Its values ride in the query string (see the
    data-params-in-url hook in ui-status.js), so this POST has no body and
    therefore needs no CloudFront payload hash."""
    # Keyword arguments deliberately: this call sat one positional ahead
    # of the signature the moment `back` was added, and it would have
    # passed the tenant id in as the return destination.
    return add_whitelist_ui(request, ip=ip, reason=reason,
                            tenant_id=tenant_id, resource=resource)


_WHITELIST_RETURNS = {"status": "/dashboard/ui"}


@router.post("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_ui(request: Request, ip: str, reason: str = "", back: str = "",
                     tenant_id: str = Depends(dashboard_auth),
                     resource=Depends(get_dynamo_resource)):
    """The IP travels in the path, not a form body.

    That is what keeps this request out of signed-post.js: CloudFront's OAC
    only demands x-amz-content-sha256 when there IS a body (ADR-005), so a
    body-less POST goes through plain htmx with no hashing at all. It also
    lets the mitigation table offer a one-click "Allow this IP" instead of
    making the customer retype an address out of the table above.
    """
    error = None
    message = None
    try:
        add_whitelist(WhitelistRequest(ip=ip, reason=reason), tenant_id=tenant_id,
                      resource=resource)
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
                        resource=Depends(get_dynamo_resource)):
    remove_whitelist(ip, tenant_id=tenant_id, resource=resource)
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
def history_page(request: Request, days: int = 1,
                 tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)):
    """What the product did, over time.

    The chart and the table come from the same query window so they can
    never disagree — a chart showing a spike the table below it does not
    list is worse than no chart.
    """
    days = days if days in (1, 7) else 1
    now = datetime.now(timezone.utc)
    until = int(now.timestamp())
    since = until - days * 86_400

    episodes = list_history(since=since, until=until, tenant_id=tenant_id, resource=resource)
    series = list_series(since=since, until=until, tenant_id=tenant_id, resource=resource)

    # The deviation chart plots the worst sigma seen in each hour. An hour
    # with traffic but no decision is a real zero; an hour with no row at
    # all is a gap and must stay None, or a dead agent renders as calm.
    worst = {}
    for e in episodes:
        z = abs(e.last_z) if e.last_z is not None else 0.0
        worst[e.hour_start] = max(worst.get(e.hour_start, 0.0), z)
    points = [(p.hour_start, -worst.get(p.hour_start, 0.0) if p.batches or p.hour_start in worst else None)
              for p in series]

    chart = deviation_chart(
        downsample(points),
        empty_message=_history_empty_message(series),
        caption=f"Worst deviation per hour, last {'24 hours' if days == 1 else '7 days'}.",
        label_for=lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M"),
    )

    return templates.TemplateResponse(request, "dashboard_history.html", _shell(
        request, tenant_id, "history",
        chart=chart, episodes=_episode_rows(episodes, now), days=days,
        unread=sum(1 for e in episodes if e.is_new),
        total_blocked=sum(e.tier2_count for e in episodes),
        total_slowed=sum(e.tier1_count for e in episodes),
    ))


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
