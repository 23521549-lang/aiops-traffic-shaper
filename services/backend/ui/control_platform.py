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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from pydantic import ValidationError

from services.backend.api.cognito_auth import admin_auth
from services.backend.api.cognito_login import get_cognito_client
from services.backend.api.routes.admin import (
    create_tenant, list_agents, list_tenants, reactivate_tenant, suspend_tenant,
)
from services.backend.schemas.admin import TenantCreateRequest
from services.backend.api.routes.admin_usage import usage as usage_report
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import _DAILY_REQUEST_CEILING, tenant_requests_today
from services.backend.ui.csrf import CSRF_COOKIE_NAME, verify_csrf
from services.backend.ui.presenters import humanise_age, timestamp_pair, usage_share
from services.backend.ui.templates_env import templates

router = APIRouter()

_FILTERS = ("active", "stale", "revoked")


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


def _agent_rows(items, now):
    """`items` are AgentSummary models from the JSON route, not raw DynamoDB
    dicts — the UI reuses the API's own functions rather than re-querying."""
    rows = []
    for a in items:
        label = "never"
        if a.last_seen_at:
            try:
                seen = datetime.fromisoformat(a.last_seen_at.replace("Z", "+00:00"))
                label = humanise_age((now - seen).total_seconds())
            except ValueError:
                label = "unknown"
        rows.append({
            "tenant_id": a.tenant_id,
            "agent_id": a.agent_id,
            # The name the customer gave the machine, which AgentSummary
            # used to drop — leaving the console showing a bare uuid.
            "label": a.agent_label or a.agent_id,
            "version": a.agent_version or "unknown",
            "status": a.status,
            "last_seen": label,
        })
    return rows


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
def tenants_page(request: Request, resource=Depends(get_dynamo_resource)):
    return templates.TemplateResponse(request, "admin_tenants.html", _shell(
        request, "tenants", resource, **_tenants_ctx(resource),
    ))


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
    rows = []
    for t in tenants:
        used = tenant_requests_today(resource, t.tenant_id)
        rows.append({"tenant": t, "used": used,
                     "registered": timestamp_pair(t.created_at)})
    return {"tenants": rows, "tenant_count": len(tenants)}


@router.get("/admin/ui/agents", response_class=HTMLResponse,
            dependencies=[Depends(admin_auth)])
def agents_page(request: Request, status: str = "stale",
                resource=Depends(get_dynamo_resource)):
    # Opens on the filter the sidebar is shouting about. The page used to
    # default to "active" while the nav said "8 stale" in red, so clicking
    # the problem produced a blank card.
    status = status if status in _FILTERS else "stale"
    now = datetime.now(timezone.utc)
    rows = _agent_rows(list_agents(status=status, resource=resource), now)
    return templates.TemplateResponse(request, "admin_agents.html", _shell(
        request, "agents", resource,
        agents=rows, agent_status=status, filters=_FILTERS,
    ))


# --- mutations ------------------------------------------------------------

@router.post("/admin/ui/tenants/{tenant_id}/suspend", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth), Depends(verify_csrf)])
def suspend_tenant_ui(request: Request, tenant_id: str,
                      resource=Depends(get_dynamo_resource)):
    result = suspend_tenant(tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_tenants_table.html", {
        **_tenants_ctx(resource),
        "message": (f"{tenant_id} suspended. "
                    f"{result.get('agents_revoked', 0)} agent key(s) revoked."),
    })


@router.post("/admin/ui/tenants/{tenant_id}/reactivate", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth), Depends(verify_csrf)])
def reactivate_tenant_ui(request: Request, tenant_id: str,
                         resource=Depends(get_dynamo_resource)):
    reactivate_tenant(tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_tenants_table.html", {
        **_tenants_ctx(resource),
        "message": (f"{tenant_id} reactivated. Agent keys stay revoked, "
                    f"so its agents must register again."),
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
def create_tenant_ui(request: Request, name: str = "",
                     contact_email: str = "",
                     tenant_id: str = "",
                     resource=Depends(get_dynamo_resource),
                     cognito=Depends(get_cognito_client)):
    # Query parameters, not a form body. The template marks this form
    # data-params-in-url so htmx moves its values into the query string,
    # which is what keeps the request body-less and therefore free of
    # CloudFront's x-amz-content-sha256 requirement (ADR-005). Declaring
    # Form(...) here would have made the template and the route disagree —
    # a test caught exactly that.
    error = None
    message = None
    try:
        created = create_tenant(
            TenantCreateRequest(name=name, contact_email=contact_email,
                                tenant_id=tenant_id),
            resource=resource, cognito=cognito)
        message = (f"{created.tenant_id} created. A temporary password has been "
                   f"emailed to {created.first_user_email}.")
    except ValidationError:
        error = "Check the name and email address."
    except HTTPException as e:
        error = str(e.detail)

    return templates.TemplateResponse(request, "_tenants_table.html", {
        **_tenants_ctx(resource), "message": message, "error": error,
    })
