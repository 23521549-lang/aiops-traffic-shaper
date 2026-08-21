import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from services.ai_engine.main import app
from services.ai_engine.core.config import settings
from services.ai_engine.core.redis_client import get_redis


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-Internal-Token": settings.internal_secret}


class TestAddToWhitelist:
    def test_adds_to_whitelist_and_training_exclusion(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=1)

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.post(
                "/api/v1/whitelist",
                json={"ip": "1.2.3.4", "reason": "trusted partner"},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 200
        assert "1.2.3.4" in response.json()["message"]
        assert mock_redis.sadd.call_count == 2
        mock_redis.sadd.assert_any_call(settings.whitelist_redis_key, "1.2.3.4")
        mock_redis.sadd.assert_any_call("training:excluded_ips", "1.2.3.4")

    def test_invalid_ip_returns_422(self, client, auth_headers):
        response = client.post(
            "/api/v1/whitelist",
            json={"ip": "not-an-ip", "reason": "x"},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestRemoveFromWhitelist:
    def test_removes_existing_ip(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=1)

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.delete(
                "/api/v1/whitelist/1.2.3.4",
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 200

    def test_removing_nonexistent_ip_returns_404(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=0)

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.delete(
                "/api/v1/whitelist/9.9.9.9",
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 404


class TestGetWhitelist:
    def test_returns_whitelisted_and_excluded_ips(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(
            side_effect=[{"1.2.3.4"}, {"5.6.7.8"}]
        )

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.get("/api/v1/whitelist", headers=auth_headers)
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 200
        data = response.json()
        assert data["whitelisted_ips"] == ["1.2.3.4"]
        assert data["training_excluded"] == ["5.6.7.8"]


class TestRemoveFromTrainingExclusion:
    def test_removes_existing_exclusion(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=1)

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.delete(
                "/api/v1/whitelist/1.2.3.4/training-exclusion",
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 200

    def test_removing_nonexistent_exclusion_returns_404(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=0)

        app.dependency_overrides[get_redis] = lambda: mock_redis
        try:
            response = client.delete(
                "/api/v1/whitelist/9.9.9.9/training-exclusion",
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(get_redis, None)

        assert response.status_code == 404
