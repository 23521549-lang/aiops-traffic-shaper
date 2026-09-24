"""Every screen, on a tenant that has nothing and on one that has everything.

The failure this catches is the one spec 9 names: a page that renders
perfectly while the feature behind it is silently dead. Each screen has its
own test file, and each of those seeds the exact state it is about - so the
combination nobody writes a test for is the empty account, which is the state
every customer is in on their first day and the state every screen in this
product spends most of its life showing.

It is deliberately shallow. It asserts that every route answers, that no route
answers with a server error, and that no page ships an unrendered template
expression - not what any of them say. The files that care what they say are
next to this one.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    WhitelistTable, create_all_tables,
)
from services.backend.main import app
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token

TENANT_SCREENS = ("/dashboard/ui", "/dashboard/ui/agents",
                  "/dashboard/ui/allowed", "/dashboard/ui/history",
                  "/dashboard/ui/model")
ADMIN_SCREENS = ("/admin/ui", "/admin/ui/tenants", "/admin/ui/tenants/new")

# Anything a template failed to resolve leaves one of these behind.
UNRENDERED = ("{{", "{%", "Undefined", "None None")


@pytest.fixture
def bare(dynamo_resource):
    """A tenant on its first day: no agent, no model, no traffic, nothing
    allowed. The state every screen spends most of its life showing and the
    one no single-feature test seeds."""
    ModelManager._cache.clear()
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


@pytest.fixture
def furnished(bare):
    """The same tenant with one of everything: a live agent, a quiet agent, a
    trained model, traffic in the window, a blocked source, an episode and an
    allowed address."""
    from services.backend.ml.training import train_and_save

    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())
    AgentsTable(bare).put(tenant_id="t-1", agent_id="a-1", agent_label="web-01",
                          registered_at=now.isoformat(),
                          last_seen_at=now.isoformat(), agent_version="1.4.0",
                          api_key_hash="h", status="active",
                          enforcers=["nginx"])
    AgentsTable(bare).put(tenant_id="t-1", agent_id="a-2", agent_label="web-02",
                          registered_at=now.isoformat(),
                          last_seen_at=(now - timedelta(hours=3)).isoformat(),
                          agent_version="1.4.0", api_key_hash="h",
                          status="active", enforcers=[])

    rng = np.random.default_rng(101)
    train_and_save(bare, "t-1",
                   [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0, 0.06)),
                     float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                     float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                     float(rng.uniform(0.02, 0.14))] for _ in range(200)],
                   stage="production", excluded_whitelist=12)

    history = TenantHistoryTable(bare)
    hour = TenantHistoryTable.hour_of(ts)
    history.record_traffic("t-1", hour, requests=900, bins={"n300": 12, "n375": 3})
    history.record_decision("t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
                            hour_start=hour, now=ts,
                            features=[40.0, .9, 90_000., 9., .99, 6., .99],
                            stats_version="v-old")
    MitigationStateTable(bare).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=ts + 3600, decided_at=ts,
        features=[40.0, .9, 90_000., 9., .99, 6., .99])
    WhitelistTable(bare).put(tenant_id="t-1", ip="10.0.0.9",
                             added_at=now.isoformat(), reason="our CDN",
                             added_by="ops@example.com")
    TenantsTable(bare).set_threshold("t-1", "tier1_z", -4.5)
    return bare


def _client(resource, keys, claims):
    app.dependency_overrides[get_dynamo_resource] = lambda: resource
    app.dependency_overrides[get_jwks] = lambda: keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        keys["private_pem"], claims)})
    return c


def _tenant(resource, keys):
    return _client(resource, keys, {"custom:tenant_id": "t-1",
                                    "email": "owner@acme.example"})


def _admin(resource, keys):
    return _client(resource, keys, {"cognito:groups": ["admin"],
                                    "email": "ops@example.com"})


def _check(response, path):
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    for marker in UNRENDERED:
        assert marker not in response.text, f"{path} shipped {marker!r}"


@pytest.mark.parametrize("path", TENANT_SCREENS)
def test_every_tenant_screen_renders_on_a_brand_new_account(path, bare, cognito_test_keys):
    _check(_tenant(bare, cognito_test_keys).get(path), path)


@pytest.mark.parametrize("path", TENANT_SCREENS)
def test_every_tenant_screen_renders_with_one_of_everything(path, furnished, cognito_test_keys):
    _check(_tenant(furnished, cognito_test_keys).get(path), path)


@pytest.mark.parametrize("path", ADMIN_SCREENS)
def test_every_operations_screen_renders_with_no_tenants(path, dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    _check(_admin(dynamo_resource, cognito_test_keys).get(path), path)


@pytest.mark.parametrize("path", ADMIN_SCREENS)
def test_every_operations_screen_renders_with_one_of_everything(path, furnished, cognito_test_keys):
    _check(_admin(furnished, cognito_test_keys).get(path), path)


def test_every_zoom_renders(furnished, cognito_test_keys):
    """The two the URL reaches rather than the nav: a selected source, and a
    selected feature of it."""
    client = _tenant(furnished, cognito_test_keys)

    for path in ("/dashboard/ui?ip=10.0.0.7",
                 "/dashboard/ui?ip=10.0.0.7&feature=post_ratio",
                 "/dashboard/ui/history?ip=10.0.0.7",
                 "/dashboard/ui/history?days=7",
                 "/dashboard/ui/agents?id=a-1",
                 "/admin/ui/tenants?id=t-1"):
        client_for = (_admin(furnished, cognito_test_keys)
                      if path.startswith("/admin") else client)
        _check(client_for.get(path), path)


@pytest.mark.parametrize("path", TENANT_SCREENS + ADMIN_SCREENS)
def test_no_screen_is_reachable_without_signing_in(path, furnished, cognito_test_keys):
    """A console page that renders for an anonymous caller is the one bug on
    this list that is not cosmetic.

    The key set is still overridden and the client still has no cookie:
    FastAPI resolves `get_jwks` as a sub-dependency before `dashboard_auth`
    runs, so without the override this reaches for a real key set and the
    test would be measuring the network rather than the guard.
    """
    app.dependency_overrides[get_dynamo_resource] = lambda: furnished
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    response = TestClient(app, base_url="https://testserver").get(
        path, follow_redirects=False)

    assert response.status_code in (302, 401, 403), path


def test_a_tenant_cannot_open_the_operations_console(furnished, cognito_test_keys):
    """follow_redirects=False, or httpx walks the 302 to the login page and
    reports its 200 - which reads exactly like a tenant opening the
    operations console and is the most alarming way a test can be wrong."""
    client = _tenant(furnished, cognito_test_keys)

    for path in ADMIN_SCREENS:
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 401, 403), path
