import json
import logging
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from services.ai_engine.api.dependencies import InternalAuth
from services.ai_engine.core.config import settings
from services.ai_engine.core.http_client import worker_circuit_breaker
from services.ai_engine.core.redis_client import get_redis
from services.ai_engine.ml.feature_engineering import (
    FeatureVector,
    compute_features_for_batch,
)
from services.ai_engine.ml.model import AnomalyTier, classify_score, model_manager
from services.ai_engine.ml.monitoring import (
    ai_anomalies_detected_total,
    inference_duration_seconds,
    model_monitor,
    shadow_mode_active,
)
from services.ai_engine.ml.training import store_shadow_vector
from services.ai_engine.schemas.telemetry import TelemetryBatch, TelemetryResponse

logger = logging.getLogger(__name__)
router = APIRouter()

REASON_MAP = {
    AnomalyTier.RATE_LIMIT: "behavioral_anomaly_tier1",
    AnomalyTier.HARD_BLOCK: "behavioral_anomaly_tier2",
}


async def store_logs_to_window(
    logs: list,
    redis: aioredis.Redis,
) -> set[str]:
    now    = time.time()
    cutoff = now - settings.window_seconds
    unique_ips: set[str] = set()

    async with redis.pipeline(transaction=False) as pipe:
        for log in logs:
            key = f"window:{log.remote_addr}"
            unique_ips.add(log.remote_addr)
            await pipe.zadd(key, {json.dumps(log.model_dump()): now})
        await pipe.execute()

    for ip in unique_ips:
        key = f"window:{ip}"
        await redis.zremrangebyscore(key, "-inf", cutoff)
        await redis.expire(key, settings.window_ttl_seconds)

    return unique_ips


async def check_ip_reputation(
    ip: str,
    redis: aioredis.Redis,
) -> bool:
    is_known_bad = await redis.sismember(
        settings.reputation_redis_key, ip
    )
    return bool(is_known_bad)


def _log_feature_vectors(vectors: list[FeatureVector]) -> None:
    for v in vectors:
        logger.info(
            "Features: ip=%s rate=%.3f err=%.3f bytes=%.1f "
            "time=%.3f uri_ratio=%.3f ua_entropy=%.3f post=%.3f samples=%d",
            v.remote_addr,
            v.request_rate,
            v.error_ratio,
            v.avg_bytes_sent,
            v.avg_request_time,
            v.unique_uri_ratio,
            v.user_agent_entropy,
            v.post_ratio,
            v.sample_size,
        )


async def _trigger_mitigation(
    ip: str,
    tier: AnomalyTier,
    reason: str,
) -> bool:
    payload = {
        "target_ip": ip,
        "reason":    reason,
        "tier":      int(tier),
    }
    headers = {
        "X-Internal-Token": settings.internal_secret,
        "Content-Type":     "application/json",
    }

    success = await worker_circuit_breaker.call(
        url=f"{settings.worker_orchestrator_url}/api/v1/mitigate",
        payload=payload,
        headers=headers,
    )

    if success:
        logger.info(
            "Mitigation triggered: ip=%s tier=%d reason=%s",
            ip, int(tier), reason,
        )
    else:
        logger.warning(
            "Mitigation skipped (circuit breaker): ip=%s tier=%d",
            ip, int(tier),
        )

    return success


@router.post("/telemetry", response_model=TelemetryResponse)
async def ingest_telemetry(
    batch: TelemetryBatch,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> TelemetryResponse:
    if not batch.logs:
        return TelemetryResponse(
            received=0,
            processed_ips=0,
            message="Empty batch received",
        )

    logger.info("Telemetry batch received: size=%d", len(batch.logs))

    unique_ips = await store_logs_to_window(batch.logs, redis)

    reputation_blocked = 0
    clean_ips: set[str] = set()

    for ip in unique_ips:
        is_bad = await check_ip_reputation(ip, redis)
        if is_bad:
            logger.warning(
                "Known bad IP via reputation list: ip=%s", ip
            )
            await _trigger_mitigation(
                ip, AnomalyTier.HARD_BLOCK, "ip_reputation_blacklist"
            )
            ai_anomalies_detected_total.labels(
                reason="ip_reputation_blacklist",
                tier="2",
            ).inc()
            reputation_blocked += 1
        else:
            clean_ips.add(ip)

    feature_vectors = await compute_features_for_batch(clean_ips, redis)

    if not feature_vectors:
        return TelemetryResponse(
            received=len(batch.logs),
            processed_ips=reputation_blocked,
            message=(
                f"Reputation blocked: {reputation_blocked} | "
                f"No clean IPs with sufficient data"
            ),
        )

    _log_feature_vectors(feature_vectors)
    model_monitor.check_drift(feature_vectors)
    shadow_mode_active.set(1 if settings.shadow_mode else 0)

    mitigated_count = 0

    with inference_duration_seconds.time():
        scored_vectors = await model_manager.score_vectors(feature_vectors)

    scores  = [score for _, score in scored_vectors]
    from services.ai_engine.ml.registry import load_metadata
    meta    = load_metadata("production")
    version = meta.version if meta else "no_model"
    model_monitor.record_scores(scores, version)

    for vector, score in scored_vectors:
        tier = classify_score(score)

        await store_shadow_vector(
            remote_addr=vector.remote_addr,
            features=vector.to_list(),
            score=score,
            redis=redis,
        )

        if tier == AnomalyTier.NORMAL:
            logger.debug(
                "Normal traffic: ip=%s score=%.4f",
                vector.remote_addr, score,
            )
            continue

        reason = REASON_MAP[tier]
        logger.warning(
            "Anomaly: ip=%s score=%.4f tier=%d reason=%s shadow=%s",
            vector.remote_addr, score, int(tier), reason, settings.shadow_mode,
        )

        ai_anomalies_detected_total.labels(
            reason=reason,
            tier=str(int(tier)),
        ).inc()

        if not settings.shadow_mode:
            success = await _trigger_mitigation(vector.remote_addr, tier, reason)
            if success:
                model_monitor.record_mitigation(int(tier))
                mitigated_count += 1

    return TelemetryResponse(
        received=len(batch.logs),
        processed_ips=len(feature_vectors) + reputation_blocked,
        message=(
            f"Reputation blocked: {reputation_blocked} | "
            f"ML processed: {len(feature_vectors)} | "
            f"Mitigated: {mitigated_count} | "
            f"Circuit: {worker_circuit_breaker.state.value} | "
            f"Mode: {'shadow' if settings.shadow_mode else 'live'}"
        ),
    )
