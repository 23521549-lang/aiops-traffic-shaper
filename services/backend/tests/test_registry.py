import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from services.backend.core.tables import create_all_tables
from services.backend.ml.registry import save_model, load_model, ModelMetadata


def _trained_model(n_estimators=50):
    X = np.random.rand(500, 7)
    return IsolationForest(n_estimators=n_estimators, contamination=0.05, random_state=0).fit(X)


def test_save_and_load_model_roundtrip(dynamo_resource):
    create_all_tables(dynamo_resource)
    model = _trained_model()
    meta = ModelMetadata(version="v1", trained_at="2026-08-21T00:00:00Z",
                          training_samples=500, contamination=0.05,
                          score_mean=0.0, score_std=1.0,
                          features=["request_rate"], stage="production")
    save_model(dynamo_resource, "t-1", model, meta, stage="production")
    loaded = load_model(dynamo_resource, "t-1", stage="production")
    assert loaded is not None
    assert loaded.n_estimators == 50


def test_save_model_rejects_oversized_blob(dynamo_resource):
    create_all_tables(dynamo_resource)
    model = _trained_model(n_estimators=100)  # measured ~474KB gzip in ADR-002 — over limit
    meta = ModelMetadata(version="v1", trained_at="2026-08-21T00:00:00Z",
                          training_samples=500, contamination=0.05,
                          score_mean=0.0, score_std=1.0,
                          features=["request_rate"], stage="production")
    with pytest.raises(ValueError, match="exceeds DynamoDB item limit"):
        save_model(dynamo_resource, "t-1", model, meta, stage="production")


def test_load_missing_model_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    assert load_model(dynamo_resource, "no-such-tenant") is None


def test_load_corrupted_blob_returns_none_not_crash(dynamo_resource):
    # Simulates a truncated write or a joblib/sklearn version mismatch
    # between the training Lambda and the serving Lambda — must fall back
    # to shadow mode (None), not propagate an unhandled exception into the
    # telemetry request path.
    from services.backend.core.tables import ModelsTable
    create_all_tables(dynamo_resource)
    ModelsTable(dynamo_resource).put(
        tenant_id="t-1", stage_version="production", model_blob=b"not a valid gzip blob",
        version="v1", trained_at="2026-08-21T00:00:00Z", training_samples=1,
        contamination=0.05, score_mean=0.0, score_std=1.0,
        features=["request_rate"], stage="production",
    )
    assert load_model(dynamo_resource, "t-1", stage="production") is None
