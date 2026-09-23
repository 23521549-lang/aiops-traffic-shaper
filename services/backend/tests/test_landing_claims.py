"""The landing page describes the product. It has to describe this one.

Everything on that page was true when it was written, and the install block
had rotted into fiction on four separate lines at once:

  - `traffic-shaper run` did not exist. The CLI had `register` and `status`,
    neither of which read a log or sent anything.
  - "a thin Python client tails your nginx access log" described code that
    was never written. Nothing parsed a log line into a record.
  - "get the token from Detection model in the console" pointed at a page
    that has never shown a token.
  - the config path was wrong.

Every one of them sits on the first instruction a new customer follows, and
no test in the product could see any of it, because the page was a template
and the CLI was a module and nothing ever compared them.

This file compares them. It is deliberately narrow: it does not police prose,
it checks that the commands the page tells a stranger to run are commands
that exist, and that the paths it names are the paths the code uses.
"""
import pytest
from fastapi.testclient import TestClient

from services.agent.cli import cli
from services.agent.config import DEFAULT_CONFIG_PATH
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.main import app


@pytest.fixture
def page(dynamo_resource):
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver").get("/").text


def _commands(html: str) -> set[str]:
    """Every `traffic-shaper <verb>` the page instructs a reader to run."""
    found = set()
    for chunk in html.split("traffic-shaper ")[1:]:
        verb = chunk.split()[0].strip("<>&;.,")
        if verb.isalpha():
            found.add(verb)
    return found


def test_every_command_the_page_prints_exists(page):
    real = set(cli.commands)
    for verb in _commands(page):
        assert verb in real, (
            f"the landing page tells people to run 'traffic-shaper {verb}', "
            f"which the CLI does not have. It has: {sorted(real)}")


def test_the_page_names_the_config_path_the_agent_actually_writes(page):
    """It said ~/.traffic-shaper/config.json for months. The agent has always
    written ~/.aiops-agent/config.json."""
    assert DEFAULT_CONFIG_PATH.name == "config.json"
    directory = DEFAULT_CONFIG_PATH.parent.name
    assert directory in page, (
        f"the page does not mention {directory}, which is where the agent "
        f"puts its credentials")


def test_the_page_does_not_send_people_looking_for_a_token(page):
    """No page in the console has ever displayed one, and now nothing needs
    one: the console mints the agent itself."""
    assert "ID_TOKEN" not in page
    assert "Get the token" not in page


def test_the_page_does_not_claim_a_sign_in_method_that_was_replaced(page):
    """It advertised "no hosted sign-in, you paste a Cognito ID token" after
    password sign-in had already shipped."""
    assert "paste a Cognito" not in page
    assert "No hosted sign-in" not in page


def test_the_page_still_admits_what_is_missing(page):
    """The honesty section is load-bearing. A rewrite that quietly drops it
    turns this page into every other security vendor's page."""
    assert "doesn" in page and "yet" in page
    assert "No single sign-on" in page
