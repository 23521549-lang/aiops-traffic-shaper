import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.ui.csrf import verify_csrf_if_cookie_auth
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, ModelsTable, TenantHistoryTable,
    WhitelistTable,
)
from services.backend.schemas.admin import AgentSummary
from services.backend.schemas.mitigation import MitigationState
from services.backend.schemas.history import HourlyPoint, MitigationEpisode
from services.backend.schemas.whitelist import WhitelistEntry, WhitelistRequest
from services.backend.schemas.model_status import ModelStatus

router = APIRouter()


@router.get("/dashboard/v1/mitigations", response_model=list[MitigationState])
def list_mitigations(tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)) -> list[MitigationState]:
    items = MitigationStateTable(resource).query_active(tenant_id)
    return [MitigationState(**item) for item in items]


@router.get("/dashboard/v1/agents", response_model=list[AgentSummary])
def list_own_agents(tenant_id: str = Depends(dashboard_auth),
                    resource=Depends(get_dynamo_resource)) -> list[AgentSummary]:
    """The tenant's own agents.

    One Query on the base table's own partition key. `AgentSummary` is
    reused rather than a raw dict: its closed field list is what keeps
    `api_key_hash` server-side, and the shape then matches what the
    publisher console already consumes.

    Liveness is derived by the caller from `last_seen_at`. `LastSeenIndex`
    is partitioned on `status` and is cross-tenant, so it cannot answer
    "which of MY agents"; a per-tenant liveness GSI would mirror every
    agent write against the account-wide 25 WCU pool to save a loop over
    single digits.
    """
    items = AgentsTable(resource).query_by_tenant(tenant_id)
    out = []
    for item in items:
        try:
            out.append(AgentSummary(**item))
        except ValidationError:
            # An agent row written before a field existed must not 500 the
            # page. Same rule the tenant list learned the hard way.
            continue
    return sorted(out, key=lambda a: a.last_seen_at, reverse=True)


@router.get("/dashboard/v1/whitelist", response_model=WhitelistEntry)
def list_whitelist(tenant_id: str = Depends(dashboard_auth),
                    resource=Depends(get_dynamo_resource)) -> WhitelistEntry:
    items = WhitelistTable(resource).query_by_tenant(tenant_id)
    return WhitelistEntry(
        whitelisted_ips=[i["ip"] for i in items],
        # The form has always collected a reason and stored it; nothing ever
        # read it back, so the field hint promised a column that could not
        # exist. Carried here rather than in a second call because the rows
        # are already in memory.
        entries=[{"ip": i["ip"], "reason": i.get("reason", ""),
                  "added_at": i.get("added_at", "")} for i in items],
    )


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
    # Projected: the blob is ~238 KB and everything below is a scalar. A bare
    # get() here was ~30 RCU against a table provisioned at 2, to print a
    # version string.
    item = ModelsTable(resource).get_metadata(tenant_id=tenant_id,
                                              stage_version="production")
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


# --- history ------------------------------------------------------------

# A window cap, not a UX preference. Thirty days of a busy tenant is ~700
# episode rows plus 720 hourly rows, and one such Query is ~15 eventually
# consistent RCU against a table provisioned at 2. Refusing with a message
# that names the cap is better than truncating silently, which is how the
# retrain ended up training on a partial window for months.
MAX_HISTORY_DAYS = 7
_MAX_WINDOW = MAX_HISTORY_DAYS * 86_400


def _window(since: int | None, until: int | None) -> tuple[int, int]:
    now = int(time.time())
    until = until if until is not None else now
    since = since if since is not None else until - 86_400
    if since > until:
        raise HTTPException(status_code=400, detail="`since` must be before `until`")
    if until - since > _MAX_WINDOW:
        raise HTTPException(
            status_code=400,
            detail=f"History window is limited to {MAX_HISTORY_DAYS} days")
    return since, until


@router.get("/dashboard/v1/history", response_model=list[MitigationEpisode])
def list_history(since: int | None = None, until: int | None = None,
                 ip: str | None = None,
                 tenant_id: str = Depends(dashboard_auth),
                 resource=Depends(get_dynamo_resource)) -> list[MitigationEpisode]:
    """What this product did, and when. PRD US-6's second acceptance
    criterion, unmeetable until TenantHistory existed."""
    since, until = _window(since, until)
    table = TenantHistoryTable(resource)
    # Absent marker means the tenant has never looked; treating that as
    # epoch 0 would present the whole window as unread the first time the
    # feature speaks.
    read_through = table.unread_since(tenant_id, default_ts=since)

    episodes = []
    for row in table.query_episodes(tenant_id, since, until):
        if ip and row.get("ip") != ip:
            continue
        episodes.append(MitigationEpisode(
            ip=row["ip"],
            hour_start=int(row["hour_start"]),
            first_ts=int(row.get("first_ts", row["hour_start"])),
            last_ts=int(row.get("last_ts", row["hour_start"])),
            tier1_count=int(row.get("tier1_count", 0)),
            tier2_count=int(row.get("tier2_count", 0)),
            last_score=float(row["last_score"]) if row.get("last_score") is not None else None,
            last_z=float(row["last_z"]) if row.get("last_z") is not None else None,
            reason=row.get("reason", "behavioral_anomaly"),
            is_new=int(row["hour_start"]) >= read_through,
        ))
    return episodes


@router.get("/dashboard/v1/series", response_model=list[HourlyPoint])
def list_series(since: int | None = None, until: int | None = None,
                tenant_id: str = Depends(dashboard_auth),
                resource=Depends(get_dynamo_resource)) -> list[HourlyPoint]:
    since, until = _window(since, until)
    rows = TenantHistoryTable(resource).query_series(tenant_id, since, until, fill=True)
    return [HourlyPoint(
        hour_start=int(r["hour_start"]),
        requests=int(r.get("requests", 0)),
        batches=int(r.get("batches", 0)),
        tier1_decisions=int(r.get("tier1_decisions", 0)),
        tier2_decisions=int(r.get("tier2_decisions", 0)),
    ) for r in rows]


@router.post("/dashboard/v1/history/mark-read",
             dependencies=[Depends(verify_csrf_if_cookie_auth)])
def mark_history_read(through_ts: int | None = None,
                      tenant_id: str = Depends(dashboard_auth),
                      resource=Depends(get_dynamo_resource)) -> dict:
    """Idempotent, and never moves backwards: two tabs or a retried request
    must not re-announce what the customer has already seen."""
    through = through_ts if through_ts is not None else int(time.time())
    TenantHistoryTable(resource).mark_read(tenant_id, through)
    return {"last_read_ts": TenantHistoryTable(resource).unread_since(tenant_id, through)}
