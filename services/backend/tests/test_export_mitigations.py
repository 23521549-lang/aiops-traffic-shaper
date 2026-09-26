"""Taking the evidence out of the product.

A decision this platform makes ends up in somebody else's ticket, somebody
else's spreadsheet, or somebody else's compliance pack. Until this route the
only way out of the console was a screenshot, and a screenshot loses the one
column a dispute turns on: whether the customer's own servers had actually
applied the rule yet. It is the column nobody thinks to scroll to.

The file is the same Query the page already runs, rendered differently. No
new index, no new access pattern and no wider window, which is what keeps it
inside the cost rule rather than beside it.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token
from services.backend.ui.dashboard import _csv_cell


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    for tid, name in (("t-1", "Acme"), ("t-2", "Globex")):
        TenantsTable(dynamo_resource).put(tenant_id=tid, name=name,
                                          status="active",
                                          created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


def _client(seeded, keys, tenant="t-1"):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        keys["private_pem"], {"custom:tenant_id": tenant})})
    return c


def _hold(resource, tenant_id, ip, tier=2, z=-6.41, ago=600):
    now = datetime.now(timezone.utc)
    MitigationStateTable(resource).put(
        tenant_id=tenant_id, ip=ip, tier=tier, score=-.4, z=z,
        reason="behavioral_anomaly",
        expires_at=int(now.timestamp()) + 3600,
        decided_at=int(now.timestamp()) - ago)


def _agent(resource, tenant_id, seen_hours_ago=0):
    now = datetime.now(timezone.utc)
    AgentsTable(resource).put(
        tenant_id=tenant_id, agent_id="a-1", agent_label="web-01",
        registered_at=now.isoformat(),
        last_seen_at=(now - timedelta(hours=seen_hours_ago)).isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active",
        enforcers=["nginx"])


def test_it_exports_what_is_being_held(seeded, cognito_test_keys):
    _agent(seeded, "t-1")
    _hold(seeded, "t-1", "203.0.113.44")

    body = _client(seeded, cognito_test_keys).get(
        "/dashboard/ui/mitigations.csv").text

    assert "203.0.113.44" in body
    assert "6.41" in body


def test_it_carries_the_column_a_screenshot_loses(seeded, cognito_test_keys):
    """Decided and in effect are two states, permanently. A customer arguing
    that a block never reached their servers is arguing about this column,
    and it is the reason the file exists at all."""
    _agent(seeded, "t-1", seen_hours_ago=4)
    _hold(seeded, "t-1", "203.0.113.44")

    body = _client(seeded, cognito_test_keys).get(
        "/dashboard/ui/mitigations.csv").text

    assert "at_your_servers" in body.splitlines()[0]
    # The stale agent has not collected since before the decision.
    assert "chưa" in body.splitlines()[1].lower()


def test_one_tenant_never_sees_another_tenants_sources(seeded, cognito_test_keys):
    """The single-table layout puts both tenants' mitigations in one table,
    so isolation here is the partition key doing its job and nothing else.
    Asserted on every read path that exists, including this one."""
    _agent(seeded, "t-1")
    _hold(seeded, "t-1", "203.0.113.44")
    _hold(seeded, "t-2", "198.51.100.7")

    body = _client(seeded, cognito_test_keys).get(
        "/dashboard/ui/mitigations.csv").text

    assert "203.0.113.44" in body
    assert "198.51.100.7" not in body


def test_it_downloads_rather_than_renders_and_is_never_cached(seeded, cognito_test_keys):
    """The active set is a moment in time, so a cached copy is a wrong answer
    rather than a stale one."""
    _agent(seeded, "t-1")
    _hold(seeded, "t-1", "203.0.113.44")

    r = _client(seeded, cognito_test_keys).get("/dashboard/ui/mitigations.csv")

    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"


def test_signing_out_is_enough_to_lose_the_file(seeded, cognito_test_keys):
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    anonymous = TestClient(app, base_url="https://testserver")

    r = anonymous.get("/dashboard/ui/mitigations.csv", follow_redirects=False)

    assert r.status_code in (302, 303, 307, 401, 403)


@pytest.mark.parametrize("raw", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)"])
def test_a_cell_that_would_run_as_a_formula_is_defused(raw):
    """Excel and Sheets execute a cell that opens with one of these. No
    address can begin with one and none of today's columns are free text,
    which is precisely why this is cheap to keep now and expensive to add
    back after the first column that is."""
    assert _csv_cell(raw).startswith("'")


def test_a_comma_or_a_quote_cannot_shift_a_column():
    assert _csv_cell('slow,"now"') == '"slow,""now"""'
