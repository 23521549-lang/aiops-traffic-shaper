from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.api.routes.dashboard import (
    add_whitelist, list_mitigations, list_whitelist, model_status, remove_whitelist,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.schemas.whitelist import WhitelistRequest
from services.backend.ui.templates_env import templates

router = APIRouter()


def _format_expiry(epoch: int) -> str:
    if not epoch:
        return "—"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@router.get("/dashboard/ui", response_class=HTMLResponse)
def dashboard_page(request: Request, tenant_id: str = Depends(dashboard_auth),
                    resource=Depends(get_dynamo_resource)):
    # Reuses the same JSON-API route functions from api/routes/dashboard.py
    # directly (they're plain functions; calling them with explicit
    # keyword args works the same as FastAPI resolving their Depends())
    # rather than re-querying DynamoDB a second, slightly different way.
    mitigations = list_mitigations(tenant_id=tenant_id, resource=resource)
    mitigation_rows = [
        {**m.model_dump(), "expires_at_display": _format_expiry(m.expires_at)} for m in mitigations
    ]
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    model = model_status(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "dashboard.html", {
        "tenant_id": tenant_id,
        "mitigations": mitigation_rows, "whitelist": whitelist.whitelisted_ips, "model": model,
    })


@router.post("/dashboard/ui/whitelist", response_class=HTMLResponse)
def add_whitelist_ui(request: Request, ip: str = Form(...), reason: str = Form(""),
                      tenant_id: str = Depends(dashboard_auth), resource=Depends(get_dynamo_resource)):
    error = None
    try:
        add_whitelist(WhitelistRequest(ip=ip, reason=reason), tenant_id=tenant_id, resource=resource)
    except ValidationError:
        error = f"“{ip}” is not a valid IP address."
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": whitelist.whitelisted_ips, "error": error,
    })


@router.delete("/dashboard/ui/whitelist/{ip}", response_class=HTMLResponse)
def remove_whitelist_ui(request: Request, ip: str, tenant_id: str = Depends(dashboard_auth),
                         resource=Depends(get_dynamo_resource)):
    remove_whitelist(ip, tenant_id=tenant_id, resource=resource)
    whitelist = list_whitelist(tenant_id=tenant_id, resource=resource)
    return templates.TemplateResponse(request, "_whitelist_table.html", {
        "whitelist": whitelist.whitelisted_ips,
    })
