import logging
from enum import IntEnum

import numpy as np
from sklearn.ensemble import IsolationForest

from services.backend.ml import registry
from services.backend.ml.registry import ScoreStats
from services.backend.ml.feature_engineering import FeatureVector

logger = logging.getLogger(__name__)

# Fallback only, for a model whose training scores had no spread at all.
TIER1_THRESHOLD = -0.1
TIER2_THRESHOLD = -0.3

# The real thresholds: how many standard deviations below this model's own
# training mean a score sits. decision_function is calibrated against the data
# a model was fitted to - `contamination` puts the offset at that training
# set's 1% quantile - so the same attack scored -0.204, -0.105 and -0.092
# against three production models of the same tenant, crossing the fixed -0.1
# line in between. Measured over 20 baselines with 300 held-out normal buckets
# each (docs/adr/006-score-calibration.md):
#
#   raw < -0.10   brute force 14/20   mild abuse  2/20   false positives 0.00%
#   z   < -4.0    brute force 20/20   mild abuse 17/20   false positives 0.27%
#   z   < -5.0    brute force 11/20   mild abuse  0/20   false positives 0.00%
#
# raw < -0.15 caught nothing at all, so the old tier-2 line at -0.3 was not
# strict - it was unreachable, and the hard-block path had never once fired.
TIER1_Z = -4.0
TIER2_Z = -5.0


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
        self._stats:  ScoreStats | None = None
        self._loaded: bool = False
        self._tenant_id: str | None = None

    def load(self, resource, tenant_id: str) -> bool:
        self._tenant_id = tenant_id

        if tenant_id in ModelManager._cache:
            self._model, self._stats = ModelManager._cache[tenant_id]
            self._loaded = True
            return True

        # ONE read. This used to call registry.model_exists() and then
        # registry.load_model(), and model_exists fetched the whole item - so
        # every cold start read the ~238KB model twice, about 120 RCU of a
        # 25 RCU/second account budget.
        model, stats = registry.load_model_and_stats(resource, tenant_id, "production")
        if model is None:
            logger.info("No usable production model for tenant=%s - shadow mode", tenant_id)
            self._model = None
            self._stats = None
            self._loaded = False
            return False

        ModelManager._cache[tenant_id] = (model, stats)
        self._model, self._stats = model, stats
        self._loaded = True
        logger.info("Production model loaded and cached: tenant=%s", tenant_id)
        return True

    @property
    def stats(self) -> ScoreStats | None:
        """The training-score distribution this model was fitted to. Cached
        with the model so tiering a score costs no extra read."""
        return self._stats

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
        except Exception as e:
            # A separate `except ValueError` (feature-shape mismatch) used
            # to sit above this with an identical fallback body — merged,
            # since ValueError is already an Exception subclass and both
            # branches did the same thing (found on review).
            logger.error("Inference failed: tenant=%s: %s", self._tenant_id, e)
            return [(v, 0.0) for v in vectors]


def classify(score: float, stats: ScoreStats | None) -> AnomalyTier:
    """Tier a score by its distance, in standard deviations, from the mean of
    the training scores of the model that produced it.

    Falls back to the absolute thresholds when a model has no usable spread:
    score_std is zero only when every training bucket scored identically, and
    dividing by it on the request path would be a crash rather than a
    detection."""
    if stats is None or stats.std <= 0:
        return classify_score(score)
    z = (score - stats.mean) / stats.std
    if z < TIER2_Z:
        return AnomalyTier.HARD_BLOCK
    if z < TIER1_Z:
        return AnomalyTier.RATE_LIMIT
    return AnomalyTier.NORMAL


def classify_score(score: float) -> AnomalyTier:
    """Absolute thresholds. Kept for the degenerate-spread fallback above."""
    if score < TIER2_THRESHOLD:
        return AnomalyTier.HARD_BLOCK
    if score < TIER1_THRESHOLD:
        return AnomalyTier.RATE_LIMIT
    return AnomalyTier.NORMAL
