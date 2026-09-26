"""The shape of your normal, and the question we cannot answer.

Spec 4.7 asks this screen for three things: the seven-feature baseline, the
training state, and the staging model - which is written every night even when
promotion is refused, deliberately, because it is the evidence for why, and
which no screen has ever read. Evidence nobody can see is not evidence.

Spec 4.8 is the part that takes discipline. "What did you do for me this week"
cannot be answered: nothing records requests actually refused, because the
agent enforces locally and does not report back. A decision count may be
shown and must be labelled as exactly that. Letting it read as "requests
blocked" merges two different quantities, which is principle 1.4 with a
number attached.
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


def _vectors(seed, n=200):
    import numpy as np

    rng = np.random.default_rng(seed)
    return [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(n)]


def _trained(resource, stage="production", seed=37):
    from services.backend.ml.training import train_and_save

    return train_and_save(resource, "t-1", _vectors(seed), stage=stage)


def test_the_screen_shows_the_shape_of_normal(client, dynamo_resource):
    """Seven rows, each naming what this tenant's ordinary traffic looks like
    on that dimension. This is the sentence the product exists to be able to
    say, and the model item has carried the numbers since Phase 0."""
    _trained(dynamo_resource)

    page = client.get("/dashboard/ui/model").text

    for label in ("Số request mỗi phút", "Tỉ lệ lỗi", "Tỉ lệ POST",
                  "Độ tản của user agent"):
        assert label in page


def test_the_baseline_gives_a_spread_and_not_only_a_middle(client, dynamo_resource):
    """A mean on its own is not a shape. The whole product is built on
    distance measured in standard deviations, so the standard deviation is
    half the answer."""
    _trained(dynamo_resource)

    assert "±" in client.get("/dashboard/ui/model").text


def test_the_gates_shown_are_this_tenants_own(client, dynamo_resource):
    """The screen had 4.0 and 5.0 written into the template and read the
    module constants, so a customer who moved their gate was shown somebody
    else's threshold on the page that explains their model."""
    _trained(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier1_z", -4.5)

    page = client.get("/dashboard/ui/model").text

    assert "4.50" in page


def test_the_staging_model_is_shown_rather_than_only_written(client, dynamo_resource):
    """It is written every night even when promotion is refused, on purpose,
    because it is the evidence for why. No screen has ever read it, so the
    evidence has never been evidence to anyone."""
    _trained(dynamo_resource, stage="production", seed=37)
    staging = _trained(dynamo_resource, stage="staging", seed=41)

    page = client.get("/dashboard/ui/model").text

    assert staging.version in page


def test_a_staging_model_identical_to_production_is_not_announced(client, dynamo_resource):
    """The ordinary night. Promotion happened, both stages hold the same
    version, and a panel headed "not promoted" would be alarming and
    wrong."""
    meta = _trained(dynamo_resource, stage="production", seed=37)
    from services.backend.ml.registry import save_model
    from services.backend.ml import registry
    from dataclasses import replace

    model = registry.load_model(dynamo_resource, "t-1", stage="production")
    save_model(dynamo_resource, "t-1", model, replace(meta, stage="staging"),
               stage="staging")

    page = client.get("/dashboard/ui/model").text

    assert "not promoted" not in page.lower()


def test_a_tenant_with_no_model_is_told_what_has_to_happen(client):
    page = client.get("/dashboard/ui/model").text.lower()

    assert "baseline" in page or "normal" in page
    assert "night" in page


def test_the_decision_count_is_labelled_as_decisions_not_as_requests(client, dynamo_resource):
    """Spec 4.8. Nothing records requests actually refused - the agent
    enforces locally and does not report back."""
    _trained(dynamo_resource)

    page = client.get("/dashboard/ui/model").text.lower()

    assert "requests blocked" not in page


def test_the_screen_says_plainly_what_it_cannot_tell_you(client, dynamo_resource):
    """Job number four, named in the BA report and unanswerable. Saying so
    beats a number that looks like the answer."""
    _trained(dynamo_resource)

    page = client.get("/dashboard/ui/model").text.lower()

    assert "không nói được" in page or "does not report back" in page


def test_the_history_summary_does_not_read_as_requests_either(client, dynamo_resource):
    """The same quantity, on the screen where it is most likely to be read
    as traffic. "412 blocked" beside a traffic chart is a claim about
    requests."""
    from services.backend.core.tables import TenantHistoryTable

    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(dynamo_resource).record_decision(
        "t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-5.5,
        hour_start=TenantHistoryTable.hour_of(now), now=now)

    page = client.get("/dashboard/ui/history").text
    # The headline specifically, not the table header below it: a column
    # called "Decisions" does not stop "412 blocked" reading as traffic.
    at = page.index('class="sub"')
    headline = page[at:page.index("</p>", at)]

    assert "đợt" in headline


def test_the_screen_never_fetches_the_blob(client, dynamo_resource, monkeypatch):
    """The model item is about 238KB, roughly 30 RCU against a table
    provisioned at 2, to print a version string. It is the worst
    read-to-value ratio in the product and it sits on a page a customer is
    invited to open."""
    from services.backend.core.tables import ModelsTable

    _trained(dynamo_resource)
    calls = []
    real = ModelsTable.get

    def watched(self, **key):
        calls.append(key)
        return real(self, **key)

    monkeypatch.setattr(ModelsTable, "get", watched)
    client.get("/dashboard/ui/model")

    assert calls == []
