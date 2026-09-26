"""The off switch.

Until this existed, the only way a customer could stop their own servers
turning visitors away was to revoke every agent key - which also stops the
telemetry that would tell them whether stopping was the right call. At 3am,
with real customers being refused, that is not a control, it is a demolition.

Three properties are load-bearing and each has a test here:

  the customer's machines stop enforcing, which works through the reconcile
  path agents ALREADY run, so it takes effect on installed agents with no new
  version rolled anywhere;

  the platform keeps measuring, so "what did I miss while it was off" has an
  answer made of evidence rather than a gap;

  and every screen says so, because a console that reports five healthy
  agents while nothing is being enforced is lying by omission.
"""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.dependencies import hash_api_key
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token

_RAW = "testkeyhash"
_LOGS = [{
    "time_iso8601": "2026-08-21T00:00:00Z", "remote_addr": "6.6.6.6",
    "request_method": "GET", "request_uri": "/a", "status": "200",
    "body_bytes_sent": "512", "request_time": "0.05",
    "http_user_agent": "ua-1",
}] * 5


class _AlwaysHardBlock:
    def decision_function(self, X):
        return np.full(len(X), -0.5)


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    for tid in ("t-1", "t-2"):
        TenantsTable(dynamo_resource).put(tenant_id=tid, name=tid, status="active",
                                          created_at="2026-08-21T00:00:00Z")
        AgentsTable(dynamo_resource).put(
            tenant_id=tid, agent_id="a-1", agent_label="web-01",
            registered_at="2026-08-21T00:00:00Z",
            last_seen_at="2026-08-21T00:00:00Z", agent_version="1.4.0",
            api_key_hash=hash_api_key(_RAW), status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    ModelManager._cache["t-1"] = (_AlwaysHardBlock(), None)
    ModelManager._cache["t-2"] = (_AlwaysHardBlock(), None)
    yield dynamo_resource
    ModelManager._cache.pop("t-1", None)
    ModelManager._cache.pop("t-2", None)


def _agent(tenant="t-1"):
    return TestClient(app)


def _telemetry(client, tenant="t-1"):
    return client.post("/agent/v1/telemetry", json={"logs": _LOGS},
                       headers={"X-Agent-Key": f"{tenant}.{_RAW}"})


def _console(seeded, keys, tenant="t-1"):
    app.dependency_overrides[get_jwks] = lambda: keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        keys["private_pem"], {"custom:tenant_id": tenant,
                              "email": "owner@acme.example"})})
    return c


def _csrf(c):
    return {"X-CSRF-Token": c.cookies["csrf_token"]}


# ── what the agent is told ──────────────────────────────────────────────

def test_while_paused_the_agent_is_served_nothing_to_enforce(seeded):
    client = _agent()
    assert _telemetry(client).json()["decisions"], "precondition: it blocks"

    TenantsTable(seeded).set_enforcement("t-1", True)
    body = _telemetry(client).json()

    assert body["decisions"] == []
    assert body["active_ips"] == []
    assert body["enforce"] is False


def test_the_empty_active_set_is_PRESENT_and_not_omitted(seeded):
    """This is the whole mechanism, and it is one key.

    `runner._apply` reconciles only when `active_ips` is in the response: an
    absent key means "I cannot tell you" and the agent changes nothing, which
    is correct during a partial rollout and catastrophic here. Present and
    empty is the instruction to release every local rule - which is why
    pausing works on agents that are ALREADY INSTALLED, with no new agent
    version rolled to a single machine.
    """
    TenantsTable(seeded).set_enforcement("t-1", True)

    body = _telemetry(_agent()).json()

    assert "active_ips" in body
    assert body["active_ips"] == []


def test_the_second_endpoint_honours_the_pause_too(seeded):
    """A blocked source stops sending traffic, so a quiet agent polls here
    instead. An endpoint that kept serving decisions after the other one
    stopped would put a paused tenant straight back into enforcement."""
    client = _agent()
    _telemetry(client)
    assert client.get("/agent/v1/decisions",
                      headers={"X-Agent-Key": f"t-1.{_RAW}"}).json()

    TenantsTable(seeded).set_enforcement("t-1", True)

    assert client.get("/agent/v1/decisions",
                      headers={"X-Agent-Key": f"t-1.{_RAW}"}).json() == []


def test_resuming_puts_it_all_back(seeded):
    client = _agent()
    _telemetry(client)
    TenantsTable(seeded).set_enforcement("t-1", True)
    assert _telemetry(client).json()["active_ips"] == []

    TenantsTable(seeded).set_enforcement("t-1", False)
    body = _telemetry(client).json()

    assert body["enforce"] is True
    assert "6.6.6.6" in body["active_ips"]


def test_one_tenant_pausing_does_not_disarm_another(seeded):
    """The flag lives on the tenant item, so this is the partition key doing
    its job - and it is worth an assertion, because the failure would be a
    customer silently unprotected."""
    client = _agent()
    TenantsTable(seeded).set_enforcement("t-1", True)

    assert _telemetry(client, "t-1").json()["enforce"] is False
    assert _telemetry(client, "t-2").json()["enforce"] is True
    assert _telemetry(client, "t-2").json()["decisions"]


# ── what the platform keeps doing ───────────────────────────────────────

def test_paused_is_not_blind(seeded):
    """The decision is still taken and still stored. Otherwise the answer to
    "what did I miss while it was off" is a gap, and a customer deciding
    whether to turn it back on has nothing to decide with."""
    TenantsTable(seeded).set_enforcement("t-1", True)

    _telemetry(_agent())

    held = MitigationStateTable(seeded).query_active("t-1")
    assert [i["ip"] for i in held] == ["6.6.6.6"]


def test_paused_still_writes_history(seeded):
    TenantsTable(seeded).set_enforcement("t-1", True)
    now = int(time.time())

    _telemetry(_agent())

    rows = TenantHistoryTable(seeded).query_series(
        "t-1", now - 7200, now + 7200, fill=False)
    assert any(int(r.get("requests", 0)) for r in rows)


# ── what the console says ───────────────────────────────────────────────

def test_every_page_says_enforcement_is_off(seeded, cognito_test_keys):
    """Not one page. A customer reaches for this while their own customers
    are being turned away, and whichever screen they are on is the screen it
    has to be on."""
    _telemetry(_agent())
    TenantsTable(seeded).set_enforcement("t-1", True)
    c = _console(seeded, cognito_test_keys)

    for path in ("/dashboard/ui", "/dashboard/ui/agents", "/dashboard/ui/allowed",
                 "/dashboard/ui/model", "/dashboard/ui/history",
                 "/dashboard/ui/system"):
        assert "Thi hành đang tạm dừng" in c.get(path).text, path


def test_a_held_row_stops_claiming_your_servers_have_it(seeded, cognito_test_keys):
    """Principle 1.4. "3 of 3 written" is a true sentence about collection
    times and a false one about the world, the moment nothing is enforced."""
    _telemetry(_agent())
    c = _console(seeded, cognito_test_keys)
    # "Chỉ theo dõi" is the label on the top bar button too, so the assertion
    # is on the sentence only a held row can carry.
    assert "đáng lẽ đang có hiệu lực" not in c.get("/dashboard/ui").text

    TenantsTable(seeded).set_enforcement("t-1", True)

    page = c.get("/dashboard/ui").text
    assert "đáng lẽ đang có hiệu lực" in page
    assert "3 agent đã nhận" not in page


def test_the_exported_evidence_carries_the_same_caveat(seeded, cognito_test_keys):
    """A CSV is what ends up in someone else's ticket, and it outlives the
    screen it came from."""
    _telemetry(_agent())
    TenantsTable(seeded).set_enforcement("t-1", True)
    c = _console(seeded, cognito_test_keys)

    body = c.get("/dashboard/ui/mitigations.csv").text

    assert "không áp gì" in body


def test_the_control_is_reachable_from_the_top_bar(seeded, cognito_test_keys):
    c = _console(seeded, cognito_test_keys)
    page = c.get("/dashboard/ui").text
    topbar = page[page.index('class="bar"'):page.index("</header>")]

    assert "/dashboard/ui/enforcement?on=0" in topbar


# ── turning protection off is an audited act ────────────────────────────

def test_switching_it_off_is_written_to_the_ledger(seeded, cognito_test_keys):
    """"Who switched it off, and when" is the first question the review after
    an incident asks."""
    c = _console(seeded, cognito_test_keys)

    r = c.post("/dashboard/ui/enforcement?on=0", headers=_csrf(c))
    assert r.status_code == 200

    now = int(time.time())
    rows = TenantHistoryTable(seeded).query_settings("t-1", now - 60, now + 60)
    assert [x for x in rows if x.get("what") == "enforcement"]
    assert any(x.get("actor") == "owner@acme.example" for x in rows)


def test_it_actually_flips_the_tenant(seeded, cognito_test_keys):
    c = _console(seeded, cognito_test_keys)

    c.post("/dashboard/ui/enforcement?on=0", headers=_csrf(c))
    assert TenantsTable(seeded).get(tenant_id="t-1").get("enforce_paused_at")

    c.post("/dashboard/ui/enforcement?on=1", headers=_csrf(c))
    assert not TenantsTable(seeded).get(tenant_id="t-1").get("enforce_paused_at")


def test_it_cannot_be_flipped_without_the_csrf_token(seeded, cognito_test_keys):
    """The one control in this console that an attacker most wants to reach
    from another origin."""
    c = _console(seeded, cognito_test_keys)
    c.cookies.pop("csrf_token", None)

    r = c.post("/dashboard/ui/enforcement?on=0", headers={"X-CSRF-Token": "wrong"})

    assert r.status_code in (400, 403)
    assert not (TenantsTable(seeded).get(tenant_id="t-1") or {}).get("enforce_paused_at")
