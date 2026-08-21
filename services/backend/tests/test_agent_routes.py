from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, create_all_tables
from services.backend.main import app


def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash="testkeyhash", status="active",
    )
    # Correct FastAPI DI override — swaps the resource for every route that
    # declares Depends(get_dynamo_resource), unlike the plan's original
    # closure-based `dynamo_resource_override()` idea, which would not have
    # affected an already-built router. See api/dependencies.py.
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app)


def test_telemetry_unauthenticated_is_401(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []})
    assert resp.status_code == 401


def test_telemetry_malformed_key_is_401(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []},
                        headers={"X-Agent-Key": "not-a-valid-key-format"})
    assert resp.status_code == 401


def test_telemetry_wrong_key_is_401(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []},
                        headers={"X-Agent-Key": "t-1.wrongkey"})
    assert resp.status_code == 401


def test_telemetry_empty_batch(dynamo_resource):
    client = _client(dynamo_resource)
    resp = client.post("/agent/v1/telemetry", json={"logs": []},
                        headers={"X-Agent-Key": "t-1.testkeyhash"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] == 0
    assert body["decisions"] == []


def test_telemetry_normal_traffic_no_decisions(dynamo_resource):
    client = _client(dynamo_resource)
    logs = [{
        "time_iso8601": "2026-08-21T00:00:00Z", "remote_addr": "1.2.3.4",
        "request_method": "GET", "request_uri": "/a", "status": "200",
        "body_bytes_sent": "512", "request_time": "0.05",
        "http_user_agent": "ua-1",
    }] * 3
    resp = client.post("/agent/v1/telemetry", json={"logs": logs},
                        headers={"X-Agent-Key": "t-1.testkeyhash"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["received"] == 3
    assert body["processed_ips"] == 1
    # No production model loaded for this tenant -> ModelManager stays in
    # shadow mode (score 0.0 for every vector) -> classify_score(0.0) is
    # NORMAL -> no decisions, matching Stage 2/3's shadow-mode behavior.
    assert body["decisions"] == []


def test_telemetry_whitelisted_ip_is_skipped(dynamo_resource):
    from services.backend.core.tables import WhitelistTable
    client = _client(dynamo_resource)  # creates tables first
    WhitelistTable(dynamo_resource).put(tenant_id="t-1", ip="9.9.9.9", added_at="2026-08-21T00:00:00Z")
    logs = [{
        "time_iso8601": "2026-08-21T00:00:00Z", "remote_addr": "9.9.9.9",
        "request_method": "GET", "request_uri": "/a", "status": "500",
        "body_bytes_sent": "512", "request_time": "0.05",
        "http_user_agent": "ua-1",
    }] * 5
    resp = client.post("/agent/v1/telemetry", json={"logs": logs},
                        headers={"X-Agent-Key": "t-1.testkeyhash"})
    assert resp.status_code == 200
    assert resp.json()["decisions"] == []


def test_decisions_endpoint_returns_active_mitigations(dynamo_resource):
    from services.backend.core.tables import MitigationStateTable
    client = _client(dynamo_resource)  # creates tables first
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="5.5.5.5", tier=2, score=-0.5,
        reason="behavioral_anomaly", expires_at=0,
    )
    resp = client.get("/agent/v1/decisions", headers={"X-Agent-Key": "t-1.testkeyhash"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ip"] == "5.5.5.5"
    assert body[0]["tier"] == 2
