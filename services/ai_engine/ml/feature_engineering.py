import json
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from services.ai_engine.core.config import settings
from services.ai_engine.core.feature_config import get_enabled_feature_names

logger = logging.getLogger(__name__)


@dataclass
class FeatureVector:
    remote_addr:        str
    request_rate:       float
    error_ratio:        float
    avg_bytes_sent:     float
    avg_request_time:   float
    unique_uri_ratio:   float
    user_agent_entropy: float
    post_ratio:         float
    sample_size:        int

    def to_list(self) -> list[float]:
        enabled = get_enabled_feature_names()
        return [getattr(self, name) for name in enabled]

    def to_dict(self) -> dict[str, float | str | int]:
        return {
            "remote_addr":        self.remote_addr,
            "request_rate":       self.request_rate,
            "error_ratio":        self.error_ratio,
            "avg_bytes_sent":     self.avg_bytes_sent,
            "avg_request_time":   self.avg_request_time,
            "unique_uri_ratio":   self.unique_uri_ratio,
            "user_agent_entropy": self.user_agent_entropy,
            "post_ratio":         self.post_ratio,
            "sample_size":        self.sample_size,
        }


def _shannon_entropy(values: list[str]) -> float:
    if not values:
        return 0.0
    counts  = Counter(values)
    total   = len(values)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 6)


def _compute_features(
    records: list[dict],
    window_seconds: int,
) -> FeatureVector | None:
    if not records:
        return None

    remote_addr = records[0].get("remote_addr", "unknown")
    total       = len(records)

    if total < settings.min_requests_threshold:
        logger.debug(
            "IP %s has %d requests, below threshold %d — skipping",
            remote_addr,
            total,
            settings.min_requests_threshold,
        )
        return None

    error_count  = sum(
        1 for r in records
        if str(r.get("status", "200")).startswith(("4", "5"))
    )
    total_bytes  = sum(float(r.get("body_bytes_sent", 0)) for r in records)
    total_time   = sum(float(r.get("request_time", 0)) for r in records)
    uris         = [r.get("request_uri", "") for r in records]
    unique_uris  = len(set(uris))
    user_agents  = [r.get("http_user_agent", "") for r in records]
    post_count   = sum(
        1 for r in records
        if str(r.get("request_method", "GET")).upper() == "POST"
    )

    return FeatureVector(
        remote_addr=remote_addr,
        request_rate=round(total / window_seconds, 6),
        error_ratio=round(error_count / total, 6),
        avg_bytes_sent=round(total_bytes / total, 6),
        avg_request_time=round(total_time / total, 6),
        unique_uri_ratio=round(unique_uris / total, 6),
        user_agent_entropy=_shannon_entropy(user_agents),
        post_ratio=round(post_count / total, 6),
        sample_size=total,
    )


async def fetch_window_records(
    ip: str,
    redis: aioredis.Redis,
) -> list[dict]:
    key    = f"window:{ip}"
    now    = time.time()
    cutoff = now - settings.window_seconds

    raw_records = await redis.zrangebyscore(key, cutoff, now)
    records     = []

    for raw in raw_records:
        try:
            records.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse record for IP %s: %s", ip, e)

    return records


async def compute_features_for_ip(
    ip: str,
    redis: aioredis.Redis,
) -> FeatureVector | None:
    records = await fetch_window_records(ip, redis)
    return _compute_features(records, settings.window_seconds)


async def compute_features_for_batch(
    unique_ips: set[str],
    redis: aioredis.Redis,
) -> list[FeatureVector]:
    feature_vectors: list[FeatureVector] = []

    for ip in unique_ips:
        vector = await compute_features_for_ip(ip, redis)
        if vector is not None:
            feature_vectors.append(vector)
            logger.debug(
                "Features computed for IP %s: %s",
                ip,
                vector.to_dict(),
            )

    return feature_vectors
