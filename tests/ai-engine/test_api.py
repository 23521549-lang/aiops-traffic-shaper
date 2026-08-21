import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from services.ai_engine.main import app
from services.ai_engine.core.config import settings
from services.ai_engine.core.redis_client import get_redis
from services.ai_engine.ml.feature_engineering import FeatureVector


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

    def _anomalous_vector(self) -> FeatureVector:
        return FeatureVector(
            remote_addr="9.9.9.9",
            request_rate=100.0,
            error_ratio=0.9,
            avg_bytes_sent=10.0,
            avg_request_time=0.01,
            unique_uri_ratio=0.9,
            user_agent_entropy=3.0,
            post_ratio=0.9,
            sample_size=50,
        )

    def _override_redis_whitelisted(self):
        # NOTE: patch("...telemetry.get_redis", ...) does NOT work here —
        # `Depends(get_redis)` binds the real function object at import
        # time, so patching the module attribute afterward never reaches
        # it. FastAPI's own override mechanism is required instead.
        mock_redis = AsyncMock()

        async def sismember_side_effect(key, ip):
            if key == settings.reputation_redis_key:
                return False
            if key == settings.whitelist_redis_key:
                return True
            return False

        mock_redis.sismember = AsyncMock(side_effect=sismember_side_effect)
        app.dependency_overrides[get_redis] = lambda: mock_redis
        return mock_redis

    def test_whitelisted_ip_not_mitigated(self, client, auth_headers, sample_log):
        log = {**sample_log, "remote_addr": "9.9.9.9"}
        vector = self._anomalous_vector()
        self._override_redis_whitelisted()

        try:
            with patch(
                "services.ai_engine.api.routes.telemetry.store_logs_to_window",
                new_callable=AsyncMock,
                return_value={"9.9.9.9"},
            ), patch(
                "services.ai_engine.api.routes.telemetry.compute_features_for_batch",
                new_callable=AsyncMock,
                return_value=[vector],
            ), patch(
                "services.ai_engine.api.routes.telemetry.model_manager.score_vectors",
                new_callable=AsyncMock,
                return_value=[(vector, -0.5)],
            ), patch(
                "services.ai_engine.api.routes.telemetry.worker_circuit_breaker.call",
                new_callable=AsyncMock,
            ) as mock_call, patch(
                "services.ai_engine.api.routes.telemetry.store_shadow_vector",
                new_callable=AsyncMock,
            ), patch(
                "services.ai_engine.ml.registry.load_metadata",
                return_value=None,
            ), patch(
                "services.ai_engine.api.routes.telemetry.settings.shadow_mode",
                False,
            ):
                client.post(
                    "/api/v1/telemetry",
                    json={"logs": [log]},
                    headers=auth_headers,
                )

            mock_call.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    def test_whitelisted_ip_still_scored(self, client, auth_headers, sample_log):
        log = {**sample_log, "remote_addr": "9.9.9.9"}
        vector = self._anomalous_vector()
        self._override_redis_whitelisted()

        try:
            with patch(
                "services.ai_engine.api.routes.telemetry.store_logs_to_window",
                new_callable=AsyncMock,
                return_value={"9.9.9.9"},
            ), patch(
                "services.ai_engine.api.routes.telemetry.compute_features_for_batch",
                new_callable=AsyncMock,
                return_value=[vector],
            ), patch(
                "services.ai_engine.api.routes.telemetry.model_manager.score_vectors",
                new_callable=AsyncMock,
                return_value=[(vector, -0.5)],
            ), patch(
                "services.ai_engine.api.routes.telemetry.worker_circuit_breaker.call",
                new_callable=AsyncMock,
            ), patch(
                "services.ai_engine.api.routes.telemetry.store_shadow_vector",
                new_callable=AsyncMock,
            ) as mock_store, patch(
                "services.ai_engine.ml.registry.load_metadata",
                return_value=None,
            ):
                client.post(
                    "/api/v1/telemetry",
                    json={"logs": [log]},
                    headers=auth_headers,
                )

            mock_store.assert_called_once()
        finally:
            app.dependency_overrides.clear()
