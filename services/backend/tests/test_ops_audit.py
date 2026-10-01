"""The last two of the four actions that earn a row.

Spec 2.5 names four: a threshold move, a whitelist add or remove, a tenant
suspend or reactivate, and an agent key mint or revoke. The test it sets is
whether a reasonable person could later dispute the action with money, blame
or security attached.

Phase 0 built the mechanism and wired the threshold move. Phase 1c wired the
whitelist pair, and found on the way that the sort key collapsed two changes
made in the same second into one row. These two have never been wired at all -
including suspension, which stops a customer's protection and revokes every
key they hold, and is the single most disputable thing this product can do.

The row lands on the AFFECTED TENANT's partition, not on an operator
partition. The question it answers is "what happened to this tenant", and it
has to come back from the same `query_settings` the rest of the product
already uses.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantHistoryTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


@pytest.fixture
def ops(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"cognito:groups": ["admin"], "email": "ops@example.com"})})
    return c


@pytest.fixture
def customer(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "owner@acme.example"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _audit(resource, tenant_id="t-1"):
    return TenantHistoryTable(resource).query_settings(
        tenant_id, 0, 2_000_000_000)


def test_suspending_a_tenant_is_recorded(ops, seeded):
    """It stops their protection and revokes every key they hold. If one
    action in this product earns a row, it is this one."""
    ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))

    rows = [r for r in _audit(seeded) if r["what"] == "tenant_status"]

    assert len(rows) == 1
    assert rows[0]["old"] == "active"
    assert rows[0]["new"] == "suspended"


def test_reactivating_is_recorded_too(ops, seeded):
    """A log that says a tenant was suspended and never says it came back is
    the more dangerous half of the pair."""
    ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))
    ops.post("/admin/ui/tenants/t-1/reactivate", headers=_csrf(ops))

    rows = [r for r in _audit(seeded) if r["what"] == "tenant_status"]

    assert [r["new"] for r in rows] == ["suspended", "active"]


def test_the_row_names_the_operator_from_the_verified_token(ops, seeded):
    """Never from a request field. That is what keeps the row fixed-size and
    what makes it worth anything in a dispute."""
    ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))

    assert _audit(seeded)[0]["actor"] == "ops@example.com"


def test_the_suspension_row_says_how_many_keys_it_revoked(ops, seeded):
    """Suspension revokes every key the tenant holds, and that is the part
    they will dispute: their agents stop working and stay stopped after
    reactivation."""
    now = datetime.now(timezone.utc).isoformat()
    for i in (1, 2):
        AgentsTable(seeded).put(
            tenant_id="t-1", agent_id=f"a-{i}", agent_label=f"web-{i}",
            registered_at=now, last_seen_at=now, agent_version="1.4.0",
            api_key_hash="h", status="active")

    ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))

    assert _audit(seeded)[0]["because"] == "2 agent keys revoked"


def test_minting_an_agent_key_is_recorded(customer, seeded):
    """A credential that can send telemetry as this tenant. Who minted it and
    when is the first question after one leaks."""
    customer.post("/dashboard/ui/agents?label=web-09", headers=_csrf(customer))

    rows = [r for r in _audit(seeded) if r["what"] == "agent_key"]

    assert len(rows) == 1
    assert rows[0]["actor"] == "owner@acme.example"
    assert rows[0]["new"] == "web-09"


def test_the_row_lands_on_the_affected_tenants_partition(ops, seeded):
    """The question it answers is "what happened to this tenant". A row on an
    operator partition could not be read by the query the rest of the product
    already uses."""
    TenantsTable(seeded).put(tenant_id="t-2", name="Globex", status="active",
                             created_at="2026-08-21T00:00:00Z")

    ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))

    assert _audit(seeded, "t-1")
    assert _audit(seeded, "t-2") == []


def test_a_throttled_audit_write_does_not_stop_the_suspension(ops, seeded, monkeypatch):
    """Same rule as every other reporting write. The ledger is worth having
    and it is not worth failing to suspend a tenant who is under attack."""
    from botocore.exceptions import ClientError

    def boom(*a, **kw):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "PutItem")

    monkeypatch.setattr(TenantHistoryTable, "record_setting", boom)

    response = ops.post("/admin/ui/tenants/t-1/suspend", headers=_csrf(ops))

    assert response.status_code == 200
    assert TenantsTable(seeded).get(tenant_id="t-1")["status"] == "suspended"


def test_suspending_a_tenant_that_does_not_exist_records_nothing(ops, seeded):
    """A refused action leaves no trace. A row saying a suspension happened
    when it did not is worse than no row."""
    ops.post("/admin/ui/tenants/t-nope/suspend", headers=_csrf(ops))

    assert _audit(seeded, "t-nope") == []


def test_all_four_audited_actions_are_now_wired():
    """Spec 2.5 names four. Three phases shipped with the mechanism built and
    some of them unwired, and nothing on any screen said which."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(p.read_text(encoding="utf-8")
                        for p in root.rglob("*.py")
                        if "tests" not in p.parts)

    for what in ("tier1_z", "whitelist_add", "whitelist_remove",
                 "tenant_status", "agent_key"):
        assert f'"{what}"' in sources, what
