import time

import numpy as np
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, create_all_tables
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token

# The raw key every test sends in X-Agent-Key: t-1.<this> — agent_auth
# hashes it and compares to the stored api_key_hash below. Found while
# wiring Stage 8's /agent/v1/register: this file used to store the
# literal string "testkeyhash" as api_key_hash and also send it as the
# raw key, which only worked because agent_auth never actually hashed
# anything at the time (the bug fixed alongside this test update).
_TEST_RAW_KEY = "testkeyhash"


def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", registered_at="2026-08-21T00:00:00Z",
        last_seen_at="2026-08-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key(_TEST_RAW_KEY), status="active",
    )
    # Correct FastAPI DI override — swaps the resource for every route that
    # declares Depends(get_dynamo_resource), unlike the plan's original
    # closure-based `dynamo_resource_override()` idea, which would not have
    # affected an already-built router. See api/dependencies.py.
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app)


def test_register_agent_returns_usable_key(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app)

    owner_token = sign_test_token(cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})
    resp = client.post("/agent/v1/register", json={"agent_label": "prod-web-1"},
                        headers={"Authorization": f"Bearer {owner_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "t-1"
    assert body["agent_id"]
    assert body["api_key"]

    # The returned raw api_key must actually authenticate against the
    # agent-facing API — this is what proves hash_api_key(raw) really
    # matches what got stored, not just that the response looked right.
    resp2 = client.post("/agent/v1/telemetry", json={"logs": []},
                         headers={"X-Agent-Key": f"{body['tenant_id']}.{body['api_key']}"})
    assert resp2.status_code == 200


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


def test_telemetry_anomaly_decision_has_real_ttl_expiry(dynamo_resource):
    # Found while designing Stage 8's agent enforcer: this route used to
    # hardcode expires_at=0 for every decision, which gives an agent no
    # real TTL to schedule an auto-unblock from. Injects a fake model
    # directly into ModelManager's cache (Stage 3) to force a real
    # anomalous score deterministically, rather than needing a fully
    # trained model to happen to classify test traffic as anomalous.
    client = _client(dynamo_resource)

    class _AlwaysHardBlock:
        def decision_function(self, X):
            return np.full(len(X), -0.5)  # well past HARD_BLOCK's -0.3 threshold

    ModelManager._cache["t-1"] = _AlwaysHardBlock()
    try:
        logs = [{
            "time_iso8601": "2026-08-21T00:00:00Z", "remote_addr": "6.6.6.6",
            "request_method": "GET", "request_uri": "/a", "status": "200",
            "body_bytes_sent": "512", "request_time": "0.05",
            "http_user_agent": "ua-1",
        }] * 5
        before = int(time.time())
        resp = client.post("/agent/v1/telemetry", json={"logs": logs},
                            headers={"X-Agent-Key": "t-1.testkeyhash"})
        assert resp.status_code == 200
        decisions = resp.json()["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["tier"] == 2
        # Real future expiry (HARD_BLOCK TTL = 3600s), not the old hardcoded 0
        assert before + 3600 <= decisions[0]["expires_at"] <= before + 3600 + 5
    finally:
        ModelManager._cache.pop("t-1", None)


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
