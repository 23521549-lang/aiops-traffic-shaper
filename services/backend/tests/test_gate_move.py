"""A customer moving their own enforcement threshold.

This is the change the whole rebuilt console exists for. TIER1_Z was a module
constant, so the product could say "6.4 standard deviations outside your
normal" and offer no way to say "for me, act at 4.5". Every verb was in a
corner, which is why two competent redesigns still read as dashboards.

Moving it is one of the four actions that earn an audit row: it changes
enforcement for all future traffic on a live site, and a reasonable person
could later dispute it with money attached.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    TenantHistoryTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _move(client, tier=1, sigma=4.25):
    return client.post(f"/dashboard/ui/gate?tier={tier}&sigma={sigma}",
                       headers=_csrf(client))


def test_moving_the_gate_stores_it_on_the_tenant(client, dynamo_resource):
    """On Tenants, not on Models: save_model rewrites the model item every
    night and would silently revert the operator."""
    _move(client, tier=1, sigma=4.25)

    tenant = TenantsTable(dynamo_resource).get(tenant_id="t-1")
    assert float(tenant["tier1_z"]) == -4.25


def test_the_stored_value_is_negative_because_z_is(client, dynamo_resource):
    """The screen speaks in magnitudes and the model speaks in z. Storing
    +5.5 would make classify() tier nothing at all, silently."""
    _move(client, tier=2, sigma=5.5)

    assert float(TenantsTable(dynamo_resource).get(tenant_id="t-1")["tier2_z"]) == -5.5


def test_the_move_is_recorded_with_who_and_both_values(client, dynamo_resource):
    _move(client, tier=1, sigma=4.25)

    rows = TenantHistoryTable(dynamo_resource).query_settings(
        "t-1", 0, 2_000_000_000)

    assert len(rows) == 1
    assert rows[0]["actor"] == "ops@example.com"
    assert rows[0]["what"] == "tier1_z"
    assert float(rows[0]["new"]) == -4.25


def test_a_position_that_is_not_one_of_the_thirteen_is_refused(client, dynamo_resource):
    """The curve offers thirteen. Accepting 4.1 would set a gate whose
    consequence the screen cannot show, because no bin measures it, and a
    control that can be put somewhere it cannot report on is a control that
    lies."""
    response = _move(client, tier=1, sigma=4.1)

    assert response.status_code == 400
    assert "tier1_z" not in (TenantsTable(dynamo_resource).get(tenant_id="t-1") or {})


def test_the_slow_gate_may_not_be_placed_above_the_block_gate(client, dynamo_resource):
    """Then every source past the block line is blocked without ever being
    slowed, and the middle band is empty and meaningless."""
    _move(client, tier=2, sigma=4.5)
    response = _move(client, tier=1, sigma=5.0)

    assert response.status_code == 400
    # Absent, not merely different: a refused move writes nothing at all,
    # rather than writing something safer than what was asked for.
    assert "tier1_z" not in TenantsTable(dynamo_resource).get(tenant_id="t-1")


def test_the_block_gate_may_not_be_dropped_below_the_slow_gate(client, dynamo_resource):
    """The same invariant from the other side."""
    _move(client, tier=1, sigma=4.5)
    response = _move(client, tier=2, sigma=4.0)

    assert response.status_code == 400


def test_one_tenant_cannot_move_another_tenants_gate(client, dynamo_resource):
    TenantsTable(dynamo_resource).put(tenant_id="t-2", name="Globex",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    _move(client, tier=1, sigma=4.25)

    assert "tier1_z" not in (TenantsTable(dynamo_resource).get(tenant_id="t-2") or {})


def test_the_operator_lands_back_on_the_screen_they_moved_it_from(client):
    """The consequence of the move is the thing they were reading. A
    response they never see is the defect hx-swap="none" already caused
    once."""
    assert _move(client).headers.get("HX-Redirect") == "/dashboard/ui"


def test_the_route_takes_no_form_body(client):
    """Behind CloudFront's OAC a body needs x-amz-content-sha256, which only
    signed-post.js can compute, and that file has one caller and must keep
    one. The values ride in the query string, like the theme toggle."""
    import inspect

    from services.backend.ui import dashboard

    source = inspect.getsource(dashboard.move_gate)
    assert "Form(" not in source
