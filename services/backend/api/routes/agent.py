import logging
import secrets
import time
import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

from fastapi import APIRouter, Depends

from services.backend.api.cognito_auth import dashboard_auth
from services.backend.api.dependencies import (
    agent_auth,
    assert_tenant_active,
    enforce_tenant_quota,
    enforce_usage_ceiling,
    hash_api_key,
)
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable,
    MitigationStateTable,
    TenantHistoryTable,
    TelemetryEventsTable,
    WhitelistTable,
)
from services.backend.core.usage import record_tenant_ingest
from services.backend.ml.feature_engineering import (
    BUCKET_SECONDS,
    _bucket_start,
    compute_features_for_ip,
    record_batch,
)
from services.backend.ml.model import AnomalyTier, ModelManager, classify, z_score
from services.backend.schemas.agent_register import AgentRegisterRequest, AgentRegisterResponse
from services.backend.schemas.mitigation import MitigationState
from services.backend.schemas.telemetry import TelemetryBatch, TelemetryResponse

def _try_history(fn, *args, **kwargs) -> None:
    """Run a history write, swallow its failure, keep the request alive.

    History and charts are reporting. Ingest is the product: it is the call
    that returns the decisions a customer's nginx enforces. DynamoDB
    throttles rather than bills when a table exceeds provisioned capacity,
    and an attack is simultaneously when the episode write is burstiest and
    when the agent most needs its decisions back. It is also what makes the
    rollout order forgiving - if the Lambda ships before Terraform creates
    the table, every batch would otherwise 500.
    """
    try:
        fn(*args, **kwargs)
    except (ClientError, BotoCoreError):
        logger.warning("history write failed (%s); ingest continues",
                       getattr(fn, "__name__", fn), exc_info=True)


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/agent/v1/register", response_model=AgentRegisterResponse)
def register_agent(
    body: AgentRegisterRequest,
    tenant_id: str = Depends(dashboard_auth),  # the tenant owner, not the agent itself — the
    # agent has no credentials yet at this point, that's the whole point of this endpoint
    resource=Depends(get_dynamo_resource),
) -> AgentRegisterResponse:
    # H3 bypass path: the tenant's dashboard JWT stays valid until it expires,
    # so without this a just-suspended tenant could simply mint a fresh agent.
    assert_tenant_active(resource, tenant_id)

    agent_id = uuid.uuid4().hex
    raw_api_key = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()

    AgentsTable(resource).put(
        tenant_id=tenant_id, agent_id=agent_id, agent_label=body.agent_label,
        registered_at=now, last_seen_at=now, agent_version="unknown",
        api_key_hash=hash_api_key(raw_api_key), status="active",
    )

    return AgentRegisterResponse(tenant_id=tenant_id, agent_id=agent_id, api_key=raw_api_key)

# Found while designing Stage 8's agent enforcer: this route previously
# hardcoded expires_at=0 for every decision, so an agent enforcing these
# locally would have no real TTL to schedule an auto-unblock from — every
# block would be effectively permanent until manually cleared. Real,
# tier-dependent expiry, computed here where the tier is decided.
_TTL_SECONDS = {
    AnomalyTier.RATE_LIMIT: 300,     # 5 min — soft, re-evaluated often
    AnomalyTier.HARD_BLOCK: 3600,    # 1 hour — harder, longer cooldown
}


@router.post("/agent/v1/telemetry", response_model=TelemetryResponse,
             dependencies=[Depends(enforce_usage_ceiling)])
def ingest_telemetry(
    batch: TelemetryBatch,
    tenant_id: str = Depends(enforce_tenant_quota),
    resource=Depends(get_dynamo_resource),
) -> TelemetryResponse:
    if not batch.logs:
        return TelemetryResponse(received=0, processed_ips=0, decisions=[])

    # Counted only once the request has been accepted: a refused batch must
    # not spend the quota that refused it.
    record_tenant_ingest(resource, tenant_id)

    # `now` is passed rather than left to default so the route knows which
    # bucket was just written, and can flag exactly that one below.
    now = time.time()
    touched_ips = record_batch(resource, tenant_id, batch.logs, now=now)
    bucket = _bucket_start(BUCKET_SECONDS, now)
    history = TenantHistoryTable(resource)
    hour_start = TenantHistoryTable.hour_of(int(now))
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
            tier = classify(score, mgr.stats)
            if tier == AnomalyTier.NORMAL:
                continue
            state = MitigationState(
                ip=v.remote_addr, tier=int(tier), score=score,
                # Recorded here, not recomputed at render time: the nightly
                # retrain moves score_mean/score_std, so a page rendered
                # tomorrow would otherwise show a different figure from the
                # one that caused this decision. mgr.stats is already in
                # memory for classify() — no extra read.
                z=z_score(score, mgr.stats),
                features=v.to_list(),
                reason="behavioral_anomaly",
                expires_at=int(time.time()) + _TTL_SECONDS[tier],
            )
            MitigationStateTable(resource).put(tenant_id=tenant_id, **state.model_dump())
            # Keep this bucket out of the nightly retrain. Training on traffic
            # this system just judged hostile is how an attacker teaches the
            # model to accept them: three flagged buckets in the 25-hour window
            # were enough to flip a caught attacker to NORMAL in every baseline
            # tested. See test_training_poisoning.py. One extra write, and only
            # for IPs that were actually anomalous.
            TelemetryEventsTable(resource).mark_flagged(tenant_id, v.remote_addr, bucket)
            # The product's memory. Wrapped because this is reporting and
            # ingest is the product: a throttled history write during an
            # attack must not stop the agent receiving the decision it needs
            # to enforce. See test_history_on_ingest.py.
            _try_history(history.record_decision, tenant_id, v.remote_addr,
                         hour_start=hour_start, tier=int(tier), now=int(now),
                         score=score, z=state.z)
            decisions.append(state)

    # One atomic ADD per batch, not per IP. This is the only unconditional
    # write this feature adds to the ingest path, and the ingest quota caps
    # the whole account at ~0.39 batches/second, so it is ~0.39 WCU
    # sustained against a table provisioned at 2.
    _try_history(history.record_traffic, tenant_id, hour_start=hour_start,
                 requests=len(batch.logs),
                 tier1=sum(1 for d in decisions if d.tier == 1),
                 tier2=sum(1 for d in decisions if d.tier >= 2))

    # One Query, ~1 RCU for a typical twenty rows. At the account-wide ingest
    # ceiling of ~0.39 batches/second that is ~0.39 RCU sustained, inside the
    # 11 RCU of headroom. It is what lets the agent stop enforcing a block a
    # human has lifted, instead of holding it for up to an hour — `decisions`
    # cannot do that job, because it only covers IPs with traffic in this
    # batch and a blocked IP stops sending traffic.
    active = MitigationStateTable(resource).query_active(tenant_id)

    return TelemetryResponse(
        received=len(batch.logs), processed_ips=len(touched_ips), decisions=decisions,
        active_ips=[item["ip"] for item in active],
    )


@router.get("/agent/v1/decisions", response_model=list[MitigationState])
def list_decisions(
    tenant_id: str = Depends(agent_auth),
    resource=Depends(get_dynamo_resource),
) -> list[MitigationState]:
    items = MitigationStateTable(resource).query_active(tenant_id)
    return [MitigationState(**item) for item in items]
