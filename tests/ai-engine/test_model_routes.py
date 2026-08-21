import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from services.ai_engine.main import app
from services.ai_engine.core.config import settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-Internal-Token": settings.internal_secret}


class TestModelStatus:
    def test_returns_not_ready_with_no_model(self, client, auth_headers):
        with patch(
            "services.ai_engine.api.routes.model.load_metadata",
            return_value=None,
        ):
            response = client.get("/api/v1/model/status", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["model_ready"] is False
        assert data["version"] is None

    def test_returns_metadata_when_model_exists(self, client, auth_headers):
        mock_meta = MagicMock()
        mock_meta.version = "v20260101000000"
        mock_meta.trained_at = "2026-01-01T00:00:00+00:00"
        mock_meta.training_samples = 500
        mock_meta.contamination = 0.01
        mock_meta.score_mean = -0.02
        mock_meta.score_std = 0.05

        with patch(
            "services.ai_engine.api.routes.model.load_metadata",
            return_value=mock_meta,
        ):
            response = client.get("/api/v1/model/status", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "v20260101000000"
        assert data["training_samples"] == 500

    def test_missing_token_returns_401(self, client):
        response = client.get("/api/v1/model/status")
        assert response.status_code == 401


class TestTriggerRetrain:
    def test_triggers_background_retrain(self, client, auth_headers):
        with patch(
            "services.ai_engine.ml.training.run_baseline_training",
            new_callable=AsyncMock,
        ):
            response = client.post("/api/v1/model/retrain", headers=auth_headers)

        assert response.status_code == 200
        assert "message" in response.json()


class TestPromoteModel:
    def test_no_staging_model_returns_message(self, client, auth_headers):
        with patch(
            "services.ai_engine.ml.registry.model_exists",
            return_value=False,
        ):
            response = client.post("/api/v1/model/promote", headers=auth_headers)

        assert response.status_code == 200
        assert "No staging model" in response.json()["message"]

    def test_successful_promotion_reloads_model(self, client, auth_headers):
        with patch(
            "services.ai_engine.ml.registry.model_exists",
            return_value=True,
        ), patch(
            "services.ai_engine.ml.registry.promote_staging_to_production",
            return_value=True,
        ), patch(
            "services.ai_engine.api.routes.model.model_manager.reload",
        ) as mock_reload:
            response = client.post("/api/v1/model/promote", headers=auth_headers)

        assert response.status_code == 200
        assert "promoted to production successfully" in response.json()["message"]
        mock_reload.assert_called_once()

    def test_failed_promotion_returns_failure_message(self, client, auth_headers):
        with patch(
            "services.ai_engine.ml.registry.model_exists",
            return_value=True,
        ), patch(
            "services.ai_engine.ml.registry.promote_staging_to_production",
            return_value=False,
        ):
            response = client.post("/api/v1/model/promote", headers=auth_headers)

        assert response.status_code == 200
        assert "Promotion failed" in response.json()["message"]
