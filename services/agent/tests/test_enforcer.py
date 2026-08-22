from services.agent.enforcer import DecisionStore, detect_adapters
from services.agent.enforcer.iptables_adapter import IptablesAdapter
from services.agent.enforcer.nginx_adapter import NginxAdapter


class _FakeResult:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _fake_run_ok(*a, **kw):
    return _FakeResult(0)


def _fake_run_fail(*a, **kw):
    return _FakeResult(1)


# --- NginxAdapter ---

def test_nginx_adapter_available_when_config_dir_exists(tmp_path):
    adapter = NginxAdapter(config_dir=tmp_path, run_command=_fake_run_ok)
    assert adapter.is_available() is True


def test_nginx_adapter_unavailable_when_config_dir_missing(tmp_path):
    adapter = NginxAdapter(config_dir=tmp_path / "does-not-exist", run_command=_fake_run_ok)
    assert adapter.is_available() is False


def test_nginx_adapter_hard_block_writes_deny_and_reloads(tmp_path):
    reload_calls = []

    def fake_run(cmd, **kw):
        reload_calls.append(cmd)
        return _FakeResult(0)

    adapter = NginxAdapter(config_dir=tmp_path, run_command=fake_run)
    assert adapter.block("1.2.3.4", tier=2, expires_at=0) is True
    content = (tmp_path / "aiops-agent-deny.conf").read_text()
    assert "deny 1.2.3.4;" in content
    assert len(reload_calls) == 1


def test_nginx_adapter_rate_limit_writes_geo_map(tmp_path):
    adapter = NginxAdapter(config_dir=tmp_path, run_command=_fake_run_ok)
    adapter.block("9.9.9.9", tier=1, expires_at=0)
    content = (tmp_path / "aiops-agent-geo.conf").read_text()
    assert "geo $binary_remote_addr $agent_rate_limited" in content
    assert "9.9.9.9 1;" in content


def test_nginx_adapter_unblock_removes_ip_and_reloads(tmp_path):
    adapter = NginxAdapter(config_dir=tmp_path, run_command=_fake_run_ok)
    adapter.block("1.2.3.4", tier=2, expires_at=0)
    assert adapter.unblock("1.2.3.4") is True
    content = (tmp_path / "aiops-agent-deny.conf").read_text()
    assert "1.2.3.4" not in content


def test_nginx_adapter_reload_failure_propagates(tmp_path):
    adapter = NginxAdapter(config_dir=tmp_path, run_command=_fake_run_fail)
    assert adapter.block("1.2.3.4", tier=2, expires_at=0) is False


# --- IptablesAdapter ---

def test_iptables_adapter_available_when_binary_works():
    adapter = IptablesAdapter(run_command=_fake_run_ok)
    assert adapter.is_available() is True


def test_iptables_adapter_unavailable_when_binary_missing():
    def raise_not_found(*a, **kw):
        raise FileNotFoundError()
    adapter = IptablesAdapter(run_command=raise_not_found)
    assert adapter.is_available() is False


def test_iptables_adapter_blocks_hard_block_tier():
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _FakeResult(0)

    adapter = IptablesAdapter(run_command=fake_run)
    assert adapter.block("1.2.3.4", tier=2, expires_at=0) is True
    assert calls[0][:2] == ["iptables", "-A"]
    assert "1.2.3.4" in calls[0]


def test_iptables_adapter_refuses_rate_limit_tier():
    adapter = IptablesAdapter(run_command=_fake_run_ok)
    assert adapter.block("1.2.3.4", tier=1, expires_at=0) is False


def test_iptables_adapter_rejects_invalid_ip():
    adapter = IptablesAdapter(run_command=_fake_run_ok)
    import pytest
    with pytest.raises(ValueError):
        adapter.block("not-an-ip", tier=2, expires_at=0)


# --- detect_adapters / DecisionStore ---

def test_detect_adapters_keeps_only_available_ones():
    class _Available:
        name = "a"
        def is_available(self): return True
        def block(self, *a): return True
        def unblock(self, *a): return True

    class _Unavailable:
        name = "b"
        def is_available(self): return False
        def block(self, *a): return True
        def unblock(self, *a): return True

    result = detect_adapters([_Available(), _Unavailable()])
    assert [a.name for a in result] == ["a"]


def test_detect_adapters_skips_adapter_whose_check_raises():
    class _Raises:
        name = "raises"
        def is_available(self): raise RuntimeError("boom")

    assert detect_adapters([_Raises()]) == []


def test_decision_store_applies_to_every_adapter():
    calls = []

    class _Recorder:
        name = "rec"
        def block(self, ip, tier, expires_at):
            calls.append((ip, tier, expires_at))
            return True
        def unblock(self, ip):
            return True

    store = DecisionStore()
    store.apply([{"ip": "1.1.1.1", "tier": 2, "expires_at": 9999999999}], [_Recorder()])
    assert calls == [("1.1.1.1", 2, 9999999999)]
    assert store.active_ips() == ["1.1.1.1"]


def test_decision_store_sweep_unblocks_expired_only():
    unblocked = []

    class _Recorder:
        name = "rec"
        def block(self, *a):
            return True
        def unblock(self, ip):
            unblocked.append(ip)
            return True

    store = DecisionStore()
    store.apply([
        {"ip": "1.1.1.1", "tier": 2, "expires_at": 1000},
        {"ip": "2.2.2.2", "tier": 2, "expires_at": 5000},
    ], [_Recorder()])

    expired = store.sweep_expired([_Recorder()], now=2000)
    assert expired == ["1.1.1.1"]
    assert unblocked == ["1.1.1.1"]
    assert store.active_ips() == ["2.2.2.2"]
