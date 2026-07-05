import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from services.worker_orchestrator.api.dependencies import InternalAuth
from services.worker_orchestrator.core.redis_client import get_redis
from services.worker_orchestrator.orchestrator.configmap_patcher import ConfigMapPatcher
from services.worker_orchestrator.orchestrator.nginx_reloader import NginxReloader
from services.worker_orchestrator.schemas.mitigation import (
    BlocklistEntry,
    WhitelistRequest,
)
from services.worker_orchestrator.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_patcher = ConfigMapPatcher(
    namespace=settings.namespace,
    configmap_name=settings.blocklist_configmap,
)
_reloader = NginxReloader()

WHITELIST_KEY = settings.whitelist_key


@router.get("/blocklist", response_model=list[BlocklistEntry])
async def get_blocklist(
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> list[BlocklistEntry]:
    entries: list[BlocklistEntry] = []
    cursor = 0

    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="mitigation:*", count=100)
        for key in keys:
            ip = key.removeprefix("mitigation:")
            value = await redis.get(key)
            ttl = await redis.ttl(key)
            if value == "blocked":
                entries.append(BlocklistEntry(ip=ip, tier=2, ttl_remaining=ttl))
            elif value == "rate_limited":
                entries.append(BlocklistEntry(ip=ip, tier=1, ttl_remaining=ttl))
        if cursor == 0:
            break

    return entries


@router.delete("/blocklist/{ip}")
async def unblock_ip(
    ip: str,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    mitigation_key = f"mitigation:{ip}"
    exists = await redis.exists(mitigation_key)

    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active mitigation found for IP {ip}",
        )

    await redis.delete(mitigation_key)
    _patcher.remove_deny_rule(ip)
    _reloader.trigger_reload()

    logger.info("Manual unblock applied for IP: %s", ip)
    return {"message": f"IP {ip} unblocked successfully"}


@router.post("/whitelist")
async def add_to_whitelist(
    request: WhitelistRequest,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    await redis.sadd(WHITELIST_KEY, request.ip)
    logger.info("IP %s added to whitelist, reason: %s", request.ip, request.reason)
    return {"message": f"IP {request.ip} added to whitelist"}


@router.delete("/whitelist/{ip}")
async def remove_from_whitelist(
    ip: str,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    removed = await redis.srem(WHITELIST_KEY, ip)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IP {ip} not found in whitelist",
        )
    logger.info("IP %s removed from whitelist", ip)
    return {"message": f"IP {ip} removed from whitelist"}


@router.get("/whitelist")
async def get_whitelist(
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, list[str]]:
    ips = await redis.smembers(WHITELIST_KEY)
    return {"whitelisted_ips": sorted(ips)}
