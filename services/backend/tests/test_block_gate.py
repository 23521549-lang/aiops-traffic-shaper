"""The block gate, on the window that has evidence for it.

ADR-006 measured 0.00% false positives below z = -5.0. That is the finding
that makes the tier-2 gate safe, and it is also what makes the tier-2 gate
impossible to tune on a day of data: the 5.0 to 6.0 bins are empty on almost
every ordinary day, so a 24-hour curve offers thirteen rows reading zero at
exactly the place the operator is being asked to put the line.

Seven days of bins is about 20 RCU per view, which is why it lives behind the
`?days=7` tab that already exists rather than being switched on everywhere.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantHistoryTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    stamp = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1",
                                     agent_label="web-01", registered_at=stamp,
                                     last_seen_at=stamp, agent_version="1.4.0",
                                     api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _trained(resource):
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(17)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")


def _bins_across_days(resource):
    """Three near-misses today, four more six days back. A 24-hour window
    sees three of these and a 7-day window sees all seven, which is the whole
    reason the block gate lives on the longer one."""
    history = TenantHistoryTable(resource)
    now = datetime.now(timezone.utc)
    history.record_traffic("t-1", TenantHistoryTable.hour_of(int(now.timestamp())),
                           requests=100, bins={"n525": 3})
    old = int((now - timedelta(days=6)).timestamp())
    history.record_traffic("t-1", TenantHistoryTable.hour_of(old),
                           requests=100, bins={"n525": 4})


def test_the_block_gate_is_not_offered_on_the_24_hour_view(client, dynamo_resource):
    """A curve with no evidence at the line it is asking about is worse than
    no curve: it reads as thirteen measured zeroes."""
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)

    assert "tier=2" not in client.get("/dashboard/ui/history?days=1").text


def test_the_block_gate_is_offered_on_the_7_day_view(client, dynamo_resource):
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)

    assert "tier=2" in client.get("/dashboard/ui/history?days=7").text


def test_it_says_which_window_the_counts_came_from(client, dynamo_resource):
    """The same control on the Protection screen counts 24 hours. Two
    identical curves reporting different numbers with nothing saying why is
    how an operator concludes the product is broken."""
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)

    assert "7 days" in client.get("/dashboard/ui/history?days=7").text


def test_the_curve_counts_the_whole_seven_days(client, dynamo_resource):
    """Three today plus four six days back. A 7-day control silently
    counting only 24 hours would be the same defect wearing a longer label."""
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)

    page = client.get("/dashboard/ui/history?days=7").text
    control = page[page.index("c-gate-curve"):]
    first_row = control[:control.index("</button>")]

    assert ">7<" in first_row


def test_a_tenant_with_no_model_is_not_offered_the_block_gate(client, dynamo_resource):
    """Nothing is enforced yet, so a control that cannot take effect is a
    promise the product is not keeping."""
    _bins_across_days(dynamo_resource)

    assert "tier=2" not in client.get("/dashboard/ui/history?days=7").text


def test_the_block_gate_is_not_the_slow_gate(client, dynamo_resource):
    """One screen offering two controls that look identical and do different
    things is how an operator blocks at the line they meant to slow at."""
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)

    assert "tier=1" not in client.get("/dashboard/ui/history?days=7").text


def test_the_current_block_gate_is_the_one_marked(client, dynamo_resource):
    _trained(dynamo_resource)
    _bins_across_days(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier2_z", -5.5)

    page = client.get("/dashboard/ui/history?days=7").text
    control = page[page.index("c-gate-curve"):]
    marked = control[control.index('aria-current="true"'):]

    assert "5.50" in marked[:250]
