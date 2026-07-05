import logging

import numpy as np
from sklearn.ensemble import IsolationForest

from services.ai_engine.ml.registry import ModelMetadata, load_metadata, load_model

logger = logging.getLogger(__name__)

MAX_ACCEPTABLE_BLOCK_RATE    = 0.15
MIN_SCORE_MEAN_IMPROVEMENT   = 0.001
MAX_STD_REGRESSION_THRESHOLD = 0.05


def validate_model(
    staging_model: IsolationForest,
    staging_meta: ModelMetadata,
    validation_vectors: list[list[float]],
) -> tuple[bool, str]:
    if len(validation_vectors) < 50:
        return False, f"Insufficient validation data: {len(validation_vectors)} samples, need 50"

    X = np.array(validation_vectors, dtype=np.float64)

    staging_scores = staging_model.decision_function(X)
    staging_block_rate = float(np.mean(staging_scores < 0))

    if staging_block_rate > MAX_ACCEPTABLE_BLOCK_RATE:
        return False, (
            f"Staging block rate too high: {staging_block_rate:.3f} "
            f"(max {MAX_ACCEPTABLE_BLOCK_RATE}). "
            f"Model may be too aggressive."
        )

    prod_model = load_model("production")
    prod_meta  = load_metadata("production")

    if prod_model is None or prod_meta is None:
        logger.info(
            "No production model to compare against — staging auto-approved"
        )
        return True, "No production model exists — staging promoted as first model"

    prod_scores    = prod_model.decision_function(X)
    prod_block_rate = float(np.mean(prod_scores < 0))

    staging_mean = float(np.mean(staging_scores))
    prod_mean    = float(np.mean(prod_scores))
    staging_std  = float(np.std(staging_scores))
    prod_std     = float(np.std(prod_scores))

    std_regression = staging_std - prod_std
    if std_regression > MAX_STD_REGRESSION_THRESHOLD:
        return False, (
            f"Staging score std increased by {std_regression:.4f} "
            f"(prod={prod_std:.4f} staging={staging_std:.4f}). "
            f"Model may be unstable."
        )

    logger.info(
        "Model validation results: "
        "staging_block_rate=%.3f prod_block_rate=%.3f "
        "staging_mean=%.4f prod_mean=%.4f "
        "staging_std=%.4f prod_std=%.4f",
        staging_block_rate, prod_block_rate,
        staging_mean, prod_mean,
        staging_std, prod_std,
    )

    return True, (
        f"Validation passed: "
        f"block_rate={staging_block_rate:.3f} "
        f"score_mean={staging_mean:.4f} "
        f"score_std={staging_std:.4f}"
    )
