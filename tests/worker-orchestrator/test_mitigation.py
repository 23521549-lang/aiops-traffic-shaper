import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.worker_orchestrator.schemas.mitigation import (
    MitigationRequest,
    MitigationTier,
)
from services.worker_orchestrator.orchestrator.configmap_patcher import ConfigMapPatcher


class TestMitigationRequest:
    def test_valid_ipv4(self):
        req = MitigationRequest(
            target_ip="1.2.3.4",
            reason="test",
            tier=MitigationTier.RATE_LIMIT,
        )
        assert req.target_ip == "1.2.3.4"

    def test_valid_ipv6(self):
        req = MitigationRequest(
            target_ip="::1",
            reason="test",
            tier=MitigationTier.HARD_BLOCK,
        )
        assert req.target_ip == "::1"

    def test_invalid_ip_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MitigationRequest(
                target_ip="not-an-ip",
                reason="test",
                tier=MitigationTier.RATE_LIMIT,
            )

    def test_default_tier_is_rate_limit(self):
        req = MitigationRequest(target_ip="1.2.3.4", reason="test")
        assert req.tier == MitigationTier.RATE_LIMIT

    def test_tier1_value(self):
        assert MitigationTier.RATE_LIMIT == 1

    def test_tier2_value(self):
        assert MitigationTier.HARD_BLOCK == 2


class TestConfigMapPatcher:
    def _make_patcher(
        self,
        existing_data: dict,
        rule_template: str = "deny {ip};",
    ) -> ConfigMapPatcher:
        patcher = ConfigMapPatcher.__new__(ConfigMapPatcher)
        patcher.namespace        = "aiops"
        patcher.configmap_name   = "nginx-blocklist"
        patcher.rule_template    = rule_template
        patcher.debounce_seconds = 3.0
        patcher._pending         = {}
        patcher._flush_handle    = None

        mock_cm       = MagicMock()
        mock_cm.data  = existing_data

        mock_v1 = MagicMock()
        mock_v1.read_namespaced_config_map.return_value  = mock_cm
        mock_v1.patch_namespaced_config_map.return_value = MagicMock()

        patcher.v1 = mock_v1
        return patcher

    def test_add_rule_new_ip(self):
        patcher = self._make_patcher({})
        result  = patcher.add_rule("1.2.3.4")
        assert result is True
        patcher.flush()
        patcher.v1.patch_namespaced_config_map.assert_called_once()

    def test_add_rule_duplicate_skips(self):
        patcher = self._make_patcher({"1_2_3_4": "deny 1.2.3.4;"})
        result  = patcher.add_rule("1.2.3.4")
        assert result is False
        patcher.flush()
        patcher.v1.patch_namespaced_config_map.assert_not_called()

    def test_remove_rule_existing(self):
        patcher = self._make_patcher({"1_2_3_4": "deny 1.2.3.4;"})
        result  = patcher.remove_rule("1.2.3.4")
        assert result is True
        patcher.flush()
        patcher.v1.patch_namespaced_config_map.assert_called_once()

    def test_remove_rule_nonexistent(self):
        patcher = self._make_patcher({})
        result  = patcher.remove_rule("1.2.3.4")
        assert result is False
        patcher.flush()
        patcher.v1.patch_namespaced_config_map.assert_not_called()

    def test_count_rules_empty(self):
        patcher = self._make_patcher({})
        assert patcher.count_rules() == 0

    def test_count_rules_multiple(self):
        data = {
            "1_2_3_4": "deny 1.2.3.4;",
            "5_6_7_8": "deny 5.6.7.8;",
        }
        patcher = self._make_patcher(data)
        assert patcher.count_rules() == 2

    def test_ip_key_format(self):
        patcher = self._make_patcher({})
        patcher.add_rule("192.168.1.100")
        patcher.flush()
        call_args = patcher.v1.patch_namespaced_config_map.call_args
        body = call_args[1]["body"]
        assert "192_168_1_100" in body["data"]

    def test_deny_rule_format(self):
        patcher = self._make_patcher({})
        patcher.add_rule("10.0.0.1")
        patcher.flush()
        call_args = patcher.v1.patch_namespaced_config_map.call_args
        body = call_args[1]["body"]
        assert body["data"]["10_0_0_1"] == "deny 10.0.0.1;"

    def test_ratelimit_rule_uses_custom_template(self):
        patcher = self._make_patcher({}, rule_template="{ip} 1;")
        patcher.add_rule("10.0.0.1")
        patcher.flush()
        call_args = patcher.v1.patch_namespaced_config_map.call_args
        body = call_args[1]["body"]
        assert body["data"]["10_0_0_1"] == "10.0.0.1 1;"

    def test_pending_changes_coalesce_into_one_flush(self):
        patcher = self._make_patcher({})
        patcher.add_rule("1.2.3.4")
        patcher.add_rule("5.6.7.8")
        # Neither add_rule call flushes immediately — only one patch call
        # should happen, once flush() runs (simulating the debounce timer
        # firing after both changes queued up).
        patcher.v1.patch_namespaced_config_map.assert_not_called()

        patcher.flush()

        patcher.v1.patch_namespaced_config_map.assert_called_once()
        body = patcher.v1.patch_namespaced_config_map.call_args[1]["body"]
        assert body["data"]["1_2_3_4"] == "deny 1.2.3.4;"
        assert body["data"]["5_6_7_8"] == "deny 5.6.7.8;"

    def test_flush_with_no_pending_changes_is_noop(self):
        patcher = self._make_patcher({})
        patcher.flush()
        patcher.v1.patch_namespaced_config_map.assert_not_called()

    def test_add_then_remove_same_ip_before_flush_removes_it(self):
        patcher = self._make_patcher({"1_2_3_4": "deny 1.2.3.4;"})
        patcher.remove_rule("1.2.3.4")
        patcher.add_rule("5.6.7.8")
        patcher.flush()

        body = patcher.v1.patch_namespaced_config_map.call_args[1]["body"]
        assert "1_2_3_4" not in body["data"]
        assert body["data"]["5_6_7_8"] == "deny 5.6.7.8;"
