"""A Greek letter does not explain itself.

The product's entire argument rests on one: distance from this tenant's own
normal, measured in standard deviations. Every screen prints it and no screen
has ever said what it means. A reader who has to translate before they can act
is a reader who stops, and this is a console somebody opens while something is
going wrong.

The words never replace the figure. An operator pasting a row into a ticket
needs 6.41, and a reader deciding whether to worry needs to know that 6.41 is
far. Both, in the same place.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TenantsTable, create_all_tables,
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


def _with_model(resource):
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(71)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")


def test_the_gate_screen_says_what_sigma_means(client, dynamo_resource):
    """In words, before the first number that uses it."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text
    at = page.index('class="plain"')

    assert "mức khác thường" in page[at:at + 500].lower()


def test_the_explanation_comes_before_the_axis(client, dynamo_resource):
    """A definition underneath the thing it defines has already failed the
    reader who needed it."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert page.index('class="plain"') < page.index('class="pipe"')


def test_the_scale_is_labelled_in_words_at_both_ends(client, dynamo_resource):
    """The ticks read 0 to 6. A reader who does not know the unit cannot
    tell whether 6 is the good end."""
    _with_model(dynamo_resource)

    # The sigma scale lives on the public page now, and a signed-in client
    # is redirected off it, so the words are checked with a client that has
    # not signed in - which is also the reader the labels exist for.
    from fastapi.testclient import TestClient

    from services.backend.main import app as _app

    public = TestClient(_app).get("/").text

    assert "giống ngày thường của bạn" in public
    assert "rất không giống bạn" in public


def test_the_words_do_not_replace_the_numbers(client, dynamo_resource):
    """An operator pasting a row into a ticket needs the figure itself, and
    a reader deciding whether to worry needs to know the figure is far. The
    screen owes both."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert "4.00" in page          # the gate, as a number
    assert "sigma" in page.lower()  # and named


def test_a_tenant_with_no_model_is_not_taught_a_unit_it_cannot_see(client):
    """Nothing is measured yet, the scale carries no reading, and the
    explanation would be a lesson about nothing on screen."""
    assert 'class="plain"' not in client.get("/dashboard/ui").text


def test_the_explanation_is_one_sentence_a_person_would_say(client, dynamo_resource):
    """Not a definition of standard deviation. The test is whether somebody
    could say it out loud to a colleague without reading it off a card."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text
    at = page.index('class="plain"')
    block = page[at:page.index("</p>", at)]

    for jargon in ("standard deviation", "z-score", "gaussian", "variance"):
        assert jargon not in block.lower(), jargon


# REMOVED: the history screen labelling its sigma scale in words.
# It no longer has a sigma scale. The timeline plots episodes against
# TIME, and its axis is labelled in days, which needs no gloss.

