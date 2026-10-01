"""One tenant, opened.

The tenant table answers "who exists". It cannot answer the question an
operator actually arrives with, which is "is this customer protected right
now, and what happens if I suspend them". Suspension revokes every agent key
the tenant holds; the console offered that button without ever showing how
many keys that was.

The pane costs one extra Query, and only when a tenant is selected: agents
are partitioned by tenant_id, so this is the same read the customer's own
console makes, on the same table, with no GSI. The list page is unchanged.
"""
import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

# Relative to when the row is written, never to import time.
#
# This was a module-level constant, and liveness is derived by comparing
# last_seen_at against the clock AT REQUEST TIME with a five-minute cutoff.
# On a nine-minute suite run the agent seeded "20 giây trước" was already
# nine minutes stale by the time its test executed, so it read as Quiet and
# the assertion failed - in the full run only, never when the file was run
# on its own.
def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})})
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="Globex", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return c


def _agent(resource, tenant_id, agent_id, label, ago, status="active"):
    AgentsTable(resource).put(
        tenant_id=tenant_id, agent_id=agent_id, agent_label=label,
        registered_at=(_now() - timedelta(days=9)).isoformat(),
        last_seen_at=(_now() - ago).isoformat(),
        agent_version="1.4.0", api_key_hash="h", status=status)


def test_no_selection_leaves_the_table_alone(client):
    page = client.get("/admin/ui/tenants").text
    assert "Tenant detail" not in page
    assert "Acme" in page


def test_selecting_a_tenant_opens_its_detail(client):
    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "Chi tiết khách hàng" in page
    assert "Acme" in page


def test_an_unknown_id_does_not_open_an_empty_pane(client):
    page = client.get("/admin/ui/tenants?id=t-missing").text
    assert "Tenant detail" not in page


def test_the_pane_names_the_agents_that_suspension_would_cut_off(client, dynamo_resource):
    """The count is the consequence of the button sitting next to it."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-1", "a-2", "web-02", timedelta(seconds=30))

    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "web-01" in page
    assert "web-02" in page


def test_the_pane_counts_only_this_tenants_agents(client, dynamo_resource):
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-2", "a-9", "globex-01", timedelta(seconds=20))

    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "globex-01" not in page


def test_a_tenant_whose_agents_are_all_quiet_says_so(client, dynamo_resource):
    """An operator about to suspend a customer should be told the customer
    was already unprotected. It changes what the support ticket says."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(hours=6))

    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "không được bảo vệ" in page


def test_a_tenant_with_no_agents_at_all_is_not_a_blank_list(client):
    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "Chưa có agent nào đăng ký" in page


def test_the_pane_offers_suspend_for_an_active_tenant(client):
    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "/admin/ui/tenants/t-1/suspend?back=detail" in page


def test_the_pane_offers_reactivate_for_a_suspended_one(client, dynamo_resource):
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="suspended", created_at="2026-08-21T00:00:00Z")
    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "/admin/ui/tenants/t-1/reactivate?back=detail" in page
    assert "suspend?back=detail" not in page


def test_the_suspend_confirmation_states_how_many_keys_it_revokes(client, dynamo_resource):
    """"Every one of its API keys" is true and unquantified. The operator is
    about to take an action whose size the console already knows."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-1", "a-2", "web-02", timedelta(seconds=20))

    page = client.get("/admin/ui/tenants?id=t-1").text
    assert "2 khoá agent" in page
