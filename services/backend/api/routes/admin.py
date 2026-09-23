from fastapi import APIRouter, Depends, HTTPException

from services.backend.api.cognito_auth import admin_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TelemetryEventsTable, TenantsTable
from services.backend.schemas.admin import AgentSummary, Tenant

router = APIRouter()


@router.get("/admin/v1/tenants", response_model=list[Tenant], dependencies=[Depends(admin_auth)])
def list_tenants(resource=Depends(get_dynamo_resource)) -> list[Tenant]:
    return [Tenant(**item) for item in TenantsTable(resource).list_all()]


@router.get("/admin/v1/agents", response_model=list[AgentSummary], dependencies=[Depends(admin_auth)])
def list_agents(status: str = "stale", resource=Depends(get_dynamo_resource)) -> list[AgentSummary]:
    # Cross-tenant listing via AgentsTable.query_by_status(), which uses
    # the LastSeenIndex GSI — schema.md's own stated purpose for that GSI
    # is finding e.g. stale/dead agents across every tenant, hence the
    # "stale" default rather than trying to list "all" agents (which the
    # GSI can't do in one query since its partition key IS status).
    return [AgentSummary(**item) for item in AgentsTable(resource).query_by_status(status)]


@router.post("/admin/v1/tenants/{tenant_id}/suspend", dependencies=[Depends(admin_auth)])
def suspend_tenant(tenant_id: str, resource=Depends(get_dynamo_resource)) -> dict:
    if not TenantsTable(resource).suspend(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Phase 4 / H3: suspension used to flip one attribute nobody read. It now
    # also revokes every API key already issued to this tenant's agents —
    # otherwise those keys keep working, since agent keys have no expiry.
    revoked = AgentsTable(resource).revoke_all_for_tenant(tenant_id)
    return {"message": f"tenant {tenant_id} suspended", "agents_revoked": revoked}


@router.post("/admin/v1/tenants/{tenant_id}/reactivate", dependencies=[Depends(admin_auth)])
def reactivate_tenant(tenant_id: str, resource=Depends(get_dynamo_resource)) -> dict:
    """Undo a suspension. Until this existed suspension was one-way, short of
    editing DynamoDB by hand.

    The tenant comes back; its old agent keys do not. suspend_tenant revoked
    them because the reason for suspending may be a leaked key, so the tenant
    registers fresh agents to get fresh keys. The response says so, because an
    operator reactivating a tenant will otherwise reasonably expect its agents
    to start reporting again on their own - and they will not."""
    if not TenantsTable(resource).reactivate(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "message": f"tenant {tenant_id} reactivated",
        "note": "agent keys revoked at suspension stay revoked; register agents again",
    }


@router.post("/admin/v1/tenants/{tenant_id}/training-exclude/{ip}",
             dependencies=[Depends(admin_auth)])
def exclude_ip_from_training(tenant_id: str, ip: str,
                             resource=Depends(get_dynamo_resource)) -> dict:
    """Stop the nightly retrain learning from this IP's traffic.

    Flagging at decision time keeps an attacker from teaching the model, but
    it cannot undo a model that has already learned them: a poisoned model
    stops detecting the attack, so it stops flagging it, and the next retrain
    learns it again. Observed on production - an attack scoring -0.204 drew no
    decision at all two days later. This is the way out.

    The inverse of the whitelist, and deliberately not the same thing: the
    whitelist exempts an IP from MITIGATION, this exempts one from TRAINING.
    The IP is still scored and still blocked."""
    excluded = TelemetryEventsTable(resource).flag_all_for_ip(tenant_id, ip)
    return {
        "message": f"{ip} excluded from training for tenant {tenant_id}",
        "buckets_excluded": excluded,
        "note": "retrain to rebuild the model without this traffic",
    }
