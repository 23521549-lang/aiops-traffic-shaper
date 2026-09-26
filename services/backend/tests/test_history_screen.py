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


# THREE MORE REMOVED, all of them about a grid CELL.
#
# "one row per hour", "an empty cell says so rather than printing zero", and
# "the gate lines use this tenant's own thresholds" have no counterpart on a
# timeline: it has no cells and no gate lines. The principle behind the
# second one survives as a sentence, tested above.

def test_the_history_screen_draws_the_window(client, dynamo_resource):
    """The grid this replaced spent 168 cells answering "did anything
    happen", and the answer is almost always no. The timeline says the same
    thing in a tenth of the height, and every mark is pressable."""
    _hours_of_traffic(dynamo_resource)

    assert 'class="tl"' in client.get("/dashboard/ui/history").text


def test_the_deviation_chart_is_gone_from_the_console(client, dynamo_resource):
    """Spec 3.4: the grid replaces it. Two charts of one window in two
    shapes leaves the operator working out which to believe."""
    _hours_of_traffic(dynamo_resource)

    assert "Worst deviation per hour" not in client.get("/dashboard/ui/history").text


# REMOVED: the gates drawn cutting across the grid.
#
# The timeline has no gate lines, because it plots EPISODES and an episode is
# by definition already past a gate. Drawing the lines would be drawing a
# threshold every mark on the chart has already crossed.


def test_a_window_with_nothing_measured_says_so_in_words(client, dynamo_resource):
    """Principle 1.3, carried over. A quiet window and a dead agent look
    identical on any chart, so the distinction is made in a sentence rather
    than in a shade of a cell."""
    page = client.get("/dashboard/ui/history").text

    assert "Không có telemetry nào trong khoảng này." in page


def test_the_page_costs_one_mark_per_episode_and_no_more(client, dynamo_resource):
    """The requirement behind the old cell count, restated for the shape
    that replaced it: a window with two episodes draws two marks, whatever
    the window length, so a long history cannot become a long page."""
    _hours_of_traffic(dynamo_resource)

    page = client.get("/dashboard/ui/history").text

    assert page.count('class="tl-ep"') == page.count('class="src-h"')


def test_the_grid_carries_a_table_for_a_screen_reader(client, dynamo_resource):
    """Position in a picture reads as nothing. A summary, not a matrix: the
    cell counts read aloud one by one answer no question anyone has."""
    _hours_of_traffic(dynamo_resource)
    page = client.get("/dashboard/ui/history").text

    assert "<caption>" in page[page.index('class="tl"'):]


def test_the_grid_offers_no_link_to_the_sub_threshold_sources(client, dynamo_resource):
    """Spec 5.3. No address below the gate is stored, only the shape, so a
    link there would lead nowhere."""
    _hours_of_traffic(dynamo_resource)

    assert "show me" not in client.get("/dashboard/ui/history").text.lower()


def test_a_window_with_no_telemetry_at_all_draws_no_reading(client):
    """Principle 1.2. An instrument with no feed does not render a reading
    with a warning beside it; it renders no reading."""
    page = client.get("/dashboard/ui/history").text

    assert 'class="tl-ep"' not in page
    assert "Không có telemetry" in page


def test_no_sigma_label_is_uppercased_into_summation(client, dynamo_resource):
    _hours_of_traffic(dynamo_resource)

    assert "Σ" not in client.get("/dashboard/ui/history").text


def test_the_table_is_not_a_matrix_of_every_bin(client, dynamo_resource):
    """312 numbers read aloud answer no question anyone has."""
    _hours_of_traffic(dynamo_resource)

    page = client.get("/dashboard/ui/history").text
    table = page[page.index("<caption>"):page.index("</table>",
                                                    page.index("<caption>"))]

    assert table.count("<td>") < 100


def test_the_table_names_the_hour_rather_than_an_epoch(client, dynamo_resource):
    """Read aloud one row at a time, so a raw timestamp would be the worst
    possible thing in the first column."""
    _hours_of_traffic(dynamo_resource)

    page = client.get("/dashboard/ui/history").text
    table = page[page.index("<caption>"):page.index("</table>",
                                                    page.index("<caption>"))]

    assert ":00" in table


