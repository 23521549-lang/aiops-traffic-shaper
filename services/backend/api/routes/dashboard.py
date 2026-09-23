from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.ui.csrf import verify_csrf_if_cookie_auth
from services.backend.core.tables import MitigationStateTable, ModelsTable, WhitelistTable
from services.backend.schemas.mitigation import MitigationState
from services.backend.schemas.whitelist import WhitelistEntry, WhitelistRequest
from services.backend.schemas.model_status import ModelStatus

router = APIRouter()


@router.get("/dashboard/v1/mitigations", response_model=list[MitigationState])
def list_mitigations(tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)) -> list[MitigationState]:
    items = MitigationStateTable(resource).query_active(tenant_id)
    return [MitigationState(**item) for item in items]


@router.get("/dashboard/v1/whitelist", response_model=WhitelistEntry)
def list_whitelist(tenant_id: str = Depends(dashboard_auth),
                    resource=Depends(get_dynamo_resource)) -> WhitelistEntry:
    items = WhitelistTable(resource).query_by_tenant(tenant_id)
    return WhitelistEntry(whitelisted_ips=[i["ip"] for i in items])


@router.post("/dashboard/v1/whitelist", dependencies=[Depends(verify_csrf_if_cookie_auth)])
def add_whitelist(body: WhitelistRequest, tenant_id: str = Depends(dashboard_auth),
                   resource=Depends(get_dynamo_resource)) -> dict:
    # NOTE: docs/api-contract.md's WhitelistRequest carries a `reason`
    # field that docs/schema.md's Whitelist table never defined (only
    # added_at/added_by) — a doc inconsistency found while implementing
    # this route. Resolved by storing `reason` as an extra attribute
    # (DynamoDB doesn't require a fixed attribute set) rather than
    # silently dropping it or misusing added_at to hold it.
    WhitelistTable(resource).put(
        tenant_id=tenant_id, ip=body.ip,
        added_at=datetime.now(timezone.utc).isoformat(), reason=body.reason,
    )
    # Whitelisting has to UNDO the block, not merely prevent the next one.
    # The whitelist is consulted at scoring time (api/routes/agent.py), so
    # without this delete the already-written MitigationState row survives:
    # /agent/v1/decisions keeps serving it, the customer's nginx keeps the
    # deny in place for up to an hour, and the dashboard keeps listing it —
    # all after the UI said "added to whitelist". This is the only lever the
    # product gives a customer for "you got this one wrong", and it reported
    # success while changing nothing they could observe.
    #
    # Scoped to (tenant_id, ip), which is the table's full key, so one
    # tenant's decision cannot clear another's. Deleting a key that is not
    # there is a no-op in DynamoDB — the common case is pre-approving an IP
    # that was never blocked.
    MitigationStateTable(resource).delete(tenant_id=tenant_id, ip=body.ip)
    return {"message": f"{body.ip} added to whitelist"}


@router.delete("/dashboard/v1/whitelist/{ip}", dependencies=[Depends(verify_csrf_if_cookie_auth)])
def remove_whitelist(ip: str, tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)) -> dict:
    table = WhitelistTable(resource)
    if table.get(tenant_id=tenant_id, ip=ip) is None:
        raise HTTPException(status_code=404, detail="IP not in whitelist")
    table.delete(tenant_id=tenant_id, ip=ip)
    return {"message": f"{ip} removed from whitelist"}


@router.get("/dashboard/v1/model/status", response_model=ModelStatus)
def model_status(tenant_id: str = Depends(dashboard_auth),
                  resource=Depends(get_dynamo_resource)) -> ModelStatus:
    item = ModelsTable(resource).get(tenant_id=tenant_id, stage_version="production")
    if item is None:
        return ModelStatus(model_ready=False, shadow_mode=True)
    return ModelStatus(
        model_ready=True,
        shadow_mode=False,
        version=item.get("version"),
        trained_at=item.get("trained_at"),
        training_samples=int(item["training_samples"]) if "training_samples" in item else None,
        contamination=float(item["contamination"]) if "contamination" in item else None,
        score_mean=float(item["score_mean"]) if "score_mean" in item else None,
        score_std=float(item["score_std"]) if "score_std" in item else None,
    )
