from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from services.ai_engine.ml.validator import validate_model


def _fit_forest(rng_seed: int = 42) -> IsolationForest:
    rng = np.random.default_rng(rng_seed)
    X = rng.normal(size=(200, 7))
    model = IsolationForest(n_estimators=20, random_state=42)
    model.fit(X)
    return model


class TestValidateModel:
    def test_rejects_insufficient_validation_data(self):
        model = _fit_forest()
        is_valid, reason = validate_model(model, None, [[0.0] * 7] * 10)
        assert is_valid is False
        assert "Insufficient validation data" in reason

    def test_auto_approves_when_no_production_model_exists(self):
        model = _fit_forest()
        vectors = np.random.default_rng(1).normal(size=(60, 7)).tolist()

        with patch(
            "services.ai_engine.ml.validator.load_model",
            return_value=None,
        ), patch(
            "services.ai_engine.ml.validator.load_metadata",
            return_value=None,
        ):
            is_valid, reason = validate_model(model, None, vectors)

        assert is_valid is True
        assert "No production model exists" in reason

    def test_rejects_when_block_rate_too_high(self):
        # A model whose decision_function always returns negative scores
        # blocks 100% of validation traffic — well above the 15% cap.
        model = MagicMock(spec=IsolationForest)
        model.decision_function.return_value = np.full(60, -0.5)
        vectors = [[0.0] * 7] * 60

        is_valid, reason = validate_model(model, None, vectors)

        assert is_valid is False
        assert "block rate too high" in reason

    def test_rejects_on_std_regression_vs_production(self):
        staging = MagicMock(spec=IsolationForest)
        staging.decision_function.return_value = np.array(
            [0.1] * 55 + [10.0] * 5
        )  # high variance, low block rate

        prod = MagicMock(spec=IsolationForest)
        prod.decision_function.return_value = np.full(60, 0.1)  # near-zero std

        vectors = [[0.0] * 7] * 60

        with patch(
            "services.ai_engine.ml.validator.load_model",
            return_value=prod,
        ), patch(
            "services.ai_engine.ml.validator.load_metadata",
            return_value=MagicMock(),
        ):
            is_valid, reason = validate_model(staging, None, vectors)

        assert is_valid is False
        assert "std increased" in reason

    def test_passes_when_comparable_to_production(self):
        staging = MagicMock(spec=IsolationForest)
        staging.decision_function.return_value = np.full(60, 0.1)

        prod = MagicMock(spec=IsolationForest)
        prod.decision_function.return_value = np.full(60, 0.1)

        vectors = [[0.0] * 7] * 60

        with patch(
            "services.ai_engine.ml.validator.load_model",
            return_value=prod,
        ), patch(
            "services.ai_engine.ml.validator.load_metadata",
            return_value=MagicMock(),
        ):
            is_valid, reason = validate_model(staging, None, vectors)

        assert is_valid is True
        assert "Validation passed" in reason
