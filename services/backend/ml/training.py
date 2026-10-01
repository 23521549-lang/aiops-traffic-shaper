import logging
from datetime import datetime, timezone

import numpy as np
from sklearn.ensemble import IsolationForest

from services.backend.ml.feature_engineering import FEATURE_NAMES
from services.backend.core.tables import TenantsTable
from services.backend.ml.registry import (
    TIER1_Z_DEFAULT, TIER2_Z_DEFAULT, ModelMetadata, save_model,
)

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
                    stage: str = "staging", contamination: float = 0.01,
                    excluded_whitelist: int | None = None) -> ModelMetadata | None:
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
    tenant = TenantsTable(resource).get(tenant_id=tenant_id) or {}
    tier1_z = float(tenant.get("tier1_z", TIER1_Z_DEFAULT))
    tier2_z = float(tenant.get("tier2_z", TIER2_Z_DEFAULT))

    metadata = ModelMetadata(
        version=version,
        trained_at=datetime.now(timezone.utc).isoformat(),
        training_samples=len(feature_vectors),
        contamination=contamination,
        score_mean=float(np.mean(scores)),
        score_std=float(np.std(scores)),
        features=FEATURE_NAMES,
        stage=stage,
        # Two numpy calls on an array that is already in memory and already
        # fitted. Fourteen floats on a write that happens once per tenant
        # per night.
        feature_means=[float(v) for v in np.mean(X, axis=0)],
        feature_stds=[float(v) for v in np.std(X, axis=0)],
        # Copied from Tenants, which is where the operator's value of record
        # lives. Storing it only here would mean this very function silently
        # reverted it every night.
        tier1_z=tier1_z,
        tier2_z=tier2_z,
        # How many measured buckets the allowed list kept out of this
        # baseline. Counted in the walk that already happened, carried on an
        # item already being written. None where the caller did not measure
        # it, which the console reads as "not measured yet" rather than as
        # zero: those are different claims and one of them is a lie.
        excluded_whitelist_buckets=excluded_whitelist,
    )

    save_model(resource, tenant_id, model, metadata, stage=stage)
    logger.info(
        "Model trained and saved: tenant=%s stage=%s version=%s samples=%d",
        tenant_id, stage, version, len(feature_vectors),
    )
    return metadata
