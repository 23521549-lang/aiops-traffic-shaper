import logging
from enum import IntEnum

import numpy as np
from sklearn.ensemble import IsolationForest

from services.backend.ml import registry
from services.backend.ml.feature_engineering import FeatureVector

logger = logging.getLogger(__name__)

TIER1_THRESHOLD = -0.1
TIER2_THRESHOLD = -0.3


class AnomalyTier(IntEnum):
    NORMAL     = 0
    RATE_LIMIT = 1
    HARD_BLOCK = 2


class ModelManager:
    # Class-level cache, shared across every instance in a warm Lambda
    # container — see docs/PLAN.md Stage 3 Step 6. A naive per-request
    # `registry.load_model()` call would read the ~238KB compressed model
    # item on EVERY telemetry request (~60 RCU for a strongly consistent
    # read of an item that size), blowing past the entire 25 RCU/sec
    # account-wide budget in a single request. Caching per tenant here,
    # persisting across warm invocations, is load-bearing, not optional.
    #
    # Explicit trade-off: a warm container can keep serving a stale model
    # for up to that container's lifetime after a retrain promotes a new
    # version. No version-check read is added, because a cheap
    # version-check would itself cost a DynamoDB read on every request,
    # reintroducing the exact problem this cache exists to remove. Lambda
    # containers recycle on their own; a manual "flush cache" control-
    # platform action is the intended fix if faster propagation is ever
    # needed, not a per-request check.
    _cache: dict[str, IsolationForest] = {}

    def __init__(self) -> None:
        self._model:  IsolationForest | None = None
        self._loaded: bool = False
        self._tenant_id: str | None = None

    def load(self, resource, tenant_id: str) -> bool:
        self._tenant_id = tenant_id

        if tenant_id in ModelManager._cache:
            self._model  = ModelManager._cache[tenant_id]
            self._loaded = True
            return True

        if not registry.model_exists(resource, tenant_id, "production"):
            logger.info("No production model for tenant=%s — shadow mode", tenant_id)
            self._model  = None
            self._loaded = False
            return False

        model = registry.load_model(resource, tenant_id, "production")
        self._loaded = model is not None
        if self._loaded:
            ModelManager._cache[tenant_id] = model
            self._model = model
            logger.info("Production model loaded and cached: tenant=%s", tenant_id)
        else:
            logger.error("Failed to load production model: tenant=%s", tenant_id)

        return self._loaded

    def reload(self, resource, tenant_id: str) -> bool:
        logger.info("Reloading production model: tenant=%s", tenant_id)
        ModelManager._cache.pop(tenant_id, None)
        return self.load(resource, tenant_id)

    @property
    def is_ready(self) -> bool:
        return self._loaded and self._model is not None

    def _predict_sync(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Model is not loaded")
        return self._model.decision_function(X)

    def score_vectors(
        self,
        vectors: list[FeatureVector],
    ) -> list[tuple[FeatureVector, float]]:
        if not vectors:
            return []

        if not self.is_ready:
            logger.debug("Model not ready — shadow mode, skipping scoring")
            return [(v, 0.0) for v in vectors]

        X = np.array([v.to_list() for v in vectors], dtype=np.float64)

        try:
            scores = self._predict_sync(X)
            return list(zip(vectors, scores.tolist()))
        except ValueError as e:
            logger.error("Model shape mismatch: tenant=%s: %s", self._tenant_id, e)
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
