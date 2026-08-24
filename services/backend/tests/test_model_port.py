import numpy as np
from sklearn.ensemble import IsolationForest as SKIsolationForest

from services.backend.core.tables import create_all_tables
from services.backend.ml import registry
from services.backend.ml.model import classify_score, AnomalyTier, ModelManager


def test_classify_score_thresholds_match_old_service():
    # TIER1_THRESHOLD = -0.1, TIER2_THRESHOLD = -0.3 — unchanged from
    # the superseded ai_engine/ml/model.py (removed Phase 6, in git
    # history); these thresholds must not silently drift from it
    assert classify_score(0.5) == AnomalyTier.NORMAL
    assert classify_score(-0.15) == AnomalyTier.RATE_LIMIT
    assert classify_score(-0.35) == AnomalyTier.HARD_BLOCK


def test_model_manager_only_reads_dynamodb_once_across_warm_calls(dynamo_resource, monkeypatch):
    create_all_tables(dynamo_resource)
    X = np.random.rand(200, 7)
    model = SKIsolationForest(n_estimators=50, random_state=0).fit(X)
    registry.save_model(dynamo_resource, "t-1", model, registry.ModelMetadata(
        version="v1", trained_at="2026-08-21T00:00:00Z", training_samples=200,
        contamination=0.05, score_mean=0.0, score_std=1.0,
        features=["request_rate"], stage="production"), stage="production")

    ModelManager._cache.clear()  # simulate a fresh cold start
    read_count = {"n": 0}
    original = registry.load_model

    def _counting_load(*a, **kw):
        read_count["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(registry, "load_model", _counting_load)

    mgr1 = ModelManager()
    mgr1.load(dynamo_resource, "t-1")
    mgr2 = ModelManager()  # simulates the next warm invocation, new instance
    mgr2.load(dynamo_resource, "t-1")

    assert read_count["n"] == 1  # second `load()` hit the cache, not DynamoDB


def test_model_manager_shadow_mode_when_no_model(dynamo_resource):
    create_all_tables(dynamo_resource)
    ModelManager._cache.clear()
    mgr = ModelManager()
    loaded = mgr.load(dynamo_resource, "no-such-tenant")
    assert loaded is False
    assert mgr.is_ready is False


def test_model_manager_reload_clears_cache_for_tenant(dynamo_resource):
    create_all_tables(dynamo_resource)
    X = np.random.rand(200, 7)
    model = SKIsolationForest(n_estimators=50, random_state=0).fit(X)
    registry.save_model(dynamo_resource, "t-2", model, registry.ModelMetadata(
        version="v1", trained_at="2026-08-21T00:00:00Z", training_samples=200,
        contamination=0.05, score_mean=0.0, score_std=1.0,
        features=["request_rate"], stage="production"), stage="production")

    ModelManager._cache.clear()
    mgr = ModelManager()
    mgr.load(dynamo_resource, "t-2")
    assert "t-2" in ModelManager._cache

    mgr.reload(dynamo_resource, "t-2")
    assert mgr.is_ready is True
