"""A tenant can see its own agents.

The publisher console has listed agents since Phase 6. The customer console
never has, so a person whose site stopped being protected had exactly one
signal: a banner saying no telemetry had arrived, with no way to find out
which of their machines had gone quiet.

The data path is already proven. `AgentsTable.query_by_tenant` is one Query
on the base table's own partition key, no GSI and no scan, and
`/dashboard/ui` already calls it on every load to compute the health banner.
Everything below it was thrown away.

Liveness is derived here rather than queried: `LastSeenIndex` is partitioned
on `status` and is cross-tenant, so it cannot answer "which of MY agents",
and a tenant has single-digit agents. A per-tenant liveness GSI would mirror
every agent write against the same account-wide 25 WCU pool to save a loop
over four items.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

NOW = datetime.now(timezone.utc)


def _agent(resource, tenant_id, agent_id, label, ago, status="active", version="1.4.0"):
    AgentsTable(resource).put(
        tenant_id=tenant_id, agent_id=agent_id, agent_label=label,
        registered_at=(NOW - timedelta(days=30)).isoformat(),
        last_seen_at=(NOW - ago).isoformat(),
        agent_version=version, api_key_hash="h", status=status,
    )


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def test_a_tenant_sees_its_own_agents(client, dynamo_resource):
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-1", "a-2", "web-02", timedelta(hours=4))

    page = client.get("/dashboard/ui/agents").text
    assert "web-01" in page
    assert "web-02" in page


def test_it_sees_nobody_else(client, dynamo_resource):
    """The guarantee the whole product rests on, on a new read path."""
    _agent(dynamo_resource, "t-2", "a-9", "someone-elses-box", timedelta(seconds=5))

    page = client.get("/dashboard/ui/agents").text
    assert "someone-elses-box" not in page


def test_a_quiet_agent_is_named_as_quiet(client, dynamo_resource):
    """The whole reason for the screen. "No telemetry" without naming the
    machine leaves the customer guessing which server to go and look at."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-1", "a-2", "staging-01", timedelta(hours=9))

    page = client.get("/dashboard/ui/agents").text
    assert "Reporting" in page
    assert "Quiet" in page


def test_an_agent_is_listed_by_the_name_its_owner_gave_it(client, dynamo_resource):
    """A uuid tells the customer nothing about which box to walk over to."""
    _agent(dynamo_resource, "t-1", "a-7f3c2b", "web-01 (nginx)", timedelta(seconds=10))

    page = client.get("/dashboard/ui/agents").text
    assert "web-01 (nginx)" in page


def test_selecting_an_agent_opens_its_detail(client, dynamo_resource):
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))

    page = client.get("/dashboard/ui/agents?id=a-1").text
    assert "Agent detail" in page
    assert "a-1" in page
    assert "1.4.0" in page


def test_an_unknown_id_does_not_open_a_pane(client, dynamo_resource):
    """A detail pane for an object that does not exist would be a blank
    panel with an action bar attached to nothing."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))

    page = client.get("/dashboard/ui/agents?id=does-not-exist").text
    assert "Agent detail" not in page


def test_an_agent_from_another_tenant_cannot_be_opened_by_id(client, dynamo_resource):
    """Guessing an id must not be a way across the boundary."""
    _agent(dynamo_resource, "t-2", "a-9", "someone-elses-box", timedelta(seconds=5))

    page = client.get("/dashboard/ui/agents?id=a-9").text
    assert "someone-elses-box" not in page
    assert "Agent detail" not in page


def test_the_api_key_hash_never_reaches_the_browser(client, dynamo_resource):
    """`AgentSummary` has a closed field list and that is what keeps the
    hash server-side. A hand-rolled dict(item) response would ship it."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))

    assert "api_key_hash" not in client.get("/dashboard/ui/agents").text
    assert "api_key_hash" not in client.get("/dashboard/v1/agents").text


def test_the_json_route_is_scoped_to_the_token(client, dynamo_resource):
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(seconds=20))
    _agent(dynamo_resource, "t-2", "a-9", "other", timedelta(seconds=20))

    rows = client.get("/dashboard/v1/agents").json()
    assert [r["agent_id"] for r in rows] == ["a-1"]


def test_no_agents_at_all_explains_the_next_step(client):
    page = client.get("/dashboard/ui/agents").text
    assert "No agent has registered" in page


def test_a_revoked_agent_says_why(client, dynamo_resource):
    """Revocation happens when a tenant is suspended. An agent that simply
    reads as "not reporting" would send its owner to restart a process that
    is working fine."""
    _agent(dynamo_resource, "t-1", "a-1", "web-01", timedelta(minutes=1),
           status="revoked")

    page = client.get("/dashboard/ui/agents").text
    assert "Revoked" in page
