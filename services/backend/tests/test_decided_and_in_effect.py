"""Two states, permanently.

The agent is asynchronous by architecture. The backend writes a decision and
the customer's nginx does not change until the agent next collects, so
"blocked" is two facts and a screen showing one of them is wrong about the
other half of the time it matters. Principle 1.5 says this has to be built in
rather than patched on, and this is the file that holds it.

There is no acknowledgement channel and this must not invent one. What there
is: `agent_auth` touches `last_seen_at` on every authenticated agent request,
including the decisions poll. "Has this agent collected anything since the
decision was made" is therefore a real measurement, and it is the strongest
claim the stored data supports.
"""
from datetime import datetime, timedelta, timezone

from services.backend.tests.conftest import sign_test_token
from services.backend.ui.presenters import reach


def _agent(seen_ago_seconds, now):
    return {"agent_id": "a-1", "agent_label": "web-01",
            "last_seen_at": (now - timedelta(seconds=seen_ago_seconds)).isoformat()}


def test_an_agent_that_has_polled_since_the_decision_has_it():
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(minutes=5)).timestamp())

    assert reach(decided, [_agent(30, now)], now)["in_effect"] is True


def test_an_agent_that_has_not_polled_since_the_decision_does_not_have_it_yet():
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(seconds=10)).timestamp())

    assert reach(decided, [_agent(600, now)], now)["in_effect"] is False


def test_one_stale_agent_out_of_three_means_not_everywhere():
    """A fleet is protected at the pace of its slowest member. Reporting
    "in effect" because two of three have it would be the more comfortable
    claim and the false one."""
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(minutes=5)).timestamp())
    agents = [_agent(30, now), _agent(30, now), _agent(3600, now)]

    state = reach(decided, agents, now)

    assert state["in_effect"] is False
    assert "1 trong 3" in state["detail"]


def test_no_agents_at_all_is_not_in_effect_and_says_why():
    now = datetime.now(timezone.utc)

    state = reach(int(now.timestamp()), [], now)

    assert state["in_effect"] is False
    assert "chưa agent nào" in state["detail"].lower()


def test_an_agent_with_an_unreadable_timestamp_counts_as_not_reached():
    """A parse failure must fail closed. Counting it as reached would report
    a customer protected on the strength of a field nobody could read."""
    now = datetime.now(timezone.utc)

    state = reach(int(now.timestamp()) - 60,
                  [{"agent_id": "a-1", "last_seen_at": "not a date"}], now)

    assert state["in_effect"] is False


def test_a_missing_timestamp_counts_as_not_reached():
    now = datetime.now(timezone.utc)

    assert reach(int(now.timestamp()) - 60, [{"agent_id": "a-1"}], now)["in_effect"] is False


def test_a_naive_timestamp_is_read_as_utc_rather_than_local():
    """Every timestamp this product writes is UTC. Reading a naive one as
    local time would shift it by hours and report a decision collected that
    was not, or the reverse, depending on where the reader happens to be."""
    now = datetime.now(timezone.utc)
    decided = int((now - timedelta(minutes=5)).timestamp())
    naive = {"agent_id": "a-1",
             "last_seen_at": (now - timedelta(seconds=30))
                             .replace(tzinfo=None).isoformat()}

    assert reach(decided, [naive], now)["in_effect"] is True


def test_a_decision_with_no_recorded_time_claims_nothing_either_way():
    """Rows written before decided_at existed. "We cannot tell" and "not in
    effect" are different statements, and asserting the second would put a
    warning on every decision this product took before the field shipped."""
    now = datetime.now(timezone.utc)

    state = reach(0, [_agent(30, now)], now)

    assert state["in_effect"] is False
    assert state["label"] == "Không rõ"


def test_the_label_never_claims_more_than_the_data_supports():
    """It says the agent collected, not that nginx applied it. There is no
    acknowledgement channel and a label implying one would be a promise the
    product cannot keep."""
    now = datetime.now(timezone.utc)

    state = reach(int((now - timedelta(minutes=5)).timestamp()),
                  [_agent(30, now)], now)

    assert "confirmed" not in state["label"].lower()
    assert "applied" not in state["detail"].lower()


def test_the_decisions_poll_is_what_keeps_this_honest(dynamo_resource):
    """The whole claim rests on last_seen_at moving when an agent collects
    decisions, not only when it sends telemetry. If that ever stops being
    true, every "in effect" on the product becomes false and silent."""
    import time

    from services.backend.api.dependencies import hash_api_key
    from services.backend.core.tables import (
        AgentsTable, TenantsTable, create_all_tables,
    )
    from services.backend.main import app

    from fastapi.testclient import TestClient

    from services.backend.core.dynamo import get_dynamo_resource

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    stale = "2020-01-01T00:00:00+00:00"
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=stale, last_seen_at=stale, agent_version="1.4.0",
        api_key_hash=hash_api_key("secret"), status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    client = TestClient(app, base_url="https://testserver")

    before = int(time.time())
    client.get("/agent/v1/decisions", headers={"X-Agent-Key": "t-1.secret"})

    agent = AgentsTable(dynamo_resource).get(tenant_id="t-1", agent_id="a-1")
    seen = datetime.fromisoformat(agent["last_seen_at"])

    assert int(seen.timestamp()) >= before


# --- on the screen ---------------------------------------------------------


def _console(dynamo_resource, cognito_test_keys, *, agent_seen_ago=30,
             decided_ago=300):
    """A tenant with one blocked source and one agent, positioned in time."""
    import time

    from fastapi.testclient import TestClient

    from services.backend.api.cognito_auth import get_jwks
    from services.backend.core.dynamo import get_dynamo_resource
    from services.backend.core.tables import (
        AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
    )
    from services.backend.main import app

    create_all_tables(dynamo_resource)
    now = datetime.now(timezone.utc)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=now.isoformat(),
        last_seen_at=(now - timedelta(seconds=agent_seen_ago)).isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active")
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=int(time.time()) + 3600,
        decided_at=int(now.timestamp()) - decided_ago)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return client


def test_the_list_says_whether_each_decision_has_reached_the_servers(
        dynamo_resource, cognito_test_keys):
    client = _console(dynamo_resource, cognito_test_keys)

    page = client.get("/dashboard/ui").text
    assert ("Chưa đủ mọi máy" in page or "Chưa agent nào nhận" in page
            or "agent đã nhận" in page)


def test_a_decision_the_agent_has_not_collected_is_marked_as_such(
        dynamo_resource, cognito_test_keys):
    """The dangerous case. The product has written a block, the customer's
    nginx has not changed, and a screen saying only "Đang chặn" is telling
    them they are protected when they are not yet."""
    client = _console(dynamo_resource, cognito_test_keys,
                      agent_seen_ago=600, decided_ago=10)

    page = client.get("/dashboard/ui").text

    assert ("Chưa đủ mọi máy" in page or "Chưa agent nào nhận" in page)
    assert "agent đã nhận" not in page


def test_a_collected_decision_reads_differently_from_an_uncollected_one(
        dynamo_resource, cognito_test_keys):
    client = _console(dynamo_resource, cognito_test_keys,
                      agent_seen_ago=30, decided_ago=300)

    page = client.get("/dashboard/ui").text

    assert "agent đã nhận" in page
    assert "Đã có hiệu lực" in page


def test_the_detail_pane_states_both_facts(dynamo_resource, cognito_test_keys):
    client = _console(dynamo_resource, cognito_test_keys)

    page = client.get("/dashboard/ui?ip=10.0.0.7").text

    assert "Đang chặn" in page
    assert "1 agent đã nhận" in page
