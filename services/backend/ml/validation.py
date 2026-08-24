"""Model validation gate — ported from the superseded ai_engine/ml/validator.py.

Until this existed, `retrain_handler` wrote every freshly trained model straight
to `production`. That was flagged as a deliberate v1 simplification in Stage 7
and carried as an accepted risk through three phase manifests: a bad night of
traffic could train a model that blocks the customer's own users, and it would
go live unopposed the moment training finished.

Both checks and all three thresholds come from the old single-tenant validator
unchanged — what changed is the registry underneath (per-tenant now), so this
takes the production model as an argument instead of reaching for a global one.
"""
import logging

import numpy as np

from services.backend.ml.registry import ModelMetadata

logger = logging.getLogger(__name__)

# Below this, the validation set is too small for the rates below to mean
# anything, so the gate refuses rather than guessing.
MIN_VALIDATION_SAMPLES = 50

# A model scoring more than this share of ordinary traffic as anomalous would
# rate-limit or block the customer's real users. 15% was the old threshold.
MAX_ACCEPTABLE_BLOCK_RATE = 0.15

# Score spread widening against production means the model has become unstable
# on the same data, even when its block rate still looks acceptable.
MAX_STD_REGRESSION_THRESHOLD = 0.05


def validate_model(staging_model, staging_meta: ModelMetadata,
                   validation_vectors: list[list[float]],
                   prod_model=None, prod_meta: ModelMetadata | None = None,
                   ) -> tuple[bool, str]:
    """Returns (promote?, human-readable reason). The reason is recorded either
    way — a refusal that nobody can explain is as bad as no gate at all."""
    if len(validation_vectors) < MIN_VALIDATION_SAMPLES:
        return False, (
            f"Insufficient validation data: {len(validation_vectors)} samples, "
            f"need {MIN_VALIDATION_SAMPLES}"
        )

    X = np.array(validation_vectors, dtype=np.float64)
    staging_scores = staging_model.decision_function(X)
    staging_block_rate = float(np.mean(staging_scores < 0))

    if staging_block_rate > MAX_ACCEPTABLE_BLOCK_RATE:
        return False, (
            f"Staging block rate too high: {staging_block_rate:.3f} "
            f"(max {MAX_ACCEPTABLE_BLOCK_RATE}) - model is too aggressive"
        )

    if prod_model is None:
        return True, "No production model exists - staging promoted as the first model"

    prod_scores = prod_model.decision_function(X)
    staging_std = float(np.std(staging_scores))
    prod_std = float(np.std(prod_scores))
    std_regression = staging_std - prod_std

    if std_regression > MAX_STD_REGRESSION_THRESHOLD:
        return False, (
            f"Staging score std increased by {std_regression:.4f} "
            f"(production={prod_std:.4f} staging={staging_std:.4f}) - model may be unstable"
        )

    return True, (
        f"Approved: block_rate={staging_block_rate:.3f} "
        f"std {prod_std:.4f} -> {staging_std:.4f}"
    )
