"""Publisher-facing pages.

Split into real routes (ADR-007), and the agent filter moved from a
`<select>` into links carrying `?status=`. That removes a defect rather than
restyling one: the filter was a `<form>`, the old helper called `reset()` on
every response, and the swap target sat outside the form — so choosing
"Revoked" correctly reloaded the table and then snapped the dropdown back to
"Stale". The control and the content it controlled disagreed permanently.
Links carry their own state, and the URL becomes shareable.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from pydantic import ValidationError

from services.backend.api.cognito_auth import admin_auth, admin_claims
from services.backend.api.cognito_login import get_cognito_client
from services.backend.api.routes.admin import (
    create_tenant, list_agents, list_tenants, reactivate_tenant, suspend_tenant,
)
from services.backend.schemas.admin import TenantCreateRequest
from services.backend.api.routes.admin_usage import usage as usage_report
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import (
    _DAILY_REQUEST_CEILING, tenant_daily_quota, tenant_requests_over,
    tenant_requests_today,
)
from services.backend.core.tables import AgentsTable
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.hx import hx_return
from services.backend.ui.presenters import (
    agent_state, timestamp_pair, usage_share,
)
from services.backend.ui.templates_env import templates

router = APIRouter()

def _shell(request, active, resource, **extra):
    """Nav counts make the sidebar carry state. The stale count in
    particular is the one number the operator wants without navigating."""
    stale = len(list_agents(status="stale", resource=resource))
    ctx = {"role": "admin", "active": active,
           "csrf_token": request.cookies.get(CSRF_COOKIE_NAME, ""),
           "theme": request.cookies.get("theme") or "dark",
           "stale_count": stale}
    ctx.update(extra)
    return ctx


@router.get("/admin/ui", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
def overview(request: Request, resource=Depends(get_dynamo_resource)):
    report = usage_report(resource=resource)
    tenants = list_tenants(resource=resource)
    live = list_agents(status="active", resource=resource)
    return templates.TemplateResponse(request, "admin_overview.html", _shell(
        request, "overview", resource,
        usage=report, share=usage_share(report.total_requests, _DAILY_REQUEST_CEILING),
        tenant_count=len(tenants), live_count=len(live),
    ))


@router.get("/admin/ui/tenants", response_class=HTMLResponse,
            dependencies=[Depends(admin_auth)])
def tenants_page(request: Request, id: str | None = None,
                 created: str | None = None,
                 resource=Depends(get_dynamo_resource)):
    """The list, and the selected tenant beside it.

    `?id=` selects, exactly as `?ip=` does on the customer console: a real
    URL, so it deep-links into a support thread and survives a refresh. The
    selection is resolved against the rows already fetched, so an id that
    does not exist opens nothing rather than a pane attached to an empty
    record.
    """
    ctx = _tenants_ctx(resource)
    selected = next((r for r in ctx["tenants"] if r["tenant"].tenant_id == id), None)
    if selected:
        selected = {**selected, **_tenant_detail(resource, id)}
    # Carried back through the redirect that keeps the email out of the URL.
    # Rendered only when it names a tenant that exists, so a hand-edited value
    # cannot put arbitrary text on the page.
    message = None
    if created and any(r["tenant"].tenant_id == created for r in ctx["tenants"]):
        message = (f"{created} created. A temporary password has been emailed "
                   f"to its first user.")

    return templates.TemplateResponse(request, "admin_tenants.html", _shell(
        request, "tenants", resource, selected=selected,
        detail_open=selected is not None, message=message, **ctx,
    ))


def _tenant_detail(resource, tenant_id: str) -> dict:
    """One extra Query, and only when a tenant is selected.

    Agents are partitioned by tenant_id on the base table, so this is the
    same read the customer's own console makes: no GSI, no scan, and the
    list page pays nothing for a pane nobody opened.

    The count is the point. Suspension revokes every key this tenant holds,
    and the console offered that button while showing how many keys that
    was precisely nowhere.
    """
    now = datetime.now(timezone.utc)
    agents = [agent_state(a, now)
              for a in AgentsTable(resource).query_by_tenant(tenant_id)]
    agents.sort(key=lambda a: a["label"])
    live = sum(1 for a in agents if a["state"] == "live")
    return {"agents": agents, "agent_count": len(agents), "live_agents": live,
            "quiet_agents": sum(1 for a in agents if a["state"] == "quiet")}


def _tenants_ctx(resource) -> dict:
    """Per-tenant usage alongside each tenant.

    The Suspend button is the publisher's only lever, and the usage card
    showed one pooled number — so the lever could not be aimed. The
    per-tenant counters were already being written on every accepted batch
    (core/usage.py) and read back by nothing.

    One GetItem per tenant against a table provisioned at 1 RCU. Fine at
    today's scale; past a few dozen tenants this wants BatchGetItem, and it
    must never become a scan per page load.
    """
    tenants = list_tenants(resource=resource)
    ids = [t.tenant_id for t in tenants]

    # Seven days for every tenant in one BatchGetItem rather than one GetItem
    # per tenant. Bounded by what BatchGetItem allows, and a tenant past the
    # bound is named on screen rather than dropped: a billing page that
    # silently showed the first fourteen would be lying by omission.
    shown, overflow = ids[:TENANTS_PER_PAGE], ids[TENANTS_PER_PAGE:]
    trend = tenant_requests_over(resource, shown, days=TREND_DAYS) if shown else {}

    rows = []
    for t in tenants:
        if t.tenant_id in trend:
            week = trend[t.tenant_id]
            used = week[-1]
        else:
            # Past the batch bound. One GetItem, so the row is still true;
            # the trend behind it is simply not drawn.
            week, used = [], tenant_requests_today(resource, t.tenant_id)
        rows.append({
            "tenant": t,
            # Never a bare number. `usage_share` has existed for this since it
            # was written, with the docstring saying so, on the one screen
            # that ignored it.
            "usage": usage_share(used, tenant_daily_quota()),
            "week": _spark(week),
            "registered": timestamp_pair(t.created_at),
        })
    return {"tenants": rows, "tenant_count": len(tenants),
            "not_charted": len(overflow)}


# Seven days is what tells a tenant that has always been busy apart from one
# that started flooding an hour ago, which is the only distinction the Suspend
# lever needs. Fourteen tenants times seven days is 98 keys, inside the 100
# BatchGetItem allows.
TREND_DAYS = 7
TENANTS_PER_PAGE = 14

# Bucketed into five heights, drawn as five SVG rect attributes. Nothing is
# computed into a style attribute, which CSP forbids, and SVG geometry is not
# CSS so it is unaffected.
_SPARK_LEVELS = 5


def _spark(week: list[int]) -> list[dict]:
    """A week as seven bars, each relative to that tenant's own busiest day.

    Relative to itself, not to the quota: the quota is already stated beside
    it as a percentage, and what this shape has to answer is "is today like
    the rest of the week", which is a question about the tenant alone.
    """
    if not week:
        return []
    tallest = max(week) or 0
    return [{"count": n,
             "level": 0 if not tallest else max(1, round(n / tallest * _SPARK_LEVELS))}
            for n in week]


def _tenant_returns(tenant_id: str) -> dict[str, str]:
    """The detail pane displays the status it just changed, so it has to be
    re-read rather than left showing the old value. The id is this route's
    own path parameter, never a caller-supplied URL."""
    return {"detail": f"/admin/ui/tenants?id={tenant_id}"}


# --- mutations ------------------------------------------------------------

@router.post("/admin/ui/tenants/{tenant_id}/suspend", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth), Depends(verify_csrf)])
def suspend_tenant_ui(request: Request, tenant_id: str, back: str = "",
                      claims: dict = Depends(admin_claims),
                      resource=Depends(get_dynamo_resource)):
    # `claims` passed explicitly, never left to the Depends() default: this
    # calls the API route as a plain function, so the default would arrive as
    # the marker object and the audit row would name it.
    result = suspend_tenant(tenant_id, claims=claims, resource=resource)
    sent_back = hx_return(back, _tenant_returns(tenant_id))
    if sent_back is not None:
        return sent_back
    return templates.TemplateResponse(request, "_tenants_table.html", {
        **_tenants_ctx(resource),
        "message": (f"Đã tạm ngưng {tenant_id}. "
                    f"Thu hồi {result.get('agents_revoked', 0)} khoá agent."),
    })


@router.post("/admin/ui/tenants/{tenant_id}/reactivate", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth), Depends(verify_csrf)])
def reactivate_tenant_ui(request: Request, tenant_id: str, back: str = "",
                         claims: dict = Depends(admin_claims),
                         resource=Depends(get_dynamo_resource)):
    reactivate_tenant(tenant_id, claims=claims, resource=resource)
    sent_back = hx_return(back, _tenant_returns(tenant_id))
    if sent_back is not None:
        return sent_back
    return templates.TemplateResponse(request, "_tenants_table.html", {
        **_tenants_ctx(resource),
        "message": (f"Đã mở lại {tenant_id}. Khoá agent vẫn bị thu hồi, "
                    f"nên các agent phải đăng ký lại."),
    })


@router.get("/admin/ui/tenants/new", response_class=HTMLResponse,
            dependencies=[Depends(admin_auth)])
def new_tenant_form(request: Request, resource=Depends(get_dynamo_resource)):
    """The id is minted here and carried in a hidden field.

    That is the whole idempotency story: a double-submitted form collides on
    `attribute_not_exists` and the UI renders "already exists" rather than
    quietly creating a second tenant. No idempotency-key table, no new
    state.
    """
    return templates.TemplateResponse(request, "admin_tenant_new.html", _shell(
        request, "tenants", resource, new_tenant_id=uuid.uuid4().hex,
    ))


@router.post("/admin/ui/tenants", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth), Depends(verify_csrf)])
def create_tenant_ui(request: Request,
                     name: str = Form(default=""),
                     contact_email: str = Form(default=""),
                     tenant_id: str = Form(default=""),
                     resource=Depends(get_dynamo_resource),
                     cognito=Depends(get_cognito_client)):
    """A real form body, and the only route besides sign-in that has one.

    This carries another person's email address. Riding in a query string it
    would be written into CloudFront access logs, Referer headers and browser
    history by the operator who was trying to onboard them, which is exactly
    what spec 12.8 refuses. So the form is marked data-signed-post and
    signed-post.js computes the payload hash CloudFront's OAC requires
    (ADR-005) - the mechanism that file was built for.

    Its contract dictates the shape of both answers here. It follows a
    REDIRECT and prints anything else as text, so success is a 303 and
    failure is plain text. HTML in the failure path would print markup at the
    operator.
    """
    try:
        created = create_tenant(
            TenantCreateRequest(name=name, contact_email=contact_email,
                                tenant_id=tenant_id),
            resource=resource, cognito=cognito)
    except ValidationError:
        return PlainTextResponse("Check the name and email address.",
                                 status_code=400)
    except HTTPException as e:
        return PlainTextResponse(str(e.detail), status_code=400)

    # The tenant id and nothing else. Carrying the address back in the
    # location header would have moved the leak rather than closed it.
    return RedirectResponse(url=f"/admin/ui/tenants?created={created.tenant_id}",
                            status_code=303)
