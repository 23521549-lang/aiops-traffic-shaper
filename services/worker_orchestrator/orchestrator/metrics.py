import logging

import redis.asyncio as aioredis
from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

nginx_blocked_ips_total = Gauge(
    "nginx_blocked_ips_total",
    "Number of IPs currently under hard block (Tier 2)",
)

nginx_rate_limited_ips_total = Gauge(
    "nginx_rate_limited_ips_total",
    "Number of IPs currently under rate limiting (Tier 1)",
)

estimated_cloud_cost_saved_usd = Counter(
    "estimated_cloud_cost_saved_usd",
    "Estimated cloud cost saved in USD by blocking malicious requests",
)

COST_PER_BLOCKED_REQUEST_USD = 0.00005
BLOCKED_REQUESTS_ESTIMATE_PER_MITIGATION = 100


async def update_mitigation_metrics(redis: aioredis.Redis) -> None:
    blocked_count = 0
    rate_limited_count = 0
    cursor = 0

    while True:
        cursor, keys = await redis.scan(
            cursor=cursor, match="mitigation:*", count=100
        )
        for key in keys:
            value = await redis.get(key)
            if value == "blocked":
                blocked_count += 1
            elif value == "rate_limited":
                rate_limited_count += 1
        if cursor == 0:
            break

    nginx_blocked_ips_total.set(blocked_count)
    nginx_rate_limited_ips_total.set(rate_limited_count)

    logger.debug(
        "Metrics updated: blocked=%d rate_limited=%d",
        blocked_count,
        rate_limited_count,
    )


def record_mitigation_cost() -> None:
    amount = (
        BLOCKED_REQUESTS_ESTIMATE_PER_MITIGATION
        * COST_PER_BLOCKED_REQUEST_USD
    )
    estimated_cloud_cost_saved_usd.inc(amount)
