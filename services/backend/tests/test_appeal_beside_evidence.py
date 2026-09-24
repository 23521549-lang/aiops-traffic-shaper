"""The verb next to the evidence, not in a corner.

Two competent redesigns of this product still read as dashboards, and the
reason was the same both times: the screen explained a decision in seven
lines and put the only response to it somewhere else. An operator who has
just read the line that convinced them has to travel to act on it, and
nothing afterwards records which line it was.

Spec 2.5 names four actions that earn an audit row and a whitelist add is
one of them. The mechanism shipped in Phase 0 and only the threshold move
was ever wired to it, so the single lever a customer has for "you got this
one wrong" left no append-only trace at all - and removing the entry erased
the only record that it had ever been made.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
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
    now = datetime.now(timezone.utc).isoformat()
    AgentsTable(dynamo_resource).put(tenant_id="t-1", agent_id="a-1",
                                     agent_label="web-01", registered_at=now,
                                     last_seen_at=now, agent_version="1.4.0",
                                     api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _blocked_source(resource, ip="10.0.0.7", features=True):
    """One blocked IP, and a model to measure it against so the
    decomposition renders at all."""
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(7)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")
    MitigationStateTable(resource).put(
        tenant_id="t-1", ip=ip, tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=2_000_000_000,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99] if features else None)


def _audit(resource):
    return [r for r in TenantHistoryTable(resource).query_settings(
        "t-1", 0, 2_000_000_000) if r.get("what", "").startswith("whitelist")]


def test_every_driver_row_carries_its_own_appeal(client, dynamo_resource):
    _blocked_source(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7").text
    table = page[page.index("c-why"):page.index("</table>", page.index("c-why"))]

    assert "because=" in table


def test_the_appeal_names_the_feature_it_was_made_from(client, dynamo_resource):
    _blocked_source(dynamo_resource)

    assert "because=post_ratio" in client.get("/dashboard/ui?ip=10.0.0.7").text


def test_allowing_a_source_is_recorded_at_all(client, dynamo_resource):
    """Spec 2.5. The one lever a customer has for "you got this wrong" wrote
    no append-only row, so removing the entry afterwards erased every trace
    that the appeal had ever been made."""
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/allowed/10.0.0.7?back=status", headers=_csrf(client))

    rows = _audit(dynamo_resource)

    assert len(rows) == 1
    assert rows[0]["actor"] == "ops@example.com"
    assert rows[0]["new"] == "10.0.0.7"


def test_the_recorded_appeal_says_which_line_convinced_them(client, dynamo_resource):
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/allowed/10.0.0.7?back=status&because=post_ratio",
                headers=_csrf(client))

    assert _audit(dynamo_resource)[0]["because"] == "post_ratio"


def test_an_appeal_with_no_feature_is_still_recorded(client, dynamo_resource):
    """The corner button stays: sometimes no single line explains it, and an
    operator forced to attribute their reasoning to one feature would pick
    one at random."""
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/allowed/10.0.0.7?back=status", headers=_csrf(client))

    assert "because" not in _audit(dynamo_resource)[0]


def test_removing_an_entry_is_recorded_too(client, dynamo_resource):
    """Otherwise the log says a source was allowed and never says it stopped
    being allowed, which is the more dangerous half of the pair.

    In order, and both times. The sort key was the timestamp at one-second
    resolution, so these two writes landed on the same key and the add was
    overwritten by the remove. "Allowed, then removed" and "removed, then
    allowed" are different events, so separating the rows is not enough on
    its own - they have to come back the way they happened.
    """
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/allowed/10.0.0.7?back=status", headers=_csrf(client))
    client.delete("/dashboard/ui/allowed/10.0.0.7", headers=_csrf(client))

    assert [r["what"] for r in _audit(dynamo_resource)] == [
        "whitelist_add", "whitelist_remove"]


def test_an_invented_feature_name_is_refused(client, dynamo_resource):
    """`because` is reflected into an audit row that a dispute may later turn
    on. It is checked against the seven real feature names rather than
    stored as given."""
    _blocked_source(dynamo_resource)
    response = client.post(
        "/dashboard/ui/allowed/10.0.0.7?back=status&because=%3Cscript%3E",
        headers=_csrf(client))

    assert response.status_code == 400
    assert not _audit(dynamo_resource)


def test_a_refused_appeal_does_not_allow_the_source_anyway(client, dynamo_resource):
    """The validation runs before the whitelist write, not after it. A 400
    that has already let the source through is worse than no check."""
    _blocked_source(dynamo_resource)
    client.post("/dashboard/ui/allowed/10.0.0.7?back=status&because=nonsense",
                headers=_csrf(client))

    assert MitigationStateTable(dynamo_resource).get(
        tenant_id="t-1", ip="10.0.0.7") is not None


def test_a_source_with_no_decomposition_offers_no_per_line_appeal(client, dynamo_resource):
    """Models trained before per-feature statistics existed produce no rows.
    A pane showing the corner button and no lines is correct; one showing
    empty lines with buttons on them is not."""
    _blocked_source(dynamo_resource, ip="10.0.0.8", features=False)

    assert "because=" not in client.get("/dashboard/ui?ip=10.0.0.8").text


def test_the_corner_action_is_still_there(client, dynamo_resource):
    """Removing it would force every appeal to be attributed to one of seven
    lines, and an operator who disagrees with the whole judgement would pick
    a line at random to get the work done."""
    _blocked_source(dynamo_resource)
    page = client.get("/dashboard/ui?ip=10.0.0.7").text

    assert "Allow this source" in page
