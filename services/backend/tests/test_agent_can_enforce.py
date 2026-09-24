"""Reporting is not protecting.

`detect_adapters()` returning an empty list is a legitimate outcome: no nginx,
no iptables, nothing on the machine that can write a rule. The agent then
sends telemetry forever, the backend scores it, decisions are written, and
nothing enforces any of them. Every screen in the product calls that agent
healthy, in green, and the customer has no way to find out.

This is the cleanest example of principle 1.4 in the product: a true sentence
("reporting") standing in for a false one ("protected").
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token
from services.backend.ui.presenters import agent_state

OLD = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def seeded(dynamo_resource):
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    AgentsTable(dynamo_resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=OLD, last_seen_at=OLD, agent_version="1.4.0",
        api_key_hash=hash_api_key("secret"), status="active")
    return dynamo_resource


@pytest.fixture
def client(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _batch(resource, enforcers):
    """One telemetry batch from an agent that says what it can enforce
    with."""
    body = {"logs": [{
        "time_iso8601": "2026-09-24T12:00:00Z", "remote_addr": "203.0.113.5",
        "request_method": "GET", "request_uri": "/", "status": "200",
        "body_bytes_sent": "512", "request_time": "0.05",
        "http_user_agent": "ua-1"}]}
    if enforcers is not None:
        body["enforcers"] = enforcers
    app.dependency_overrides[get_dynamo_resource] = lambda: resource
    client = TestClient(app, base_url="https://testserver")
    return client.post("/agent/v1/telemetry", json=body,
                       headers={"X-Agent-Key": "t-1.secret"})


def _agent(resource):
    return AgentsTable(resource).get(tenant_id="t-1", agent_id="a-1")


# --- what the agent reports ------------------------------------------------


def test_an_agent_that_can_enforce_says_which_backends(seeded):
    _batch(seeded, ["nginx", "iptables"])

    assert sorted(_agent(seeded)["enforcers"]) == ["iptables", "nginx"]


def test_an_agent_that_can_enforce_nothing_says_that_too(seeded):
    """An empty list is a real answer and must be stored as one. Treating it
    as "nothing reported" is how the silent case stays silent."""
    _batch(seeded, [])

    agent = _agent(seeded)

    assert "enforcers" in agent
    assert list(agent["enforcers"]) == []


def test_an_agent_that_has_never_said_stores_nothing(seeded):
    """Agents running the version before this field existed. Writing an
    empty list for them would mark every old agent as unable to enforce on
    upgrade day, which is the louder wrong answer."""
    _batch(seeded, None)

    assert "enforcers" not in _agent(seeded)


def test_the_enforcer_names_are_a_closed_set(seeded):
    """This is written onto an item and rendered on a page. The names come
    from the adapter classes, so it is validated as a closed set rather than
    stored as given."""
    response = _batch(seeded, ["nginx", "; rm -rf /"])

    assert response.status_code == 422
    assert "enforcers" not in (_agent(seeded) or {})


def test_an_unchanged_list_costs_no_write_at_all(seeded, monkeypatch):
    """The list is derived from what is installed on the machine and changes
    about never, while the batch carrying it arrives every few seconds. An
    unconditional write would spend real WCU on a constant, against the 20
    the whole account has."""
    writes = []
    real = AgentsTable.set_enforcers

    def counting(self, tenant_id, agent_id, enforcers):
        writes.append(list(enforcers))
        return real(self, tenant_id, agent_id, enforcers)

    monkeypatch.setattr(AgentsTable, "set_enforcers", counting)
    _batch(seeded, ["nginx"])
    _batch(seeded, ["nginx"])
    _batch(seeded, ["nginx"])

    assert writes == [["nginx"]]


def test_a_changed_list_is_written_once(seeded, monkeypatch):
    """Somebody installed nginx on a machine that had only iptables."""
    _batch(seeded, ["iptables"])
    _batch(seeded, ["iptables", "nginx"])

    assert sorted(_agent(seeded)["enforcers"]) == ["iptables", "nginx"]


# --- what the console makes of it ------------------------------------------


def _row(enforcers=..., seen_ago=30):
    now = datetime.now(timezone.utc)
    row = {"agent_id": "a-1", "agent_label": "web-01", "status": "active",
           "agent_version": "1.4.0",
           "last_seen_at": (now - timedelta(seconds=seen_ago)).isoformat()}
    if enforcers is not ...:
        row["enforcers"] = enforcers
    return row, now


def test_an_agent_with_a_backend_is_reported_as_able_to_enforce():
    row, now = _row(["nginx"])

    assert agent_state(row, now)["can_enforce"] == "yes"


def test_an_agent_with_no_backend_is_reporting_and_not_protecting():
    row, now = _row([])

    state = agent_state(row, now)

    assert state["can_enforce"] == "no"
    assert "nothing" in state["enforce_label"].lower()


def test_an_agent_that_has_not_said_is_neither_confirmed_nor_denied():
    """"We have not been told" is not "it cannot enforce"."""
    row, now = _row()

    assert agent_state(row, now)["can_enforce"] == "unknown"


def test_a_quiet_agent_is_not_described_as_enforcing_anything():
    """It reported nginx an hour ago and has said nothing since. Whether it
    is still enforcing is exactly what "quiet" means we cannot say."""
    row, now = _row(["nginx"], seen_ago=7200)

    state = agent_state(row, now)

    assert state["live"] is False


def test_the_screen_shows_both_states(client, seeded):
    _batch(seeded, [])

    page = client.get("/dashboard/ui/agents").text

    assert "Enforcing" in page or "Can enforce" in page
    assert "nothing" in page.lower()


def test_the_page_summary_does_not_call_a_non_enforcing_fleet_healthy(client, seeded):
    """"1 of 1 reporting" is true and is the wrong headline for a fleet
    protecting nothing."""
    _batch(seeded, [])

    page = client.get("/dashboard/ui/agents").text
    summary = page[page.index("c-page-summary"):page.index("c-page-summary") + 400] \
        if "c-page-summary" in page else page

    assert "enforc" in summary.lower() or "not protecting" in page.lower()


# --- the agent end of the wire ---------------------------------------------


def test_the_agent_sends_what_detect_adapters_found():
    """The whole chain depends on this being on the batch. The CLI already
    prints a warning when nothing is available, which is seen once, on the
    machine, by whoever typed the command."""
    from services.agent.collector import Collector, LogRecord

    sent = {}

    def fake_post(url, payload, headers=None):
        sent.update(payload)
        return {}

    c = Collector("https://api.example.com", "t-1", "secret", batch_size=1,
                  post_json_fn=fake_post, enforcers=["nginx"])
    c.add(LogRecord(time_iso8601="2026-09-24T12:00:00Z",
                    remote_addr="203.0.113.5", request_method="GET",
                    request_uri="/", status="200", body_bytes_sent="1",
                    request_time="0.01", http_user_agent="ua"))

    assert sent["enforcers"] == ["nginx"]


def test_an_agent_with_nothing_available_sends_an_empty_list_not_nothing():
    """The case this whole field exists for. Omitting the key would make a
    machine that can enforce nothing indistinguishable from an agent too old
    to report."""
    from services.agent.collector import Collector, LogRecord

    sent = {}

    def fake_post(url, payload, headers=None):
        sent.update(payload)
        return {}

    c = Collector("https://api.example.com", "t-1", "secret", batch_size=1,
                  post_json_fn=fake_post, enforcers=[])
    c.add(LogRecord(time_iso8601="2026-09-24T12:00:00Z",
                    remote_addr="203.0.113.5", request_method="GET",
                    request_uri="/", status="200", body_bytes_sent="1",
                    request_time="0.01", http_user_agent="ua"))

    assert sent["enforcers"] == []


def test_the_names_the_agent_sends_are_the_names_the_backend_accepts():
    """Two lists in two packages that must not drift. The backend does not
    import the agent, so nothing else keeps them equal."""
    from services.agent.enforcer.iptables_adapter import IptablesAdapter
    from services.agent.enforcer.nginx_adapter import NginxAdapter
    from services.backend.schemas.telemetry import KNOWN_ENFORCERS

    assert {NginxAdapter().name, IptablesAdapter().name} == set(KNOWN_ENFORCERS)
