"""The telemetry response carries the full active set, so the agent can
reconcile.

`decisions[]` is built from `touched_ips` - only addresses with traffic in
that batch. A blocked IP stops sending traffic, so reconciling against
`decisions[]` would unblock every attacker seconds after blocking them. The
full set costs one Query per batch and about 800 bytes for fifty addresses;
the alternative, polling /agent/v1/decisions every 60s, costs 69% of a
tenant's daily request share for four agents.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
)
from services.backend.main import app


@pytest.fixture
def client(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at="2026-09-01T00:00:00+00:00",
        last_seen_at="2026-09-01T00:00:00+00:00",
        agent_version="1.4.0", api_key_hash=hash_api_key("secret"), status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver")


def _log(ip="198.51.100.50"):
    return {"time_iso8601": "2026-09-24T10:00:00+07:00", "remote_addr": ip,
            "request_method": "GET", "request_uri": "/", "status": "200",
            "body_bytes_sent": "10", "request_time": "0.01",
            "http_user_agent": "curl/8.4.0"}


def _post(client, logs=None):
    """Always with at least one line.

    An empty batch returns at `agent.py:103` before any of this runs, and
    that path is deliberate: it exists so a no-op costs nothing. It is also
    unreachable from the real agent, which never POSTs an empty batch —
    `Collector.flush()` returns None when the buffer is empty. Testing
    through it would have tested nothing.
    """
    return client.post("/agent/v1/telemetry",
                       json={"logs": list(logs) if logs else [_log()]},
                       headers={"X-Agent-Key": "t-1.secret"})


def test_the_response_lists_every_active_mitigation(client, dynamo_resource):
    """Including ones with no traffic in this batch, which is every blocked
    IP, because blocked IPs stop sending traffic."""
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="203.0.113.7", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    body = _post(client).json()

    assert body["active_ips"] == ["203.0.113.7"]


def test_it_never_lists_another_tenants_addresses(client, dynamo_resource):
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-2", ip="198.51.100.9", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)

    assert _post(client).json()["active_ips"] == []


def test_an_empty_set_is_an_empty_list_not_a_missing_key(client):
    """The agent distinguishes "the backend told me nothing is served" from
    "this backend is too old to tell me". Only the second may be ignored."""
    body = _post(client).json()

    assert "active_ips" in body
    assert body["active_ips"] == []
