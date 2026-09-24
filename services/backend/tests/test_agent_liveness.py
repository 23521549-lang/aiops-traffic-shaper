"""The Control Platform's Agents card could never show anything.

Three facts, each true in the shipped code, which together make the card
that answers "which agents are alive?" structurally incapable of being
wrong, because it never changed:

  1. `last_seen_at` was written exactly once, at registration
     (api/routes/agent.py), and never again.
  2. No code path anywhere set `status` to "stale". An agent is "active"
     from registration, or "revoked" when its tenant is suspended.
  3. The UI opened on `status="stale"` (ui/control_platform.py), querying
     the LastSeenIndex GSI whose partition key IS status.

So the default view was always empty, and the "Active" filter listed every
agent ever registered whether it had spoken in the last second or never
spoken again.

The fix keeps `status` as the lifecycle state — agent_auth checks it, and
a revoked key must die immediately — and derives LIVENESS from
`last_seen_at` instead. That is what LastSeenIndex was built for: its sort
key is `last_seen_at`, so a range condition on it answers "which active
agents have gone quiet" in one query, cross-tenant, no scan. The index was
right all along; nothing ever queried it by time.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _agent(resource, tenant_id, agent_id, last_seen, status="active", label="nginx-01"):
    AgentsTable(resource).put(
        tenant_id=tenant_id, agent_id=agent_id, agent_label=label,
        registered_at=_iso(NOW - timedelta(days=1)), last_seen_at=_iso(last_seen),
        agent_version="unknown", api_key_hash="h", status=status,
    )


@pytest.fixture
def agents(dynamo_resource):
    create_all_tables(dynamo_resource)
    _agent(dynamo_resource, "t-1", "a-live", NOW - timedelta(seconds=30))
    _agent(dynamo_resource, "t-1", "a-quiet", NOW - timedelta(hours=3))
    _agent(dynamo_resource, "t-2", "a-dead", NOW - timedelta(days=2))
    _agent(dynamo_resource, "t-2", "a-gone", NOW - timedelta(minutes=1), status="revoked")
    return dynamo_resource


# --- liveness is derived from last_seen_at, not stored -------------------

def test_quiet_agents_are_the_ones_that_have_not_called_in(agents):
    stale = AgentsTable(agents).query_stale(now=NOW)
    assert {a["agent_id"] for a in stale} == {"a-quiet", "a-dead"}


def test_live_agents_are_the_ones_that_have(agents):
    live = AgentsTable(agents).query_live(now=NOW)
    assert {a["agent_id"] for a in live} == {"a-live"}


def test_a_revoked_agent_is_neither_live_nor_stale(agents):
    """It called in a minute ago, so by time alone it would look healthy.
    Revocation is a lifecycle fact and outranks liveness: its key is dead."""
    ids = {a["agent_id"] for a in AgentsTable(agents).query_live(now=NOW)}
    ids |= {a["agent_id"] for a in AgentsTable(agents).query_stale(now=NOW)}
    assert "a-gone" not in ids
    assert {a["agent_id"] for a in AgentsTable(agents).query_revoked()} == {"a-gone"}


def test_every_active_agent_is_either_live_or_stale_and_never_both(agents):
    live = {a["agent_id"] for a in AgentsTable(agents).query_live(now=NOW)}
    stale = {a["agent_id"] for a in AgentsTable(agents).query_stale(now=NOW)}
    assert live & stale == set()
    assert live | stale == {"a-live", "a-quiet", "a-dead"}


# --- last_seen_at has to actually advance --------------------------------

def test_an_authenticated_call_advances_last_seen(agents):
    table = AgentsTable(agents)
    assert table.touch("t-1", "a-quiet", now=NOW) is True
    assert table.get(tenant_id="t-1", agent_id="a-quiet")["last_seen_at"] == _iso(NOW)
    # and it moves from stale to live on the very next query
    assert {a["agent_id"] for a in table.query_live(now=NOW)} == {"a-live", "a-quiet"}


def test_last_seen_is_not_rewritten_on_every_single_call(agents):
    """`last_seen_at` is the LastSeenIndex sort key, so each update is a GSI
    delete+insert — 2 WCU against a 25 WCU account-wide pool shared by every
    table (core/tables.py). A busy agent posting telemetry every few seconds
    would spend the free tier on a timestamp nobody reads that often. The
    write is therefore conditional on the record being stale enough to
    matter, and 'no' is the normal answer."""
    table = AgentsTable(agents)
    assert table.touch("t-1", "a-live", now=NOW) is False
    assert table.get(tenant_id="t-1", agent_id="a-live")["last_seen_at"] == _iso(
        NOW - timedelta(seconds=30))


def test_touching_an_agent_that_is_gone_does_not_recreate_it(agents):
    """A conditional update with no condition on existence will happily
    create the item. An agent deleted mid-request must not be resurrected
    as a half-formed record with nothing but a timestamp."""
    table = AgentsTable(agents)
    assert table.touch("t-1", "a-never-existed", now=NOW) is False
    assert table.get(tenant_id="t-1", agent_id="a-never-existed") is None


def test_authenticating_an_agent_touches_it(dynamo_resource):
    """The end the defect is actually fixed at. `authenticated_agent` already
    reads the full agent item to check the key hash, so it holds agent_id in
    hand and the refresh costs no extra read.

    Called here rather than `agent_auth`, which is now a thin wrapper that
    takes the item from this and returns only the tenant id."""
    from services.backend.api.dependencies import (
        authenticated_agent, hash_api_key,
    )

    # It has no `now` seam — it is called by FastAPI — so this one
    # test works against the real clock rather than the fixed NOW the table
    # tests use. A record stamped in the future would make touch() correctly
    # refuse, which is a trap worth not walking into twice.
    real_now = datetime.now(timezone.utc)
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at=_iso(real_now))
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="nginx-01",
        registered_at=_iso(real_now - timedelta(days=1)),
        last_seen_at=_iso(real_now - timedelta(hours=5)),
        agent_version="unknown", api_key_hash=hash_api_key("rawkey"), status="active",
    )

    before = AgentsTable(dynamo_resource).get(tenant_id="t-1", agent_id="a-1")["last_seen_at"]
    agent = authenticated_agent(x_agent_key="t-1.rawkey", resource=dynamo_resource)
    assert agent["tenant_id"] == "t-1"
    assert agent["agent_id"] == "a-1"
    after = AgentsTable(dynamo_resource).get(tenant_id="t-1", agent_id="a-1")["last_seen_at"]
    assert after > before


# --- what the operator is shown ------------------------------------------

def test_the_control_platform_opens_on_agents_that_are_alive(agents, cognito_test_keys):
    """The old default was `stale`, which could never match anything, so the
    landing view was a permanent empty list. Opening on what is working is
    also the right default: the admin's question is 'is my fleet healthy',
    and an empty Active list is the alarming answer, not the boring one."""
    from services.backend.api.routes.admin import list_agents

    summaries = list_agents(status="active", resource=agents, now=NOW)
    assert {s.agent_id for s in summaries} == {"a-live"}


def test_an_agent_is_listed_under_the_name_its_owner_gave_it(agents):
    """agent_label is captured at registration and was dropped from
    AgentSummary, so the console showed a bare uuid for a machine the
    customer had already named."""
    from services.backend.api.routes.admin import list_agents

    summaries = list_agents(status="active", resource=agents, now=NOW)
    assert summaries[0].agent_label == "nginx-01"
