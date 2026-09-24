"""The history screen, once the axis is the only instrument on it.

Spec 3.4 is explicit that the grid REPLACES the deviation chart rather than
joining it. Two charts of the same window in two different shapes is the
dashboard failure this rebuild exists to undo: the operator has to work out
which one to believe, and nothing on the screen helps them.
"""
from datetime import datetime, timezone

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


def _hours_of_traffic(resource, dead_hour=True):
    """Three hours: one busy, one with a block, and one with no telemetry at
    all, which is the row the grid has to draw differently."""
    history = TenantHistoryTable(resource)
    now = int(datetime.now(timezone.utc).timestamp())
    hour = TenantHistoryTable.hour_of(now)

    history.record_traffic("t-1", hour - 7200, requests=900,
                           bins={"n300": 12, "n375": 3})
    history.record_traffic("t-1", hour, requests=400, bins={"n325": 5})
    history.record_decision("t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-5.5,
                            hour_start=hour, now=now)


def test_the_history_screen_draws_the_grid(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)

    assert "c-grid" in client.get("/dashboard/ui/history").text


def test_the_deviation_chart_is_gone_from_the_console(client, dynamo_resource):
    """Spec 3.4: the grid replaces it. Two charts of one window in two
    shapes leaves the operator working out which to believe."""
    _hours_of_traffic(dynamo_resource)

    assert "Worst deviation per hour" not in client.get("/dashboard/ui/history").text


def test_the_gates_cut_through_the_whole_grid(client, dynamo_resource):
    """Not a decoration per row. The shape exists so an operator sees the
    gate literally cutting across their own history."""
    _hours_of_traffic(dynamo_resource)

    assert client.get("/dashboard/ui/history").text.count("c-grid-gate ") == 2


def test_an_hour_with_no_telemetry_is_drawn_as_a_hole(client, dynamo_resource):
    """Principle 1.3. An empty row from a working agent and an empty row
    from a dead one look identical, and only one is evidence."""
    _hours_of_traffic(dynamo_resource)

    assert "c-grid-dead" in client.get("/dashboard/ui/history").text


def test_only_populated_cells_reach_the_page(client, dynamo_resource):
    """Spec 3.4 states this as a requirement. Three populated bins and one
    identified source is four cells, whatever the window length."""
    _hours_of_traffic(dynamo_resource)

    # Counted on the opening of the class attribute: an identified cell
    # carries both c-grid-cell and c-grid-cell--id, so a bare substring count
    # reports it twice.
    assert client.get("/dashboard/ui/history").text.count('class="c-grid-cell') == 4


def test_the_grid_carries_a_table_for_a_screen_reader(client, dynamo_resource):
    """Position in a picture reads as nothing. A summary, not a matrix: the
    cell counts read aloud one by one answer no question anyone has."""
    _hours_of_traffic(dynamo_resource)
    page = client.get("/dashboard/ui/history").text

    assert "<caption>" in page[page.index("c-grid"):]


def test_the_grid_offers_no_link_to_the_sub_threshold_sources(client, dynamo_resource):
    """Spec 5.3. No address below the gate is stored, only the shape, so a
    link there would lead nowhere."""
    _hours_of_traffic(dynamo_resource)

    assert "show me" not in client.get("/dashboard/ui/history").text.lower()


def test_a_window_with_no_telemetry_at_all_draws_no_reading(client):
    """Principle 1.2. An instrument with no feed does not render a reading
    with a warning beside it; it renders no reading."""
    page = client.get("/dashboard/ui/history").text

    assert 'class="c-grid-cell' not in page
    assert "no telemetry" in page.lower()


def test_the_grid_uses_the_tenants_own_gates(client, dynamo_resource):
    """Bands drawn from the module constants would show every tenant
    somebody else's threshold, on their own history."""
    _hours_of_traffic(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier1_z", -3.5)

    from services.backend.ui.charts import axis_x

    page = client.get("/dashboard/ui/history").text

    assert f'x1="{axis_x(3.5)}"' in page


def test_no_sigma_label_is_uppercased_into_summation(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)

    assert "Σ" not in client.get("/dashboard/ui/history").text
