import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from services.worker_orchestrator.main import app
from services.worker_orchestrator.core.config import settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"X-Internal-Token": settings.internal_secret}


@pytest.fixture
def sample_mitigate_payload():
    return {
        "target_ip": "1.2.3.4",
        "reason":    "test_anomaly",
        "tier":      1,
    }


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy(self, client):
        assert client.get("/health").json() == {"status": "healthy"}


class TestMitigateEndpoint:
    def test_missing_token_returns_401(self, client, sample_mitigate_payload):
        response = client.post(
            "/api/v1/mitigate",
            json=sample_mitigate_payload,
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, client, sample_mitigate_payload):
        response = client.post(
            "/api/v1/mitigate",
            json=sample_mitigate_payload,
            headers={"X-Internal-Token": "wrong"},
        )
        assert response.status_code == 401

    def test_whitelisted_ip_skipped(
        self, client, auth_headers, sample_mitigate_payload
    ):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(return_value=True)

        with patch(
            "services.worker_orchestrator.api.routes.mitigate.get_redis",
            return_value=mock_redis,
        ):
            response = client.post(
                "/api/v1/mitigate",
                json=sample_mitigate_payload,
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["whitelisted"] is True
        assert data["action"] == "skipped"

    def test_tier1_mitigation_sets_redis_key(
        self, client, auth_headers, sample_mitigate_payload
    ):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(return_value=False)
        mock_redis.set       = AsyncMock()

        with patch(
            "services.worker_orchestrator.api.routes.mitigate.get_redis",
            return_value=mock_redis,
        ):
            response = client.post(
                "/api/v1/mitigate",
                json={**sample_mitigate_payload, "tier": 1},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "rate_limited"
        assert data["tier"] == 1
        mock_redis.set.assert_called_once()

    def test_tier2_mitigation_patches_configmap(
        self, client, auth_headers, sample_mitigate_payload
    ):
        mock_redis = AsyncMock()
        mock_redis.sismember = AsyncMock(return_value=False)
        mock_redis.get       = AsyncMock(return_value=None)
        mock_redis.set       = AsyncMock()

        mock_patcher = MagicMock()
        mock_patcher.add_deny_rule.return_value = True

        with patch(
            "services.worker_orchestrator.api.routes.mitigate.get_redis",
            return_value=mock_redis,
        ), patch(
            "services.worker_orchestrator.api.routes.mitigate._patcher",
            mock_patcher,
        ):
            response = client.post(
                "/api/v1/mitigate",
                json={**sample_mitigate_payload, "tier": 2},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "blocked"
        assert data["tier"] == 2
        mock_patcher.add_deny_rule.assert_called_once_with("1.2.3.4")

    def test_invalid_ip_returns_422(self, client, auth_headers):
        response = client.post(
            "/api/v1/mitigate",
            json={"target_ip": "not-an-ip", "reason": "test", "tier": 1},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestBlocklistEndpoint:
    def test_get_blocklist_returns_list(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.scan    = AsyncMock(return_value=(0, []))

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.get(
                "/api/v1/blocklist",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_unblock_nonexistent_returns_404(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=False)

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.delete(
                "/api/v1/blocklist/1.2.3.4",
                headers=auth_headers,
            )

        assert response.status_code == 404


class TestWhitelistEndpoint:
    def test_add_to_whitelist(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock(return_value=1)

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.post(
                "/api/v1/whitelist",
                json={"ip": "1.2.3.4", "reason": "trusted partner"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert "1.2.3.4" in response.json()["message"]

    def test_remove_from_whitelist(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=1)

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.delete(
                "/api/v1/whitelist/1.2.3.4",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_remove_nonexistent_from_whitelist_returns_404(
        self, client, auth_headers
    ):
        mock_redis = AsyncMock()
        mock_redis.srem = AsyncMock(return_value=0)

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.delete(
                "/api/v1/whitelist/9.9.9.9",
                headers=auth_headers,
            )

        assert response.status_code == 404

    def test_get_whitelist(self, client, auth_headers):
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"1.2.3.4", "5.6.7.8"})

        with patch(
            "services.worker_orchestrator.api.routes.blocklist.get_redis",
            return_value=mock_redis,
        ):
            response = client.get(
                "/api/v1/whitelist",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert "whitelisted_ips" in data
        assert "1.2.3.4" in data["whitelisted_ips"]
