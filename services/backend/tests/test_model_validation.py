"""Port of the old ai_engine/ml/validator.py into the hybrid backend.

Retraining has written straight to `production` with no gate since Stage 7 —
recorded as a deliberate simplification in retrain_handler's own docstring, and
carried as an accepted risk through the Phase 3, 4 and 5 manifests. The old
single-tenant model had a real validation gate; this brings it across, adapted
to the per-tenant registry.

The two checks are the ones the old validator made, with its thresholds intact:
a staging model that would block too much of normal traffic is rejected, and a
staging model whose score spread widened materially against production is
rejected as unstable.
"""
import numpy as np

from services.backend.ml.registry import ModelMetadata


def _meta(stage="staging", score_std=0.05, score_mean=0.1):
    return ModelMetadata(
        version="v20260824000000", trained_at="2026-08-24T00:00:00Z",
        training_samples=200, contamination=0.01,
        score_mean=score_mean, score_std=score_std,
        features=["f1"], stage=stage,
    )


class _FixedScores:
    """A stand-in model returning a controlled score vector, so each threshold
    can be driven exactly rather than hoping a trained forest lands there."""

    def __init__(self, scores):
        self._scores = np.asarray(scores, dtype=np.float64)

    def decision_function(self, X):
        return self._scores[: len(X)]


def _vectors(n):
    return [[float(i)] for i in range(n)]


def test_rejects_insufficient_validation_data():
    from services.backend.ml.validation import validate_model

    ok, reason = validate_model(_FixedScores([0.1] * 10), _meta(), _vectors(10),
                                prod_model=None, prod_meta=None)
    assert ok is False
    assert "validation data" in reason.lower()


def test_rejects_a_staging_model_that_blocks_too_much():
    """A model scoring most of normal traffic below zero would rate-limit or
    block the customer's own users. The old threshold was 15%."""
    from services.backend.ml.validation import validate_model

    scores = [-1.0] * 30 + [0.5] * 70  # 30% would be blocked
    ok, reason = validate_model(_FixedScores(scores), _meta(), _vectors(100),
                                prod_model=None, prod_meta=None)
    assert ok is False
    assert "block rate" in reason.lower()


def test_first_model_is_promoted_when_no_production_exists():
    from services.backend.ml.validation import validate_model

    ok, reason = validate_model(_FixedScores([0.5] * 100), _meta(), _vectors(100),
                                prod_model=None, prod_meta=None)
    assert ok is True
    assert "no production model" in reason.lower()


def test_rejects_a_staging_model_whose_score_spread_widened():
    """Score std widening materially against production means the model has
    become unstable on the same data — the old validator's second check."""
    from services.backend.ml.validation import validate_model

    # Both models must stay BELOW the block-rate ceiling, or that check fires
    # first and this test would pass for the wrong reason: all scores positive,
    # only the spread differs.
    staging = _FixedScores([0.9, 0.1] * 50)    # std 0.4
    prod = _FixedScores([0.5, 0.5] * 50)       # std 0.0
    ok, reason = validate_model(staging, _meta(), _vectors(100),
                                prod_model=prod, prod_meta=_meta(stage="production"))
    assert ok is False
    assert "std" in reason.lower()


def test_accepts_a_healthy_staging_model_against_production():
    from services.backend.ml.validation import validate_model

    staging = _FixedScores([0.2, 0.1] * 50)
    prod = _FixedScores([0.2, 0.1] * 50)
    ok, reason = validate_model(staging, _meta(), _vectors(100),
                                prod_model=prod, prod_meta=_meta(stage="production"))
    assert ok is True, reason


# --- integration: the retrain loop must actually use the gate -------------

def _seed_and_retrain(dynamo_resource, monkeypatch=None):
    from services.backend.core.tables import TenantsTable, create_all_tables
    from services.backend.ml.feature_engineering import record_batch
    from services.backend.ml.training import MIN_TRAINING_SAMPLES

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="A", status="active",
                                      created_at="2026-08-21T00:00:00Z")

    class _Log:
        remote_addr = "1.1.1.1"
        status = "200"
        body_bytes_sent = "512"
        request_time = "0.05"
        request_uri = "/a"
        request_method = "GET"
        http_user_agent = "ua-1"

    for i in range(MIN_TRAINING_SAMPLES + 5):
        record_batch(dynamo_resource, "t-1", [_Log()] * 3, bucket_seconds=5,
                     now=1000.0 + i * 5)


def test_retrain_promotes_to_production_only_through_the_gate(dynamo_resource, monkeypatch):
    """The whole point of the port: retrain_handler must consult the validator
    instead of writing to production unconditionally."""
    from services.backend.ml import registry
    from services.backend import retrain_handler

    _seed_and_retrain(dynamo_resource)
    monkeypatch.setattr(retrain_handler, "validate_model",
                        lambda *a, **k: (False, "rejected by test"))

    results = retrain_handler.retrain_all_tenants(dynamo_resource)

    assert results["t-1"] is None or results["t-1"].stage != "production"
    assert not registry.model_exists(dynamo_resource, "t-1", stage="production")
    assert registry.model_exists(dynamo_resource, "t-1", stage="staging")


def test_retrain_promotes_when_the_gate_passes(dynamo_resource, monkeypatch):
    from services.backend.ml import registry
    from services.backend import retrain_handler

    _seed_and_retrain(dynamo_resource)
    monkeypatch.setattr(retrain_handler, "validate_model",
                        lambda *a, **k: (True, "approved by test"))

    results = retrain_handler.retrain_all_tenants(dynamo_resource)

    assert results["t-1"] is not None
    assert registry.model_exists(dynamo_resource, "t-1", stage="production")
