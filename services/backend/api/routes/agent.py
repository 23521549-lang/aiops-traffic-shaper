from fastapi import APIRouter, Depends

from services.backend.api.dependencies import agent_auth
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import MitigationStateTable, WhitelistTable
from services.backend.ml.feature_engineering import compute_features_for_ip, record_batch
from services.backend.ml.model import AnomalyTier, ModelManager, classify_score
from services.backend.schemas.mitigation import MitigationState
from services.backend.schemas.telemetry import TelemetryBatch, TelemetryResponse

router = APIRouter()


@router.post("/agent/v1/telemetry", response_model=TelemetryResponse)
def ingest_telemetry(
    batch: TelemetryBatch,
    tenant_id: str = Depends(agent_auth),
    resource=Depends(get_dynamo_resource),
) -> TelemetryResponse:
    if not batch.logs:
        return TelemetryResponse(received=0, processed_ips=0, decisions=[])

    touched_ips = record_batch(resource, tenant_id, batch.logs)
    whitelist = {i["ip"] for i in WhitelistTable(resource).query_by_tenant(tenant_id)}

    mgr = ModelManager()
    mgr.load(resource, tenant_id)  # cached across warm invocations, see docs/PLAN.md Stage 3 —
    # do NOT "simplify" this back to an unconditional registry.load_model() call

    decisions: list[MitigationState] = []
    for ip in touched_ips:
        if ip in whitelist:
            continue
        vector = compute_features_for_ip(resource, tenant_id, ip)
        if vector is None:
            continue
        for v, score in mgr.score_vectors([vector]):
            tier = classify_score(score)
            if tier == AnomalyTier.NORMAL:
                continue
            state = MitigationState(
                ip=v.remote_addr, tier=int(tier), score=score,
                reason="behavioral_anomaly", expires_at=0,
            )
            MitigationStateTable(resource).put(tenant_id=tenant_id, **state.model_dump())
            decisions.append(state)

    return TelemetryResponse(
        received=len(batch.logs), processed_ips=len(touched_ips), decisions=decisions,
    )


@router.get("/agent/v1/decisions", response_model=list[MitigationState])
def list_decisions(
    tenant_id: str = Depends(agent_auth),
    resource=Depends(get_dynamo_resource),
) -> list[MitigationState]:
    items = MitigationStateTable(resource).query_by_tenant(tenant_id)
    return [MitigationState(**item) for item in items]
