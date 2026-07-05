import json
import logging
from datetime import datetime, timezone

import numpy as np
import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sklearn.ensemble import IsolationForest

from services.ai_engine.core.config import settings
from services.ai_engine.core.feature_config import get_enabled_feature_names
from services.ai_engine.core.redis_client import get_redis
from services.ai_engine.ml.model import model_manager
from services.ai_engine.ml.registry import (
    ModelMetadata,
    model_exists,
    promote_staging_to_production,
    save_model,
)
from services.ai_engine.ml.validator import validate_model

logger = logging.getLogger(__name__)

SHADOW_STREAM_KEY    = "training:shadow_data"
TRAINING_EXCLUDE_KEY = "training:excluded_ips"
SHADOW_STREAM_TTL    = 7 * 24 * 3600
MIN_TRAINING_SAMPLES = 100
VALIDATION_SPLIT     = 0.2


async def store_shadow_vector(
    remote_addr: str,
    features: list[float],
    score: float,
    redis: aioredis.Redis,
) -> None:
    record = {
        "remote_addr": remote_addr,
        "score":       str(score),
        "features":    json.dumps(features),
        "ts":          str(datetime.now(timezone.utc).timestamp()),
    }

    await redis.xadd(
        SHADOW_STREAM_KEY,
        record,
        maxlen=settings.redis_stream_maxlen,
        approximate=True,
    )
    await redis.expire(SHADOW_STREAM_KEY, SHADOW_STREAM_TTL)


async def _read_shadow_data(redis: aioredis.Redis) -> list[list[float]]:
    excluded_ips = await redis.smembers(TRAINING_EXCLUDE_KEY)
    records      = await redis.xrange(SHADOW_STREAM_KEY, "-", "+")
    vectors      = []
    excluded     = 0
    feature_names = get_enabled_feature_names()

    for _, fields in records:
        try:
            remote_addr = fields.get("remote_addr", "")
            if remote_addr in excluded_ips:
                excluded += 1
                continue

            features = json.loads(fields.get("features", "[]"))
            if len(features) == len(feature_names):
                vectors.append(features)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Skipping malformed shadow record: %s", e)

    if excluded > 0:
        logger.info(
            "Excluded %d records from whitelisted IPs during training",
            excluded,
        )

    return vectors


async def run_baseline_training() -> bool:
    logger.info("Starting training job")
    redis   = await get_redis()
    vectors = await _read_shadow_data(redis)

    if len(vectors) < MIN_TRAINING_SAMPLES:
        logger.warning(
            "Insufficient training data: have=%d need=%d",
            len(vectors),
            MIN_TRAINING_SAMPLES,
        )
        return False

    split_idx     = int(len(vectors) * (1 - VALIDATION_SPLIT))
    train_vectors = vectors[:split_idx]
    val_vectors   = vectors[split_idx:]

    X_train = np.array(train_vectors, dtype=np.float64)
    logger.info(
        "Training IsolationForest: train=%d val=%d features=%s",
        len(train_vectors),
        len(val_vectors),
        get_enabled_feature_names(),
    )

    model = IsolationForest(
        n_estimators=100,
        contamination=settings.model_contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train)

    scores     = model.decision_function(X_train)
    score_mean = float(np.mean(scores))
    score_std  = float(np.std(scores))

    is_valid, reason = validate_model(model, None, val_vectors)
    if not is_valid:
        logger.warning(
            "Model validation failed: %s — not promoting", reason
        )
        return False

    logger.info("Model validation passed: %s", reason)

    version = datetime.now(timezone.utc).strftime("v%Y%m%d%H%M%S")
    metadata = ModelMetadata(
        version=version,
        trained_at=datetime.now(timezone.utc).isoformat(),
        training_samples=len(train_vectors),
        contamination=settings.model_contamination,
        score_mean=score_mean,
        score_std=score_std,
        features=get_enabled_feature_names(),
        stage="staging",
    )

    save_model(model, metadata, stage="staging")
    logger.info(
        "Model saved to staging: version=%s samples=%d "
        "score_mean=%.4f score_std=%.4f",
        version,
        len(train_vectors),
        score_mean,
        score_std,
    )

    if not model_exists("production"):
        promoted = promote_staging_to_production()
        if promoted:
            model_manager.reload()
            logger.info("First model promoted to production and loaded")

    return True


async def run_daily_retrain() -> bool:
    logger.info("Starting daily retrain job")
    trained = await run_baseline_training()

    if not trained:
        logger.warning(
            "Daily retrain skipped — training failed or insufficient data"
        )
        return False

    promoted = promote_staging_to_production()
    if promoted:
        model_manager.reload()
        logger.info("Daily retrain completed and promoted to production")
    else:
        logger.warning(
            "Staging model not promoted — keeping production model"
        )

    return promoted


def start_training_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        run_baseline_training,
        trigger="interval",
        hours=settings.shadow_mode_hours,
        id="baseline_training",
        replace_existing=True,
    )

    scheduler.add_job(
        run_daily_retrain,
        trigger="interval",
        hours=settings.retrain_interval_hours,
        id="daily_retrain",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Training scheduler started: shadow_hours=%d retrain_hours=%d",
        settings.shadow_mode_hours,
        settings.retrain_interval_hours,
    )
    return scheduler
