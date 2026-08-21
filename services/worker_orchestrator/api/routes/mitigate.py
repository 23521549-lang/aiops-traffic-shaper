import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from services.worker_orchestrator.api.dependencies import InternalAuth
from services.worker_orchestrator.core.config import settings
from services.worker_orchestrator.core.redis_client import get_redis
from services.worker_orchestrator.orchestrator.configmap_patcher import ConfigMapPatcher
from services.worker_orchestrator.orchestrator.metrics import (
    record_mitigation_cost,
    update_mitigation_metrics,
)
from services.worker_orchestrator.orchestrator.nginx_reloader import NginxReloader
from services.worker_orchestrator.schemas.mitigation import (
    MitigationRequest,
    MitigationResponse,
    MitigationTier,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_patcher = ConfigMapPatcher(
    namespace=settings.namespace,
    configmap_name=settings.blocklist_configmap,
    rule_template="deny {ip};",
)
_ratelimit_patcher = ConfigMapPatcher(
    namespace=settings.namespace,
    configmap_name=settings.ratelimit_configmap,
    rule_template="{ip} 1;",
)
_reloader = NginxReloader()


async def _apply_mitigation(
    redis: aioredis.Redis,
    patcher: ConfigMapPatcher,
    mitigation_key: str,
    target_ip: str,
    state: str,
    ttl_seconds: int,
) -> None:
    existing = await redis.get(mitigation_key)
    if existing != state:
        patcher.add_rule(target_ip)
        _reloader.trigger_reload()

    await redis.set(mitigation_key, state, ex=ttl_seconds)


@router.post("/mitigate", response_model=MitigationResponse)
async def mitigate(
    request: MitigationRequest,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> MitigationResponse:
    is_whitelisted = await redis.sismember(
        settings.whitelist_key, request.target_ip
    )
    if is_whitelisted:
        logger.info(
            "IP %s is whitelisted, skipping mitigation", request.target_ip
        )
        return MitigationResponse(
            target_ip=request.target_ip,
            tier=request.tier,
            action="skipped",
            ttl_seconds=0,
            whitelisted=True,
            message=f"IP {request.target_ip} is whitelisted, no action taken",
        )

    mitigation_key = f"{settings.mitigation_key_prefix}:{request.target_ip}"

    if request.tier == MitigationTier.RATE_LIMIT:
        action = "rate_limited"
        ttl = settings.tier1_ttl_seconds
        await _apply_mitigation(
            redis, _ratelimit_patcher, mitigation_key, request.target_ip, action, ttl
        )
        logger.info(
            "Tier 1 mitigation applied: ip=%s reason=%s ttl=%ss",
            request.target_ip,
            request.reason,
            ttl,
        )

    else:
        action = "blocked"
        ttl = settings.tier2_ttl_seconds
        await _apply_mitigation(
            redis, _patcher, mitigation_key, request.target_ip, action, ttl
        )
        logger.info(
            "Tier 2 mitigation applied: ip=%s reason=%s ttl=%ss",
            request.target_ip,
            request.reason,
            ttl,
        )

    record_mitigation_cost()
    await update_mitigation_metrics(redis)

    return MitigationResponse(
        target_ip=request.target_ip,
        tier=request.tier,
        action=action,
        ttl_seconds=ttl,
        whitelisted=False,
        message=f"Mitigation applied: {action} for {request.target_ip}",
    )
