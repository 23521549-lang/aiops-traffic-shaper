"""The drawing has to be read off the machinery, not drawn beside it.

A diagram is trusted more than a sentence, so a diagram that has drifted from
the system is worse than no diagram at all. Every interval, every file path
and every threshold on this screen comes from the constant the running code
uses, and this file asserts that by reading the same constants.

It is also the one screen that answers the three questions a buyer asks and
no console in this product has ever answered: where does the agent run, what
does it write the rule WITH, and do my machines cover for each other.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.api.routes.agent import _TTL_SECONDS
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import AnomalyTier, ModelManager
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


def _fleet(resource, n=3, enforcers=("nginx",), behind=0):
    now = datetime.now(timezone.utc)
    for i in range(n):
        stale = i < behind
        AgentsTable(resource).put(
            tenant_id="t-1", agent_id=f"a-{i}", agent_label=f"web-0{i}",
            registered_at=now.isoformat(),
            last_seen_at=(now - timedelta(hours=4) if stale else now).isoformat(),
            agent_version="1.4.0", api_key_hash="h", status="active",
            enforcers=list(enforcers))
    MitigationStateTable(resource).put(
        tenant_id="t-1", ip="203.0.113.44", tier=2, score=-.4, z=-6.41,
        reason="behavioral_anomaly", expires_at=int(now.timestamp()) + 3600,
        decided_at=int(now.timestamp()) - 600)


def test_the_screen_exists_and_is_in_the_navigation(client, seeded):
    _fleet(seeded)

    page = client.get("/dashboard/ui/system").text

    assert "sys-plot" in page
    assert "/dashboard/ui/system" in page       # its own nav entry


def test_it_names_the_tools_the_agent_writes_rules_with(client, seeded):
    """"Enforced" is an abstraction. A buyer wants to know which file on
    their own machine changes, because that is where they will look."""
    _fleet(seeded, enforcers=("nginx", "iptables"))

    page = client.get("/dashboard/ui/system").text

    assert "iptables" in page
    assert "aiops-agent-deny.conf" in page
    assert "nginx -s reload" in page


def test_the_thresholds_are_this_tenants_own(client, seeded):
    """Drawing the shipped defaults at a customer who has moved their gate
    would make the picture wrong for the one reader it matters to."""
    _fleet(seeded)
    TenantsTable(seeded).set_threshold("t-1", "tier1_z", -4.5)

    assert "4.50" in client.get("/dashboard/ui/system").text


def test_the_holding_times_come_from_the_constants_the_code_enforces(client, seeded):
    """Not typed into the template. If the TTLs change, the drawing changes
    with them instead of quietly becoming a lie."""
    _fleet(seeded)

    page = client.get("/dashboard/ui/system").text
    slow_minutes = _TTL_SECONDS[AnomalyTier.RATE_LIMIT] // 60
    block_hours = _TTL_SECONDS[AnomalyTier.HARD_BLOCK] // 3600

    assert f"{slow_minutes} phút" in page
    assert f"{block_hours} giờ" in page


def test_it_shows_the_fleet_covering_for_each_other(client, seeded):
    """The thing a rule engine cannot do, and the reason `active_ips` carries
    the tenant's whole set rather than only what this batch touched."""
    _fleet(seeded, n=3)

    page = client.get("/dashboard/ui/system").text

    assert "cả 3 máy của bạn" in page


def test_an_agent_that_has_not_caught_up_is_shown_as_such(client, seeded):
    _fleet(seeded, n=3, behind=1)

    assert "1 trên 3 máy chưa bắt kịp" in client.get("/dashboard/ui/system").text


def test_it_states_the_cost_and_why_it_stays_zero(client, seeded):
    """The product's supreme constraint, and the reason this console never
    refreshes itself. Saying the number without the mechanism invites a
    reader to assume it is an introductory price."""
    _fleet(seeded)

    page = client.get("/dashboard/ui/system").text

    assert "0 đồng" in page
    assert "TỪ CHỐI việc" in page


def test_a_tenant_with_no_agent_is_not_shown_a_loop_that_is_not_running(client, seeded):
    """Four boxes and a pulse would say the machinery is turning. Nothing is
    installed."""
    page = client.get("/dashboard/ui/system").text

    assert "sys-plot" not in page
    assert "Chưa có agent nào được cài" in page


def test_the_drawing_asks_the_server_for_nothing(client, seeded):
    """The pulse is an SVG animation on a five second cycle. A console that
    polled at that rate would take 52% of the account's daily ceiling, and at
    the ceiling the platform refuses telemetry for every tenant - so the
    dashboard would be switching off the protection it reports on."""
    _fleet(seeded)

    page = client.get("/dashboard/ui/system").text

    assert "hx-trigger" not in page
    assert "setInterval" not in page
    assert "EventSource" not in page


def test_the_drawing_uses_no_literal_colour(client, seeded):
    """An SVG is where a second palette takes root, because no stylesheet
    test looks inside one."""
    import re

    _fleet(seeded)
    page = client.get("/dashboard/ui/system").text
    plot = page[page.index("sys-plot"):page.index("</svg>", page.index("sys-plot"))]

    assert not re.search(r'(fill|stroke|stop-color)="#', plot)


def test_the_drawing_carries_a_description_for_a_screen_reader(client, seeded):
    """A picture is exactly what a screen reader cannot read, and this one
    carries the whole argument of the product."""
    _fleet(seeded)

    page = client.get("/dashboard/ui/system").text

    assert "<title id=\"sys-t\">" in page
    assert "<desc id=\"sys-d\">" in page
    assert "aria-labelledby=\"sys-t sys-d\"" in page
