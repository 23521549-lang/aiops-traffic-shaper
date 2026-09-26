"""Explanation and control, in the same units, on the same screen.

This is the one thing the spec says must be true of the final design. A rule
engine explains itself with a rule id, and an id is not on a scale, so it
cannot be moved. Traffic Shaper's explanation is already a coordinate in the
customer's own measurement space, which is why it can double as a control
surface - and if the decomposition and the tuning end up in two different
places, the product has thrown away the only thing it owns.
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
    # A fresh container. ModelManager caches at class level across warm
    # invocations on purpose, so without this a test that trains a model
    # leaves it loaded for the next test, which asserts there is none.
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1",
                                     agent_label="web-01", registered_at=now,
                                     last_seen_at=now, agent_version="1.4.0",
                                     api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _with_model(resource):
    """A tenant with a trained model, so the gates are armed."""
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(4)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")


def _control(page):
    """Just the gate control, so a match elsewhere on the page cannot pass
    for a match inside it. The curve is the last thing in its panel, so it
    ends wherever the next structure on the page begins."""
    start = page.index("class=\"gate\"")
    ends = [page.find(marker, start)
            for marker in ("</section>", "<form", "class=\"src\"")]
    return page[start:min([e for e in ends if e != -1], default=len(page))]


def test_all_thirteen_positions_are_offered_at_once(client, dynamo_resource):
    """Not a slider. Thirteen rows, so every outcome is readable without a
    gesture."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert page.count("/dashboard/ui/gate?") == len(TenantHistoryTable.NEAR_BINS)


def test_each_position_is_addressed_entirely_by_its_url(client, dynamo_resource):
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text

    assert 'hx-post="/dashboard/ui/gate?tier=1&sigma=4.0"' in page


def test_the_control_carries_no_request_body(client, dynamo_resource):
    """Behind CloudFront's OAC a POST body needs x-amz-content-sha256, which
    only signed-post.js can compute - and that file has one caller and must
    keep one. All thirteen positions ride in the query string instead, so
    not one of them is a form field."""
    _with_model(dynamo_resource)

    control = _control(client.get("/dashboard/ui").text)

    assert "<input" not in control
    assert 'name="sigma"' not in control


def test_the_current_position_is_marked_without_relying_on_colour(client, dynamo_resource):
    _with_model(dynamo_resource)

    assert 'aria-current="true"' in _control(client.get("/dashboard/ui").text)


def test_the_counts_come_from_this_tenants_own_measurements(client, dynamo_resource):
    _with_model(dynamo_resource)
    hour = TenantHistoryTable.hour_of(int(datetime.now(timezone.utc).timestamp()))
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", hour, requests=10, bins={"n300": 41})

    assert "41" in _control(client.get("/dashboard/ui").text)


def test_the_not_recommended_positions_say_so_in_words(client, dynamo_resource):
    """ADR-006 measured the false-positive rate climbing steeply below 4
    sigma. Colour alone would not survive a screenshot pasted into a
    ticket."""
    _with_model(dynamo_resource)

    assert "báo nhầm" in _control(client.get("/dashboard/ui").text).lower()


def test_the_control_never_offers_a_link_to_the_sub_threshold_sources(client, dynamo_resource):
    """No address below the gate is stored, only the shape. A link there
    would lead nowhere, and a disabled affordance is worse than none."""
    _with_model(dynamo_resource)

    assert "show me" not in client.get("/dashboard/ui").text.lower()


def test_a_tenant_with_no_model_is_not_offered_a_gate_to_move(client):
    """Nothing is enforced yet, so a control that cannot take effect would
    be a promise the product is not keeping."""
    assert "gate-row" not in client.get("/dashboard/ui").text


def test_no_sigma_label_is_uppercased_into_summation(client, dynamo_resource):
    _with_model(dynamo_resource)

    assert "Σ" not in client.get("/dashboard/ui").text


def test_the_control_sits_in_the_same_panel_as_the_axis(client, dynamo_resource):
    """The spec's one non-negotiable: explanation and control are the same
    object. A settings page elsewhere would throw away the only thing this
    product owns."""
    _with_model(dynamo_resource)

    page = client.get("/dashboard/ui").text
    axis_at = page.index("Cổng làm chậm")
    gate_at = page.index("class=\"gate\"")

    assert axis_at < gate_at
    assert "</section>" not in page[axis_at:gate_at]


def test_the_screen_shows_the_gate_of_record_not_the_models_copy(client, dynamo_resource):
    """The gate of record is on Tenants, because save_model rewrites the
    Models item every night and would silently revert the operator. The
    console has to read it from there too: a control whose own screen still
    shows the old position has not visibly done anything."""
    _with_model(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier1_z", -4.5)

    control = _control(client.get("/dashboard/ui").text)
    marked = control[control.index('aria-current="true"'):]

    assert "4.50" in marked[:200]


def test_a_moved_gate_says_it_applies_now_and_not_tonight(client, dynamo_resource):
    """It used to reach enforcement only when the nightly retrain copied it
    onto the model, and the screen said so, which was honest and was still
    the wrong behaviour for a security control.

    The ingest path reads the Tenants item on every batch anyway, so it now
    judges by the value of record. What still lags is the model's copy, and
    the screen distinguishes the two without telling the customer to wait.
    """
    _with_model(dynamo_resource)
    TenantsTable(dynamo_resource).set_threshold("t-1", "tier1_z", -4.5)

    page = client.get("/dashboard/ui").text.lower()

    assert "lô telemetry kế tiếp" in page
    assert "4.0" in page          # the copy the model still carries


def test_an_unmoved_gate_makes_no_claim_about_a_pending_change(client, dynamo_resource):
    _with_model(dynamo_resource)

    assert "lô telemetry kế tiếp" not in client.get("/dashboard/ui").text.lower()
