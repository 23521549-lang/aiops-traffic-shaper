"""Tenant lifecycle: suspension is no longer one-way.

Phase 4 (H3) made suspension real - it blocks ingest, revokes every agent key
the tenant was ever issued, and refuses re-registration. But nothing undid it.
A tenant suspended by mistake, or suspended while a noisy agent was
investigated, stayed suspended until someone edited DynamoDB by hand. The
retrospective carried it as "a gap, not a policy".

Reactivation restores the TENANT and deliberately not its old KEYS. Suspension
revokes keys because the reason for suspending may be that a key leaked;
bringing those keys back would quietly re-open exactly the door suspension
closed. After reactivation the tenant registers fresh agents and gets fresh
keys - the only way to be sure no leaked key survives.
"""
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

_LOGS = [{
    "time_iso8601": "2026-09-21T00:00:00Z", "remote_addr": "1.2.3.4",
    "request_method": "GET", "request_uri": "/a", "status": "200",
    "body_bytes_sent": "512", "request_time": "0.05", "http_user_agent": "ua-1",
}]


def _client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-09-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-old", registered_at="2026-09-21T00:00:00Z",
        last_seen_at="2026-09-21T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key("old-key"), status="active",
    )
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    return TestClient(app, base_url="https://testserver")


def _admin(keys):
    return {"X-Id-Token": sign_test_token(keys["private_pem"], {"cognito:groups": ["admin"]})}


def _owner(keys):
    return {"X-Id-Token": sign_test_token(keys["private_pem"], {"custom:tenant_id": "t-1"})}


def test_suspension_is_no_longer_one_way(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    assert client.post("/admin/v1/tenants/t-1/suspend",
                       headers=_admin(cognito_test_keys)).status_code == 200

    resp = client.post("/admin/v1/tenants/t-1/reactivate", headers=_admin(cognito_test_keys))

    assert resp.status_code == 200
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "active"


def test_a_reactivated_tenant_can_register_a_fresh_agent(dynamo_resource, cognito_test_keys):
    """While suspended, even a still-valid dashboard token cannot mint a new
    agent (H3). After reactivation it must be able to again - otherwise
    reactivation restores a tenant that can never actually be protected."""
    client = _client(dynamo_resource, cognito_test_keys)
    client.post("/admin/v1/tenants/t-1/suspend", headers=_admin(cognito_test_keys))
    client.post("/admin/v1/tenants/t-1/reactivate", headers=_admin(cognito_test_keys))

    resp = client.post("/agent/v1/register", json={"agent_label": "fresh"},
                       headers=_owner(cognito_test_keys))
    assert resp.status_code == 200
    new_key = resp.json()["api_key"]

    ingest = client.post("/agent/v1/telemetry", json={"logs": _LOGS},
                         headers={"X-Agent-Key": f"t-1.{new_key}"})
    assert ingest.status_code == 200


def test_reactivation_does_not_resurrect_keys_that_suspension_revoked(dynamo_resource,
                                                                      cognito_test_keys):
    """The security property of this whole feature. If the reason for the
    suspension was a leaked agent key, handing that key its access back on
    reactivation would silently undo the suspension's only real effect."""
    client = _client(dynamo_resource, cognito_test_keys)
    client.post("/admin/v1/tenants/t-1/suspend", headers=_admin(cognito_test_keys))
    client.post("/admin/v1/tenants/t-1/reactivate", headers=_admin(cognito_test_keys))

    resp = client.post("/agent/v1/telemetry", json={"logs": _LOGS},
                       headers={"X-Agent-Key": "t-1.old-key"})
    assert resp.status_code == 401
    old = AgentsTable(dynamo_resource).get(tenant_id="t-1", agent_id="a-old")
    assert old["status"] != "active"


def test_reactivating_a_missing_tenant_is_404_and_creates_nothing(dynamo_resource,
                                                                 cognito_test_keys):
    """The same trap suspend() already fell into once: DynamoDB's UpdateItem
    CREATES an item when the key is absent, so an unconditional reactivate
    would conjure a half-formed tenant record out of a typo."""
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/admin/v1/tenants/no-such/reactivate", headers=_admin(cognito_test_keys))

    assert resp.status_code == 404
    assert TenantsTable(dynamo_resource).get(tenant_id="no-such") is None


def test_reactivate_requires_the_admin_group(dynamo_resource, cognito_test_keys):
    """A suspended tenant's own owner must not be able to lift their own
    suspension - that is the whole point of it being an admin action."""
    client = _client(dynamo_resource, cognito_test_keys)
    client.post("/admin/v1/tenants/t-1/suspend", headers=_admin(cognito_test_keys))

    resp = client.post("/admin/v1/tenants/t-1/reactivate", headers=_owner(cognito_test_keys))

    assert resp.status_code == 403
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "suspended"


def test_reactivating_an_active_tenant_is_a_harmless_no_op(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    resp = client.post("/admin/v1/tenants/t-1/reactivate", headers=_admin(cognito_test_keys))

    assert resp.status_code == 200
    assert TenantsTable(dynamo_resource).get(tenant_id="t-1")["status"] == "active"


def test_suspension_reports_only_the_keys_it_actually_revoked(dynamo_resource,
                                                             cognito_test_keys):
    """Found on production: suspending a tenant with one live agent and one
    agent revoked long before reported agents_revoked=2. The operator reading
    that number is deciding how bad an incident was; it must count what this
    suspension did, not what the table happens to contain. Re-revoking also
    spent a write per dead agent against a 25 WCU budget for nothing."""
    client = _client(dynamo_resource, cognito_test_keys)
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-dead", registered_at="2026-09-01T00:00:00Z",
        last_seen_at="2026-09-01T00:00:00Z", agent_version="0.1.0",
        api_key_hash=hash_api_key("dead-key"), status="revoked",
    )

    resp = client.post("/admin/v1/tenants/t-1/suspend", headers=_admin(cognito_test_keys))

    assert resp.json()["agents_revoked"] == 1
