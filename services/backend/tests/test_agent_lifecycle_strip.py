"""What your agent is doing, drawn.

The BA report's job number one, and the thing no screen has ever answered. A
customer installs an agent on their own server and then has no way to see what
it does there. The Agents screen says an agent is REPORTING. It does not say
what becomes of a decision after that, which tool writes the rule, or whether
the other machines in the fleet ever got it.

All of it is already known to the product on this page. None of it was drawn.

The strip is four steps because the loop has four: read the log, measure the
source, cross a gate, write the rule. Step four is the one that matters, and
it is principle 1.5 drawn instead of written - the backend deciding and the
customer's nginx changing are two different events, and until now only a chip
in a table said so.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def seeded(dynamo_resource):
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


@pytest.fixture
def client(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _running(resource, *, enforcers=("nginx",), stale_second=False):
    """A tenant whose machinery is actually turning: two agents, a model,
    traffic in the window and one source being held."""
    import numpy as np

    from services.backend.ml.training import train_and_save

    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    AgentsTable(resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=now.isoformat(), last_seen_at=now.isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active",
        enforcers=list(enforcers))
    seen = now - timedelta(hours=3) if stale_second else now
    AgentsTable(resource).put(
        tenant_id="t-1", agent_id="a-2", agent_label="web-02",
        registered_at=now.isoformat(), last_seen_at=seen.isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active",
        enforcers=list(enforcers))

    rng = np.random.default_rng(83)
    train_and_save(resource, "t-1",
                   [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0, .06)),
                     float(rng.uniform(600, 6000)), float(rng.uniform(.02, .35)),
                     float(rng.uniform(.4, .8)), float(rng.uniform(.6, 1.4)),
                     float(rng.uniform(.02, .14))] for _ in range(200)],
                   stage="production")
    TenantHistoryTable(resource).record_traffic(
        "t-1", TenantHistoryTable.hour_of(ts), requests=184_220,
        bins={"n300": 120, "n375": 31})
    MitigationStateTable(resource).put(
        tenant_id="t-1", ip="203.0.113.44", tier=2, score=-.4, z=-6.41,
        reason="behavioral_anomaly", expires_at=ts + 3600, decided_at=ts - 300)
    return resource


def _strip(page):
    at = page.index("c-lifecycle")
    return page[at:page.index("</div>", page.index("</div>", at) + 1) + 6] \
        if "c-lifecycle" in page else ""


def test_the_strip_shows_all_four_steps(client, seeded):
    _running(seeded)

    page = client.get("/dashboard/ui").text

    assert "c-lifecycle" in page
    assert page.count("c-lifecycle-step") == 4


def test_the_first_step_counts_what_was_actually_read(client, seeded):
    _running(seeded)

    page = client.get("/dashboard/ui").text

    assert "184,220" in page


def test_the_last_step_says_how_many_agents_have_the_rule(client, seeded):
    """Principle 1.5, drawn. One agent has collected since the decision and
    one has been quiet for three hours, so the fleet is not holding it."""
    _running(seeded, stale_second=True)

    page = client.get("/dashboard/ui").text
    at = page.index("c-lifecycle")

    assert "of 2" in page[at:at + 2200] or "1/2" in page[at:at + 2200]


def test_the_strip_names_the_tool_that_writes_the_rule(client, seeded):
    """"Enforced" is an abstraction. The customer has to know which file on
    their own machine changed, because that is where they will look."""
    _running(seeded, enforcers=("nginx",))

    assert "nginx" in client.get("/dashboard/ui").text


def test_an_agent_that_can_enforce_nothing_is_named_in_the_strip(client, seeded):
    """The silent failure this product had: reporting, green everywhere, and
    applying nothing."""
    _running(seeded, enforcers=())

    page = client.get("/dashboard/ui").text
    at = page.index("c-lifecycle")

    assert "nothing" in page[at:at + 2200].lower()


def test_a_tenant_with_no_agent_gets_no_pipeline_to_look_at(client, seeded):
    """Nothing is flowing. Four zeroes would imply a system that is running
    and idle, which is the opposite of the truth."""
    assert "c-lifecycle" not in client.get("/dashboard/ui").text


def test_the_strip_costs_no_extra_query(client, seeded, monkeypatch):
    """Every figure it shows is already on this page for another reason. A
    strip that cost a query would be a vanity panel on a 14 RCU budget."""
    from services.backend.core.tables import AgentsTable as AT

    calls = []
    real = AT.query_by_tenant

    def counting(self, tenant_id):
        calls.append(tenant_id)
        return real(self, tenant_id)

    monkeypatch.setattr(AT, "query_by_tenant", counting)
    _running(seeded)
    client.get("/dashboard/ui")

    assert len(calls) == 1


def test_the_motion_costs_no_request(client, seeded):
    """The pulse is CSS. A console that polls turns off protection for every
    tenant on the platform, which is the whole reason spec 12.3 exists: one
    tab at five seconds is 52% of the account's daily ceiling."""
    _running(seeded)

    page = client.get("/dashboard/ui").text

    assert "hx-trigger" not in page
    assert "setInterval" not in page


def test_the_strip_is_readable_without_colour(client, seeded):
    """Four steps that differ only by hue are four steps a colour-blind
    operator cannot order."""
    _running(seeded)

    page = client.get("/dashboard/ui").text
    at = page.index("c-lifecycle")
    block = page[at:at + 2400]

    for word in ("Read", "Near your lines", "Past a gate", "Written"):
        assert word in block, word


def test_an_undated_decision_is_not_reported_as_a_fleet_failure(client, seeded):
    """The defect this strip shipped with for about ten minutes.

    Decisions written before `decided_at` existed cannot be asked whether the
    fleet has collected them. Counting them as NOT collected renders "4 has
    not collected yet" at a fleet that is working perfectly - the same
    merging of "we cannot tell" with "no" that reach() itself is careful
    never to do, reintroduced one layer up.
    """
    import numpy as np

    from services.backend.ml.training import train_and_save

    now = datetime.now(timezone.utc)
    AgentsTable(seeded).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=now.isoformat(), last_seen_at=now.isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active",
        enforcers=["nginx"])
    rng = np.random.default_rng(91)
    train_and_save(seeded, "t-1",
                   [[float(rng.uniform(.8, 1.6)), float(rng.uniform(0, .06)),
                     float(rng.uniform(600, 6000)), float(rng.uniform(.02, .35)),
                     float(rng.uniform(.4, .8)), float(rng.uniform(.6, 1.4)),
                     float(rng.uniform(.02, .14))] for _ in range(200)],
                   stage="production")
    # No decided_at: exactly what every row written before Phase 1d looks like.
    MitigationStateTable(seeded).put(
        tenant_id="t-1", ip="203.0.113.44", tier=2, score=-.4, z=-6.41,
        reason="behavioral_anomaly", expires_at=int(now.timestamp()) + 3600)

    page = client.get("/dashboard/ui").text
    at = page.index("c-lifecycle")
    block = page[at:at + 2400]

    assert "has not collected" not in block
    assert "not recorded" in block


def test_a_dated_decision_the_fleet_is_holding_says_which_tool(client, seeded):
    """The other end of the same branch, so the three states are all pinned
    rather than only the one that was wrong."""
    _running(seeded, enforcers=("nginx", "iptables"))

    page = client.get("/dashboard/ui").text
    at = page.index("c-lifecycle")
    block = page[at:at + 2400]

    assert "iptables" in block
    assert "not recorded" not in block
