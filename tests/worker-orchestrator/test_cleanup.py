from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.worker_orchestrator.orchestrator.cleanup import (
    cleanup_expired_mitigations,
)


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.scan = AsyncMock(return_value=(0, ["mitigation:1.2.3.4"]))
    redis.ttl = AsyncMock(return_value=-2)
    return redis


class TestCleanupExpiredMitigations:
    @pytest.mark.anyio
    async def test_removes_expired_ip_from_both_configmaps(self, mock_redis):
        mock_patcher = MagicMock()
        mock_patcher.remove_rule.return_value = True
        mock_ratelimit_patcher = MagicMock()
        mock_ratelimit_patcher.remove_rule.return_value = False

        with patch(
            "services.worker_orchestrator.orchestrator.cleanup.get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.ConfigMapPatcher",
            side_effect=[mock_patcher, mock_ratelimit_patcher],
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.update_mitigation_metrics",
            new_callable=AsyncMock,
        ):
            await cleanup_expired_mitigations()

        mock_patcher.remove_rule.assert_called_once_with("1.2.3.4")
        mock_ratelimit_patcher.remove_rule.assert_called_once_with("1.2.3.4")

    @pytest.mark.anyio
    async def test_flushes_both_patchers_once_per_run(self, mock_redis):
        mock_patcher = MagicMock()
        mock_ratelimit_patcher = MagicMock()

        with patch(
            "services.worker_orchestrator.orchestrator.cleanup.get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.ConfigMapPatcher",
            side_effect=[mock_patcher, mock_ratelimit_patcher],
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.update_mitigation_metrics",
            new_callable=AsyncMock,
        ):
            await cleanup_expired_mitigations()

        mock_patcher.flush.assert_called_once()
        mock_ratelimit_patcher.flush.assert_called_once()

    @pytest.mark.anyio
    async def test_non_expired_key_is_untouched(self, mock_redis):
        mock_redis.ttl = AsyncMock(return_value=300)
        mock_patcher = MagicMock()
        mock_ratelimit_patcher = MagicMock()

        with patch(
            "services.worker_orchestrator.orchestrator.cleanup.get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.ConfigMapPatcher",
            side_effect=[mock_patcher, mock_ratelimit_patcher],
        ), patch(
            "services.worker_orchestrator.orchestrator.cleanup.update_mitigation_metrics",
            new_callable=AsyncMock,
        ):
            await cleanup_expired_mitigations()

        mock_patcher.remove_rule.assert_not_called()
        mock_ratelimit_patcher.remove_rule.assert_not_called()
