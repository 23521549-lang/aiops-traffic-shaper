import logging

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.worker_orchestrator.core.config import settings
from services.worker_orchestrator.core.redis_client import get_redis
from services.worker_orchestrator.orchestrator.configmap_patcher import ConfigMapPatcher
from services.worker_orchestrator.orchestrator.metrics import update_mitigation_metrics

logger = logging.getLogger(__name__)


async def cleanup_expired_mitigations() -> None:
    logger.info("Running mitigation cleanup job")

    redis: aioredis.Redis = await get_redis()
    patcher = ConfigMapPatcher(
        namespace=settings.namespace,
        configmap_name=settings.blocklist_configmap,
    )

    cursor = 0
    removed_count = 0

    while True:
        cursor, keys = await redis.scan(
            cursor=cursor, match="mitigation:*", count=100
        )

        for key in keys:
            ttl = await redis.ttl(key)
            if ttl == -2:
                ip = key.removeprefix("mitigation:")
                removed = patcher.remove_deny_rule(ip)
                if removed:
                    removed_count += 1
                    logger.info(
                        "Cleanup: removed expired block for IP %s", ip
                    )

        if cursor == 0:
            break

    await update_mitigation_metrics(redis)

    if removed_count > 0:
        logger.info(
            "Cleanup job completed: removed %d expired rules", removed_count
        )
    else:
        logger.info("Cleanup job completed: no expired rules found")


def start_cleanup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        cleanup_expired_mitigations,
        trigger="interval",
        seconds=settings.cleanup_interval_seconds,
        id="cleanup_expired_mitigations",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Cleanup scheduler started: interval=%ss",
        settings.cleanup_interval_seconds,
    )
    return scheduler
