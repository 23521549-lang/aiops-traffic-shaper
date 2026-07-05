import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from services.ai_engine.api.dependencies import InternalAuth
from services.ai_engine.core.config import settings
from services.ai_engine.core.redis_client import get_redis
from services.ai_engine.schemas.mitigation import WhitelistRequest

logger = logging.getLogger(__name__)
router = APIRouter()

WHITELIST_KEY        = settings.whitelist_redis_key
TRAINING_EXCLUDE_KEY = "training:excluded_ips"


@router.post("/whitelist")
async def add_to_whitelist(
    request: WhitelistRequest,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    await redis.sadd(WHITELIST_KEY, request.ip)
    await redis.sadd(TRAINING_EXCLUDE_KEY, request.ip)

    logger.info(
        "IP %s added to whitelist and training exclusion list, reason: %s",
        request.ip,
        request.reason,
    )
    return {
        "message": (
            f"IP {request.ip} added to whitelist "
            f"and excluded from future training data"
        )
    }


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
    ips          = await redis.smembers(WHITELIST_KEY)
    excluded_ips = await redis.smembers(TRAINING_EXCLUDE_KEY)
    return {
        "whitelisted_ips":    sorted(ips),
        "training_excluded":  sorted(excluded_ips),
    }


@router.delete("/whitelist/{ip}/training-exclusion")
async def remove_from_training_exclusion(
    ip: str,
    _: None = InternalAuth,
    redis: aioredis.Redis = Depends(get_redis),
) -> dict[str, str]:
    removed = await redis.srem(TRAINING_EXCLUDE_KEY, ip)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IP {ip} not found in training exclusion list",
        )
    logger.info(
        "IP %s removed from training exclusion list "
        "(will be included in future training)",
        ip,
    )
    return {
        "message": (
            f"IP {ip} removed from training exclusion. "
            f"Will be included in next retrain."
        )
    }
