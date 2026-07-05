import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from sklearn.ensemble import IsolationForest

from services.ai_engine.ml.model import (
    AnomalyTier,
    ModelManager,
    classify_score,
    TIER1_THRESHOLD,
    TIER2_THRESHOLD,
)
from services.ai_engine.ml.feature_engineering import FeatureVector


def make_vector(
    remote_addr: str = "1.2.3.4",
    request_rate: float = 1.0,
    error_ratio: float = 0.0,
    avg_bytes_sent: float = 1024.0,
    avg_request_time: float = 0.05,
    unique_uri_ratio: float = 0.2,
    user_agent_entropy: float = 0.0,
    post_ratio: float = 0.0,
    sample_size: int = 10,
) -> FeatureVector:
    return FeatureVector(
        remote_addr=remote_addr,
        request_rate=request_rate,
        error_ratio=error_ratio,
        avg_bytes_sent=avg_bytes_sent,
        avg_request_time=avg_request_time,
        unique_uri_ratio=unique_uri_ratio,
        user_agent_entropy=user_agent_entropy,
        post_ratio=post_ratio,
        sample_size=sample_size,
    )


class TestClassifyScore:
    def test_positive_score_is_normal(self):
        assert classify_score(0.1) == AnomalyTier.NORMAL

    def test_zero_score_is_normal(self):
        assert classify_score(0.0) == AnomalyTier.NORMAL

    def test_score_between_thresholds_is_tier1(self):
        score = (TIER1_THRESHOLD + TIER2_THRESHOLD) / 2
        assert classify_score(score) == AnomalyTier.RATE_LIMIT

    def test_score_below_tier2_is_hard_block(self):
        assert classify_score(-0.5) == AnomalyTier.HARD_BLOCK

    def test_tier1_boundary(self):
        assert classify_score(TIER1_THRESHOLD - 0.001) == AnomalyTier.RATE_LIMIT

    def test_tier2_boundary(self):
        assert classify_score(TIER2_THRESHOLD - 0.001) == AnomalyTier.HARD_BLOCK


class TestModelManager:
    def test_not_ready_without_model(self):
        manager = ModelManager()
        assert not manager.is_ready

    def test_load_returns_false_without_model_file(self):
        manager = ModelManager()
        with patch(
            "services.ai_engine.ml.model.model_exists",
            return_value=False,
        ):
            result = manager.load()
        assert result is False
        assert not manager.is_ready

    def test_load_returns_true_with_valid_model(self):
        manager = ModelManager()
        mock_model = MagicMock(spec=IsolationForest)

        with patch(
            "services.ai_engine.ml.model.model_exists",
            return_value=True,
        ), patch(
            "services.ai_engine.ml.model.load_model",
            return_value=mock_model,
        ):
            result = manager.load()

        assert result is True
        assert manager.is_ready

    @pytest.mark.anyio
    async def test_score_vectors_returns_neutral_in_shadow_mode(self):
        manager = ModelManager()
        vectors = [make_vector()]

        result = await manager.score_vectors(vectors)

        assert len(result) == 1
        assert result[0][1] == 0.0

    @pytest.mark.anyio
    async def test_score_vectors_empty_input(self):
        manager = ModelManager()
        result = await manager.score_vectors([])
        assert result == []

    @pytest.mark.anyio
    async def test_score_vectors_with_loaded_model(self):
        manager = ModelManager()
        mock_model = MagicMock(spec=IsolationForest)
        mock_model.decision_function.return_value = np.array([0.15])
        manager._model  = mock_model
        manager._loaded = True

        vectors = [make_vector()]
        result = await manager.score_vectors(vectors)

        assert len(result) == 1
        vector, score = result[0]
        assert isinstance(score, float)
        assert score == pytest.approx(0.15, rel=1e-3)

    @pytest.mark.anyio
    async def test_score_vectors_fallback_on_shape_mismatch(self):
        manager = ModelManager()
        mock_model = MagicMock(spec=IsolationForest)
        mock_model.decision_function.side_effect = ValueError("shape mismatch")
        manager._model  = mock_model
        manager._loaded = True

        vectors = [make_vector()]
        result = await manager.score_vectors(vectors)

        assert len(result) == 1
        assert result[0][1] == 0.0

    @pytest.mark.anyio
    async def test_multiple_vectors_scored(self):
        manager = ModelManager()
        mock_model = MagicMock(spec=IsolationForest)
        mock_model.decision_function.return_value = np.array([0.2, -0.15, -0.4])
        manager._model  = mock_model
        manager._loaded = True

        vectors = [make_vector(f"1.2.3.{i}") for i in range(3)]
        result = await manager.score_vectors(vectors)

        assert len(result) == 3
        assert classify_score(result[0][1]) == AnomalyTier.NORMAL
        assert classify_score(result[1][1]) == AnomalyTier.RATE_LIMIT
        assert classify_score(result[2][1]) == AnomalyTier.HARD_BLOCK
