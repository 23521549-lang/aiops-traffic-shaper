"""Phase 5 E2E — the agent's OWN code driving the real backend.

`webapp-testing` (Playwright) is not installed, so Phase 5 runs degraded on the
pattern Stage 9 already proved in this project: the real ASGI app, real routes,
real auth, real DynamoDB (moto), real ModelManager cache, and the agent's real
Collector / CLI / DecisionStore / NginxAdapter. The ONLY thing faked is the OS
boundary the agent shells out to — `nginx -s reload` — because running it for
real needs root and a live nginx. Every "verified" claim below is true up to
exactly that boundary and no further.

Covers the Must-story acceptance criteria in docs/PRD.md that unit tests could
not reach: US-3 (telemetry out, decision back, and surviving a dead backend),
US-4 (no cross-tenant leakage along the agent path), US-5 (one command to
register, and the key it issues actually works).
"""
import json

import numpy as np
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from services.agent.cli import cli
from services.agent.collector import Collector, LogRecord
from services.agent.enforcer import DecisionStore
from services.agent.enforcer.nginx_adapter import NginxAdapter
from services.agent.http_client import BackendError
from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token


class _FakeRun:
    """Stands in for subprocess.run — the one mocked boundary in this file."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return type("R", (), {"returncode": 0})()


@pytest.fixture
def backend(dynamo_resource, cognito_test_keys):
    """The real app, wired to moto DynamoDB and a locally generated keypair."""
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")

    def post_json(url, payload, headers=None):
        """Routes the agent's own HTTP call into the real ASGI app."""
        path = url.replace("https://testserver", "")
        resp = client.post(path, json=payload, headers=headers or {})
        if resp.status_code >= 400:
            raise BackendError(f"{resp.status_code}: {resp.text}")
        return resp.json()

    return {"client": client, "post_json": post_json, "resource": dynamo_resource,
            "keys": cognito_test_keys}


def _log(ip="6.6.6.6"):
    return LogRecord(time_iso8601="2026-08-21T00:00:00Z", remote_addr=ip,
                     request_method="GET", request_uri="/a", status="200",
                     body_bytes_sent="512", request_time="0.05", http_user_agent="ua-1")


def _force_anomaly(tenant_id="t-1"):
    """Inject a deterministic always-anomalous model into ModelManager's cache,
    the same technique test_agent_routes uses — a genuinely trained forest
    would make the assertions depend on chance."""
    class _AlwaysHardBlock:
        def decision_function(self, X):
            return np.full(len(X), -0.5)  # well past HARD_BLOCK's -0.3 threshold

    # The cache holds (model, stats): tiers are measured in standard
    # deviations from the training mean, so the stats travel with the
    # model. None here means "no usable spread" and the classifier falls
    # back to the absolute thresholds, which is what this stub wants.
    ModelManager._cache[tenant_id] = (_AlwaysHardBlock(), None)


def _registered_collector(backend, tenant_id="t-1"):
    AgentsTable(backend["resource"]).put(
        tenant_id=tenant_id, agent_id=f"a-{tenant_id}", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key("rawkey"), status="active",
    )
    return Collector("https://testserver", tenant_id, "rawkey",
                     post_json_fn=backend["post_json"])


# --- US-5: one command registers the agent, and the key really works -----

def test_us5_cli_register_yields_a_key_that_authenticates(backend, tmp_path, monkeypatch):
    """AC: "một lệnh cài + một lệnh đăng ký kết nối agent với backend". Unit
    tests only proved the CLI writes back whatever the backend returned; this
    proves what it wrote is a credential the backend then accepts."""
    monkeypatch.setattr("services.agent.cli.post_json", backend["post_json"])
    token = sign_test_token(backend["keys"]["private_pem"], {"custom:tenant_id": "t-1"})
    config_path = tmp_path / "config.json"

    result = CliRunner().invoke(cli, ["register", "--backend-url", "https://testserver",
                                      "--token", token, "--label", "prod-web-1",
                                      "--config-path", str(config_path)])
    assert result.exit_code == 0, result.output

    config = json.loads(config_path.read_text())
    collector = Collector("https://testserver", config["tenant_id"], config["api_key"],
                          post_json_fn=backend["post_json"])
    collector.add(_log())
    assert collector.flush()["received"] == 1  # the freshly issued key is accepted


def test_us5_cli_reports_a_failed_registration_clearly(backend, tmp_path, monkeypatch):
    """AC: "CLI báo được trạng thái kết nối (thành công/lỗi) rõ ràng" — edge."""
    monkeypatch.setattr("services.agent.cli.post_json", backend["post_json"])
    bad_token = sign_test_token(backend["keys"]["private_pem"], {"sub": "no-tenant-claim"})
    result = CliRunner().invoke(cli, ["register", "--backend-url", "https://testserver",
                                      "--token", bad_token,
                                      "--config-path", str(tmp_path / "c.json")])
    assert result.exit_code != 0
    assert "Registration failed" in result.output
    assert not (tmp_path / "c.json").exists()  # nothing half-written


# --- US-3: telemetry out, decision back, enforcement applied -------------

def test_us3_full_loop_from_telemetry_to_an_enforced_nginx_deny(backend, tmp_path):
    """AC: "Agent gửi được telemetry tới backend trung tâm và nhận lại quyết
    định". The whole chain in one test: the agent's Collector batches real log
    records, the real backend scores them, the decision returns over the real
    response, the agent's real DecisionStore hands it to the real NginxAdapter,
    and a real deny file lands on disk."""
    _force_anomaly()
    collector = _registered_collector(backend)
    runner = _FakeRun()
    adapter = NginxAdapter(config_dir=tmp_path, run_command=runner)
    store = DecisionStore()

    for _ in range(5):
        collector.add(_log("6.6.6.6"))
    decisions = collector.flush()["decisions"]

    assert len(decisions) == 1
    assert decisions[0]["tier"] == 2
    assert decisions[0]["expires_at"] > 0  # a real TTL, not the old hardcoded 0

    store.apply(decisions, [adapter])
    assert "deny 6.6.6.6;" in (tmp_path / "aiops-agent-deny.conf").read_text()
    assert runner.calls == [["nginx", "-s", "reload"]]

    # ...and the block lifts itself once the TTL passes. Nothing external ever
    # removes an nginx deny line, so this is the agent's own responsibility.
    assert store.sweep_expired([adapter], now=decisions[0]["expires_at"] + 1) == ["6.6.6.6"]
    assert "6.6.6.6" not in (tmp_path / "aiops-agent-deny.conf").read_text()


def test_us3_agent_keeps_enforcing_when_the_backend_is_unreachable(backend, tmp_path):
    """AC: "Agent hoạt động độc lập nếu backend trung tâm tạm thời không phản
    hồi". Never tested at any level before. A dead backend must not drop blocks
    already in force, and must not take the agent down with it."""
    runner = _FakeRun()
    adapter = NginxAdapter(config_dir=tmp_path, run_command=runner)
    store = DecisionStore()
    store.apply([{"ip": "7.7.7.7", "tier": 2, "expires_at": 9999999999}], [adapter])

    def dead_backend(url, payload, headers=None):
        raise BackendError("connection refused")

    collector = Collector("https://testserver", "t-1", "rawkey", post_json_fn=dead_backend)
    collector.add(_log())
    with pytest.raises(BackendError):
        collector.flush()

    # the existing block stands, and local expiry still works with no backend
    assert "deny 7.7.7.7;" in (tmp_path / "aiops-agent-deny.conf").read_text()
    assert store.active_ips() == ["7.7.7.7"]
    assert store.sweep_expired([adapter], now=10000000000) == ["7.7.7.7"]


# --- US-4: no cross-tenant leakage along the agent path ------------------

def test_us4_an_agent_only_receives_its_own_tenants_decisions(backend):
    """AC: "Dữ liệu/telemetry của các tenant được cô lập với nhau". Already
    proven at the UI layer; this proves it on the path an attacker would
    actually hold credentials for — the agent API."""
    TenantsTable(backend["resource"]).put(tenant_id="t-2", name="Globex", status="active",
                                          created_at="2026-08-21T00:00:00Z")
    _force_anomaly("t-1")
    _force_anomaly("t-2")

    a1 = _registered_collector(backend, "t-1")
    a2 = _registered_collector(backend, "t-2")
    for _ in range(5):
        a1.add(_log("1.1.1.1"))
    for _ in range(5):
        a2.add(_log("2.2.2.2"))
    a1.flush()
    a2.flush()

    client = backend["client"]
    seen_by_t1 = client.get("/agent/v1/decisions", headers={"X-Agent-Key": "t-1.rawkey"}).json()
    seen_by_t2 = client.get("/agent/v1/decisions", headers={"X-Agent-Key": "t-2.rawkey"}).json()

    assert [d["ip"] for d in seen_by_t1] == ["1.1.1.1"]
    assert [d["ip"] for d in seen_by_t2] == ["2.2.2.2"]


def test_us4_suspending_a_tenant_cuts_its_agent_off_immediately(backend):
    """The Phase 4 fix, end to end: an agent working a moment ago is refused on
    its very next call once the publisher suspends the tenant."""
    collector = _registered_collector(backend)
    collector.add(_log())
    assert collector.flush()["received"] == 1

    admin = sign_test_token(backend["keys"]["private_pem"], {"cognito:groups": ["admin"]})
    resp = backend["client"].post("/admin/v1/tenants/t-1/suspend",
                                  headers={"Authorization": f"Bearer {admin}"})
    assert resp.status_code == 200
    assert resp.json()["agents_revoked"] == 1

    collector.add(_log())
    with pytest.raises(BackendError, match="403"):
        collector.flush()
