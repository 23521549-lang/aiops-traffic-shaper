import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from services.ai_engine.main import app
from services.ai_engine.core.config import settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-Internal-Token": settings.internal_secret}


@pytest.fixture
def sample_log():
    return {
        "time_iso8601":    "2026-01-01T00:00:00+07:00",
        "remote_addr":     "1.2.3.4",
        "request_method":  "GET",
        "request_uri":     "/api/test",
        "status":          "200",
        "body_bytes_sent": "1024",
        "request_time":    "0.05",
        "http_user_agent": "Mozilla/5.0",
    }


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy(self, client):
        response = client.get("/health")
        assert response.json() == {"status": "healthy"}


class TestTelemetryEndpoint:
    def test_missing_token_returns_401(self, client, sample_log):
        response = client.post(
            "/api/v1/telemetry",
            json={"logs": [sample_log]},
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, client, sample_log):
        response = client.post(
            "/api/v1/telemetry",
            json={"logs": [sample_log]},
            headers={"X-Internal-Token": "invalid-token"},
        )
        assert response.status_code == 401

    def test_empty_batch_returns_200(self, client, auth_headers):
        with patch(
            "services.ai_engine.api.routes.telemetry.get_redis",
            return_value=AsyncMock(),
        ):
            response = client.post(
                "/api/v1/telemetry",
                json={"logs": []},
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["received"] == 0

    def test_valid_batch_returns_200(self, client, auth_headers, sample_log):
        mock_redis = AsyncMock()
        mock_redis.pipeline.return_value.__aenter__ = AsyncMock(
            return_value=AsyncMock()
        )
        mock_redis.pipeline.return_value.__aexit__ = AsyncMock(
            return_value=False
        )
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.zrangebyscore = AsyncMock(return_value=[])

        with patch(
            "services.ai_engine.api.routes.telemetry.get_redis",
            return_value=mock_redis,
        ), patch(
            "services.ai_engine.ml.feature_engineering.fetch_window_records",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.post(
                "/api/v1/telemetry",
                json={"logs": [sample_log]},
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["received"] == 1

    def test_invalid_log_schema_returns_422(self, client, auth_headers):
        response = client.post(
            "/api/v1/telemetry",
            json={"logs": [{"invalid_field": "value"}]},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_response_schema(self, client, auth_headers, sample_log):
        mock_redis = AsyncMock()
        mock_redis.pipeline.return_value.__aenter__ = AsyncMock(
            return_value=AsyncMock()
        )
        mock_redis.pipeline.return_value.__aexit__ = AsyncMock(
            return_value=False
        )
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.zrangebyscore = AsyncMock(return_value=[])

        with patch(
            "services.ai_engine.api.routes.telemetry.get_redis",
            return_value=mock_redis,
        ), patch(
            "services.ai_engine.ml.feature_engineering.fetch_window_records",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.post(
                "/api/v1/telemetry",
                json={"logs": [sample_log]},
                headers=auth_headers,
            )
        data = response.json()
        assert "received" in data
        assert "processed_ips" in data
        assert "message" in data
