"""Getting an agent onto a machine, without the step that did not exist.

The landing page said: "Get the token from Detection model in the console
after signing in." No page in the console has ever shown a token. That is
the first instruction a new customer follows, and it pointed at nothing, so
there was no path from signing up to being protected at all.

The token was never the right answer either. It is a bearer credential for
the whole account, it would have to be displayed on a screen and pasted
through a shell history, and it expires in an hour. The console is already
authenticated as that tenant, so it can simply mint the agent itself and
hand back the one credential the agent actually needs.

`/agent/v1/register` already does exactly this and already takes the
session. Nothing new is minted, nothing new is stored, and the tenant sees
their own agent key once.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import AgentsTable, TenantsTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def test_the_agents_page_offers_a_way_to_add_one(client):
    assert "Add an agent" in client.get("/dashboard/ui/agents").text


def test_adding_an_agent_creates_it_for_this_tenant(client, dynamo_resource):
    client.post("/dashboard/ui/agents?label=web-01", headers=_csrf(client))

    agents = AgentsTable(dynamo_resource).query_by_tenant("t-1")
    assert [a["agent_label"] for a in agents] == ["web-01"]


def test_the_key_is_shown_once_and_only_once(client, dynamo_resource):
    """It is stored hashed, so there is no second chance to display it, and
    the page has to say so plainly. An operator who assumes they can come
    back for it loses the machine's credential."""
    page = client.post("/dashboard/ui/agents?label=web-01",
                       headers=_csrf(client)).text

    assert "shown once" in page
    agent = AgentsTable(dynamo_resource).query_by_tenant("t-1")[0]
    key = page.split("--api-key ")[1].split("<")[0].strip()
    assert key
    # Never stored in the clear: what came back cannot be read off the row.
    assert key not in str(agent)


def test_the_page_hands_over_a_command_that_runs(client):
    """Not a token to paste into an instruction that no longer exists."""
    page = client.post("/dashboard/ui/agents?label=web-01",
                       headers=_csrf(client)).text

    assert "traffic-shaper run" in page
    assert "t-1" in page


def test_an_unlabelled_agent_is_refused_rather_than_named_after_a_uuid(client):
    """The label is the only thing that will tell its owner which machine
    went quiet. A blank one produces a fleet of indistinguishable rows."""
    r = client.post("/dashboard/ui/agents?label=", headers=_csrf(client))
    assert "name" in r.text.lower()


def test_a_suspended_tenant_cannot_mint_a_new_agent(client, dynamo_resource):
    """The dashboard session outlives the suspension, so without this a
    just-suspended tenant simply mints a fresh key and carries on."""
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="suspended", created_at="2026-08-21T00:00:00Z")

    client.post("/dashboard/ui/agents?label=web-01", headers=_csrf(client))

    assert AgentsTable(dynamo_resource).query_by_tenant("t-1") == []


def test_the_new_agent_appears_in_the_list_immediately(client):
    page = client.post("/dashboard/ui/agents?label=web-01",
                       headers=_csrf(client)).text
    assert "web-01" in page
