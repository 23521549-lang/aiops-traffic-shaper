import logging
from enum import IntEnum

import anyio
import numpy as np
from sklearn.ensemble import IsolationForest

from services.ai_engine.core.feature_config import (
    get_enabled_feature_names,
    get_feature_count,
)
from services.ai_engine.ml.feature_engineering import FeatureVector
from services.ai_engine.ml.registry import load_model, model_exists

logger = logging.getLogger(__name__)

FEATURE_NAMES = get_enabled_feature_names()

TIER1_THRESHOLD = -0.1
TIER2_THRESHOLD = -0.3


class AnomalyTier(IntEnum):
    NORMAL     = 0
    RATE_LIMIT = 1
    HARD_BLOCK = 2


class ModelManager:
    def __init__(self) -> None:
        self._model:  IsolationForest | None = None
        self._loaded: bool = False

    def load(self) -> bool:
        if not model_exists("production"):
            logger.info("No production model found — running in shadow mode")
            self._model  = None
            self._loaded = False
            return False

        self._model  = load_model("production")
        self._loaded = self._model is not None

        if self._loaded:
            logger.info(
                "Production model loaded: feature_count=%d features=%s",
                get_feature_count(),
                FEATURE_NAMES,
            )
        else:
            logger.error("Failed to load production model")

        return self._loaded

    def reload(self) -> bool:
        logger.info("Reloading production model")
        return self.load()

    @property
    def is_ready(self) -> bool:
        return self._loaded and self._model is not None

    def _predict_sync(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model is not loaded")
        return self._model.decision_function(X)

    async def score_vectors(
        self,
        vectors: list[FeatureVector],
    ) -> list[tuple[FeatureVector, float]]:
        if not vectors:
            return []

        if not self.is_ready:
            logger.debug("Model not ready — shadow mode, skipping scoring")
            return [(v, 0.0) for v in vectors]

        X = np.array([v.to_list() for v in vectors], dtype=np.float64)

        expected_features = get_feature_count()
        if X.shape[1] != expected_features:
            logger.error(
                "Feature shape mismatch: expected=%d got=%d",
                expected_features,
                X.shape[1],
            )
            return [(v, 0.0) for v in vectors]

        try:
            scores = await anyio.to_thread.run_sync(
                lambda: self._predict_sync(X)
            )
            return list(zip(vectors, scores.tolist()))
        except ValueError as e:
            logger.error(
                "Model shape mismatch: expected=%d features: %s",
                expected_features,
                e,
            )
            return [(v, 0.0) for v in vectors]
        except Exception as e:
            logger.error("Unexpected error during inference: %s", e)
            return [(v, 0.0) for v in vectors]


def classify_score(score: float) -> AnomalyTier:
    if score < TIER2_THRESHOLD:
        return AnomalyTier.HARD_BLOCK
    if score < TIER1_THRESHOLD:
        return AnomalyTier.RATE_LIMIT
    return AnomalyTier.NORMAL


model_manager = ModelManager()
