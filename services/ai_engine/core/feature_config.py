from dataclasses import dataclass
from typing import Callable

import redis.asyncio as aioredis


@dataclass
class FeatureDefinition:
    name:        str
    enabled:     bool
    description: str


FEATURE_REGISTRY: list[FeatureDefinition] = [
    FeatureDefinition(
        name="request_rate",
        enabled=True,
        description="Total requests / window_seconds — detects volumetric DDoS",
    ),
    FeatureDefinition(
        name="error_ratio",
        enabled=True,
        description="(4xx+5xx) / total — detects scanners and brute force",
    ),
    FeatureDefinition(
        name="avg_bytes_sent",
        enabled=True,
        description="sum(bytes) / total — detects scrapers and data exfiltration",
    ),
    FeatureDefinition(
        name="avg_request_time",
        enabled=True,
        description="sum(time) / total — detects Slowloris and conn exhaustion",
    ),
    FeatureDefinition(
        name="unique_uri_ratio",
        enabled=True,
        description="distinct(uri) / total — detects path scanners and crawlers",
    ),
    FeatureDefinition(
        name="user_agent_entropy",
        enabled=True,
        description="Shannon entropy of UA distribution — detects botnets",
    ),
    FeatureDefinition(
        name="post_ratio",
        enabled=True,
        description="POST count / total — detects credential stuffing",
    ),
]


def get_enabled_features() -> list[FeatureDefinition]:
    return [f for f in FEATURE_REGISTRY if f.enabled]


def get_enabled_feature_names() -> list[str]:
    return [f.name for f in get_enabled_features()]


def get_feature_count() -> int:
    return len(get_enabled_features())
