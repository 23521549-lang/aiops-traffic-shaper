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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.api.routes.dashboard import (
    add_whitelist, list_history, list_mitigations, list_series, list_whitelist,
    mark_history_read, model_status, remove_whitelist,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable
from services.backend.ui.charts import deviation_chart, downsample
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.presenters import (
    absolute_expiry, agent_health, relative_expiry, severity_detail, tier_css, tier_label,
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
        "expires_label": relative_expiry(m.expires_at, now),
        "expires_exact": absolute_expiry(m.expires_at),
        "score": m.score,
        "z": m.z,
    } for m in mitigations]


def _shell(request, tenant_id, active, **extra):
    """Context every tenant page needs: who this is, the nav counts that make
    the sidebar carry state instead of merely pointing at it, and the CSRF
    token rendered into an hx-headers attribute.

    Server-rendering the token is what lets `csrf_token` become httpOnly: it
    was readable by script only so the old helper could parse
    document.cookie."""
    ctx = {"tenant_id": tenant_id, "role": "tenant", "active": active,
           "csrf_token": request.cookies.get(CSRF_COOKIE_NAME, ""),
           "theme": request.cookies.get("theme", "")}
    ctx.update(extra)
    return ctx


@router.get("/dashboard/ui", response_class=HTMLResponse)
def protection_status(request: Request, tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)):
    now = datetime.now(timezone.utc)
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    # One Query on the base table's own partition key — no GSI, no scan.
    # Without it an empty mitigation list is indistinguishable from a dead
    # agent, and the page reassures the customer either way.
    health = agent_health(AgentsTable(resource).query_by_tenant(tenant_id), now)
    return templates.TemplateResponse(request, "dashboard_status.html", _shell(
        request, tenant_id, "status",
        mitigations=_rows(mitigations, now), health=health,
        active_count=len(mitigations),
    ))


@router.get("/dashboard/ui/whitelist", response_class=HTMLResponse)
def whitelist_page(request: Request, tenant_id: str = Depends(dashboard_auth),
                   resource=Depends(get_dynamo_resource)):
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "dashboard_whitelist.html", _shell(
        request, tenant_id, "whitelist",
        whitelist=whitelist.whitelisted_ips, whitelist_count=len(whitelist.whitelisted_ips),
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
    return add_whitelist_ui(request, ip, reason, tenant_id, resource)


@router.post("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse,
             dependencies=[Depends(verify_csrf)])
def add_whitelist_ui(request: Request, ip: str, reason: str = "",
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
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": whitelist.whitelisted_ips, "error": error, "message": message,
    })


@router.delete("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse,
               dependencies=[Depends(verify_csrf)])
def remove_whitelist_ui(request: Request, ip: str, tenant_id: str = Depends(dashboard_auth),
                        resource=Depends(get_dynamo_resource)):
    remove_whitelist(ip, tenant_id=tenant_id, resource=resource)
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": whitelist.whitelisted_ips,
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
