"""One screen, one path.

Spec 4.5 requires the rename and then requires that exactly one path exist:
update every link that points at it rather than letting two URLs live. Two
live URLs for one screen is how a nav item and a command palette entry end up
at different handlers a year later, and nothing notices until one of them is
the one a customer sends to support.

The storage and the JSON API keep their names. `WhitelistTable`,
`add_whitelist` and `/dashboard/v1/whitelist` are a versioned contract the
agent CLI consumes; the rename is a console concern and must not reach them.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, MitigationStateTable, TenantsTable, create_all_tables,
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
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def test_the_allowed_list_lives_at_one_path(client):
    assert client.get("/dashboard/ui/allowed").status_code == 200


def test_the_old_path_is_gone_rather_than_redirecting(client):
    """A redirect is a second URL that keeps working, and the nav item and
    the command palette drift apart behind it."""
    assert client.get("/dashboard/ui/whitelist").status_code == 404


def test_nothing_in_the_console_still_links_to_the_old_path():
    """The reason this is a task rather than a sed: thirteen references
    across five routes, four templates and the command palette."""
    ui = Path(__file__).resolve().parents[1] / "ui"
    stale = [p for p in list(ui.rglob("*.html")) + list(ui.rglob("*.py"))
             if "/dashboard/ui/whitelist" in p.read_text(encoding="utf-8")]

    assert stale == []


def test_the_json_api_path_is_untouched(client):
    """A versioned contract the agent CLI consumes. The console rename must
    not reach it."""
    assert client.get("/dashboard/v1/whitelist").status_code == 200


def test_allowing_from_the_protection_screen_still_works(client, dynamo_resource):
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=2_000_000_000)

    response = client.post("/dashboard/ui/allowed/10.0.0.7?back=status",
                           headers=_csrf(client))

    assert response.headers.get("HX-Redirect", "").startswith("/dashboard/ui")
    assert MitigationStateTable(dynamo_resource).get(
        tenant_id="t-1", ip="10.0.0.7") is None


def test_the_bulk_action_still_works(client, dynamo_resource):
    for ip in ("10.0.0.7", "10.0.0.8"):
        MitigationStateTable(dynamo_resource).put(
            tenant_id="t-1", ip=ip, tier=2, score=-0.4, z=-6.2,
            reason="behavioral_anomaly", expires_at=2_000_000_000)

    client.post("/dashboard/ui/allowed/bulk?ip=10.0.0.7&ip=10.0.0.8",
                headers=_csrf(client))

    assert MitigationStateTable(dynamo_resource).query_active("t-1") == []


def test_removing_an_entry_still_works(client, dynamo_resource):
    client.post("/dashboard/ui/allowed/10.0.0.7", headers=_csrf(client))

    response = client.delete("/dashboard/ui/allowed/10.0.0.7",
                             headers=_csrf(client))

    assert response.status_code == 200


def test_the_nav_and_the_palette_point_at_the_same_screen():
    """The specific failure the one-path rule exists to prevent."""
    ui = Path(__file__).resolve().parents[1] / "ui" / "templates"
    shell = (ui / "app_shell.html").read_text(encoding="utf-8")
    palette = (ui / "_palette.html").read_text(encoding="utf-8")

    assert "/dashboard/ui/allowed" in shell
    assert "/dashboard/ui/allowed" in palette
