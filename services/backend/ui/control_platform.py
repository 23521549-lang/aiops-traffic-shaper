from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from services.backend.api.cognito_auth import admin_auth
from services.backend.api.routes.admin import list_agents, list_tenants, suspend_tenant
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.usage import get_usage_report
from services.backend.ui.templates_env import templates

router = APIRouter()


@router.get("/admin/ui", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
def control_platform_page(request: Request, resource=Depends(get_dynamo_resource)):
    tenants = list_tenants(resource=resource)
    agents = list_agents(status="stale", resource=resource)
    usage = get_usage_report(resource, date=None)
    return templates.TemplateResponse(request, "control_platform.html", {
        "tenants": tenants, "agents": agents, "agent_status": "stale", "usage": usage,
    })


@router.get("/admin/ui/agents", response_class=HTMLResponse, dependencies=[Depends(admin_auth)])
def agents_partial(request: Request, status: str = "stale", resource=Depends(get_dynamo_resource)):
    agents = list_agents(status=status, resource=resource)
    return templates.TemplateResponse(request, "_agents_table.html", {"agents": agents})


@router.post("/admin/ui/tenants/{tenant_id}/suspend", response_class=HTMLResponse,
             dependencies=[Depends(admin_auth)])
def suspend_tenant_ui(request: Request, tenant_id: str, resource=Depends(get_dynamo_resource)):
    suspend_tenant(tenant_id, resource=resource)
    tenants = list_tenants(resource=resource)
    return templates.TemplateResponse(request, "_tenants_table.html", {"tenants": tenants})
