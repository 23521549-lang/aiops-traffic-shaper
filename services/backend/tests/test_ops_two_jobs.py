"""Two jobs, and only two.

Spec 6 is unusually blunt about this surface: it answers "am I about to be
billed" and "which tenant caused it, and do I suspend them". A third nav item
is a third job.

The Agents page was a filter over a list wearing the shape of a page. It had
its own nav item, its own template and its own tab strip, and the question it
answered - which machines have gone quiet - is a column on the tenant it
belongs to. `_tenant_detail` was already querying exactly those agents.

Deleting a page is only correct if the answer survives it, which is most of
what this file checks.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"cognito:groups": ["admin"], "email": "ops@example.com"})})
    return c


def _tenant_with_a_quiet_agent(resource):
    now = datetime.now(timezone.utc)
    TenantsTable(resource).put(tenant_id="t-1", name="Acme", status="active",
                               created_at="2026-08-21T00:00:00Z")
    AgentsTable(resource).put(
        tenant_id="t-1", agent_id="a-1", agent_label="web-01",
        registered_at=now.isoformat(),
        last_seen_at=(now - timedelta(hours=3)).isoformat(),
        agent_version="1.4.0", api_key_hash="h", status="active")


def test_the_agents_page_is_gone(client):
    assert client.get("/admin/ui/agents").status_code == 404


def test_the_operations_console_offers_exactly_two_jobs(client, dynamo_resource):
    """Am I about to be billed, and which tenant caused it. Counted off the
    nav rather than off a list in a test, so adding a page is what breaks
    this and not forgetting to update it."""
    _tenant_with_a_quiet_agent(dynamo_resource)
    page = client.get("/admin/ui").text
    nav = page[page.index("c-nav"):page.index("</nav>")]

    assert nav.count("c-nav-link") == 2


def test_a_quiet_agent_is_still_findable(client, dynamo_resource):
    """Deleting the page must not delete the answer. It was a filter over a
    list, and the list is the tenant table."""
    _tenant_with_a_quiet_agent(dynamo_resource)

    page = client.get("/admin/ui/tenants?id=t-1").text

    assert "web-01" in page
    assert "Quiet" in page


def test_the_overview_still_counts_the_quiet_ones(client, dynamo_resource):
    """The figure is half of job one: a fleet that has gone quiet is not
    being billed for and is also not being protected."""
    _tenant_with_a_quiet_agent(dynamo_resource)

    assert "gone quiet" in client.get("/admin/ui").text.lower()


def test_the_quiet_figure_leads_somewhere_that_still_exists(client, dynamo_resource):
    """A number with no way in is a dead end, and the way in it had was the
    page this task removes."""
    _tenant_with_a_quiet_agent(dynamo_resource)

    page = client.get("/admin/ui").text

    assert "/admin/ui/agents" not in page


def test_nothing_anywhere_still_links_to_the_removed_page():
    ui = Path(__file__).resolve().parents[1] / "ui"
    stale = [p for p in list(ui.rglob("*.html")) + list(ui.rglob("*.py"))
             if "/admin/ui/agents" in p.read_text(encoding="utf-8")]

    assert stale == []


def test_the_command_palette_does_not_offer_a_page_that_is_gone(client, dynamo_resource):
    """The palette is the fastest way to reach a 404 in the whole product."""
    _tenant_with_a_quiet_agent(dynamo_resource)

    page = client.get("/admin/ui/tenants").text
    palette = page[page.index("id=\"palette\""):] if "id=\"palette\"" in page else page

    assert "/admin/ui/agents" not in palette


def test_the_template_is_gone_rather_than_orphaned():
    """An unrendered template is a file the next person edits believing it
    ships."""
    ui = Path(__file__).resolve().parents[1] / "ui"

    assert not (ui / "templates" / "admin_agents.html").exists()
