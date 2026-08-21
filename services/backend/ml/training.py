import logging
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import IsolationForest

from services.backend.ml.feature_engineering import FEATURE_NAMES
from services.backend.ml.registry import ModelMetadata, save_model

logger = logging.getLogger(__name__)

# Full retrain orchestration (collecting per-tenant training data from
# TelemetryEvents, looping every tenant, EventBridge entry point,
# staging->production promotion policy) belongs in docs/PLAN.md Stage 7,
# not here — Stage 3's scope is just "given feature vectors, train and
# save one tenant's model correctly", matching this stage's checkpoint.
MIN_TRAINING_SAMPLES = 100
N_ESTIMATORS = 50  # ADR-002 measured constraint — do not raise without
                    # re-measuring the gzip size against DynamoDB's 400KB
                    # item limit (100 estimators measured ~474KB, over it)


def train_and_save(resource, tenant_id: str, feature_vectors: list[list[float]],
                    stage: str = "staging", contamination: float = 0.01) -> ModelMetadata | None:
    if len(feature_vectors) < MIN_TRAINING_SAMPLES:
        logger.warning(
            "Insufficient training data for tenant=%s: have=%d need=%d",
            tenant_id, len(feature_vectors), MIN_TRAINING_SAMPLES,
        )
        return None

    X = np.array(feature_vectors, dtype=np.float64)
    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        contamination=contamination,
        random_state=42,
    )
    model.fit(X)

    scores = model.decision_function(X)
    version = datetime.now(timezone.utc).strftime("v%Y%m%d%H%M%S")
    metadata = ModelMetadata(
        version=version,
        trained_at=datetime.now(timezone.utc).isoformat(),
        training_samples=len(feature_vectors),
        contamination=contamination,
        score_mean=float(np.mean(scores)),
        score_std=float(np.std(scores)),
        features=FEATURE_NAMES,
        stage=stage,
    )

    save_model(resource, tenant_id, model, metadata, stage=stage)
    logger.info(
        "Model trained and saved: tenant=%s stage=%s version=%s samples=%d",
        tenant_id, stage, version, len(feature_vectors),
    )
    return metadata
