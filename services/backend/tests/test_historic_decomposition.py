"""Why a decision was taken, after the fact.

The whole argument for this product is that it can answer that question in
the customer's own units. Answering it only while the block is still active
makes it a monitoring feature; answering it a week later, in a ticket, is the
thing being sold.

The catch this screen exists to handle: `z` and the feature vector are frozen
at decision time and the baseline is read live. One nightly retrain between
them and the two halves of the explanation describe different models. The
episode carries `stats_version` for exactly this, and nothing else in the
product is in a position to notice.
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


def _trained(resource):
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(11)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")


def _episode(resource, ip="10.0.0.7", *, version=None, features=True):
    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(resource).record_decision(
        "t-1", ip=ip, tier=2, score=-0.4, z=-6.2,
        hour_start=TenantHistoryTable.hour_of(now), now=now,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99] if features else None,
        stats_version=version)


def test_an_episode_from_last_week_still_explains_itself(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    assert "POST ratio" in client.get("/dashboard/ui/history?ip=10.0.0.7").text


def test_filtering_by_source_actually_filters(client, dynamo_resource):
    """The link from the detail pane has always pointed here. The route had
    no `ip` parameter, so it silently showed the unfiltered list and the
    operator read the wrong object."""
    _trained(dynamo_resource)
    _episode(dynamo_resource)
    _episode(dynamo_resource, ip="10.0.0.9")

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "10.0.0.7" in page
    assert "10.0.0.9" not in page


def test_the_unfiltered_page_still_lists_everything(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource)
    _episode(dynamo_resource, ip="10.0.0.9")

    page = client.get("/dashboard/ui/history").text

    assert "10.0.0.7" in page and "10.0.0.9" in page


def test_a_baseline_that_has_moved_since_is_said_plainly(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource, version="an-older-model")

    assert "retrained" in client.get("/dashboard/ui/history?ip=10.0.0.7").text.lower()


def test_a_baseline_that_has_not_moved_makes_no_such_claim(client, dynamo_resource):
    from services.backend.ml.registry import load_model_and_stats

    _trained(dynamo_resource)
    _, stats = load_model_and_stats(dynamo_resource, "t-1")
    _episode(dynamo_resource, version=stats.version)

    assert "retrained" not in client.get("/dashboard/ui/history?ip=10.0.0.7").text.lower()


def test_an_episode_that_never_recorded_its_version_claims_nothing(client, dynamo_resource):
    """Episodes written before Phase 0 carry no version. "We cannot tell" is
    not the same as "it moved", and asserting the latter would put a warning
    on every old episode in the product."""
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    assert "retrained" not in client.get("/dashboard/ui/history?ip=10.0.0.7").text.lower()


def test_an_episode_with_no_vector_says_so_instead_of_inventing_one(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource, features=False)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "does not carry" in page.lower()


def test_an_unknown_source_is_an_empty_filter_not_an_error(client, dynamo_resource):
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    assert client.get("/dashboard/ui/history?ip=10.0.0.99").status_code == 200


def test_the_chart_keeps_measuring_everything(client, dynamo_resource):
    """The chart above is total traffic per hour. Filtering it to one source
    would be a different quantity wearing the same axis, and the caption
    would still say what it said before."""
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert "Worst deviation per hour" in page


def test_the_filter_says_what_it_is_filtering_and_how_to_leave(client, dynamo_resource):
    """A filtered list that looks like an unfiltered one is how an operator
    concludes a source has no other episodes."""
    _trained(dynamo_resource)
    _episode(dynamo_resource)

    page = client.get("/dashboard/ui/history?ip=10.0.0.7").text

    assert 'href="/dashboard/ui/history"' in page
