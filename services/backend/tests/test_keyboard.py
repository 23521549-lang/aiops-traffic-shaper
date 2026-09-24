"""Every shortcut the console prints on screen has to exist.

Four detail panes already render `Close <kbd>Esc</kbd>` and `Allow this
source <kbd>A</kbd>`. No key handler has ever been shipped: not in
ui-status.js, not in bulk-select.js, nowhere. The product was printing
promises.

That is worse than having no shortcuts. A hint in the interface is a claim
about what the software does, and an operator who trusts it presses Esc over
an open pane during an incident and gets nothing. Silence is the one
response that teaches them to stop trusting the rest of the hints too.

So the binding lives in the markup, next to the hint, and the script is a
dispatcher that knows no page-specific keys at all. That is what makes the
first test below possible: a page cannot advertise a key without also
declaring it, because the two are the same edit.
"""
import re

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    MitigationStateTable, TenantsTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

# The element that carries the hint, with whatever came before it on the
# same tag. `data-key` has to be on that element or the dispatcher cannot
# find it from the key press.
HINTED = re.compile(r"<(a|button)\b([^>]*)>(?:(?!</\1>).)*?<kbd class=\"c-key\">([^<]+)</kbd>",
                    re.DOTALL)


def _client(dynamo_resource, cognito_test_keys, claims):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], claims)})
    return c


@pytest.fixture
def tenant(dynamo_resource, cognito_test_keys):
    c = _client(dynamo_resource, cognito_test_keys, {"custom:tenant_id": "t-1"})
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="203.0.113.1", tier=2, score=-0.6, z=-6.0,
        reason="behavioral_anomaly", expires_at=0)
    return c


@pytest.fixture
def admin(dynamo_resource, cognito_test_keys):
    c = _client(dynamo_resource, cognito_test_keys, {"cognito:groups": ["admin"]})
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme", status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return c


PAGES_WITH_PANES = [
    ("tenant", "/dashboard/ui?ip=203.0.113.1"),
    ("tenant", "/dashboard/ui/agents"),
    ("admin", "/admin/ui/tenants?id=t-1"),
]


@pytest.mark.parametrize("who,url", PAGES_WITH_PANES)
def test_no_shortcut_is_advertised_without_being_bound(who, url, request):
    client = request.getfixturevalue(who)
    html = client.get(url).text

    hints = HINTED.findall(html)
    assert hints, f"{url} renders no shortcut hints; the pattern stopped matching"
    for tag, attrs, key in hints:
        # A modifier combination is the palette's, and the dispatcher takes
        # it globally; the control still has to exist, so the marker that
        # makes it clickable is what stands in for a binding here.
        wanted = "data-palette-open" if ("Ctrl" in key or "⌘" in key) else "data-key="
        assert wanted in attrs, (
            f"{url} prints the {key!r} hint on a <{tag}> that binds nothing")


def test_escape_closes_the_source_pane(tenant):
    """Closing is a navigation back to the list, so the key is bound to the
    link that already does it rather than to a script that guesses a URL."""
    html = tenant.get("/dashboard/ui?ip=203.0.113.1").text
    assert 'data-key="Escape"' in html


def test_escape_is_not_bound_when_there_is_no_pane_to_close(tenant):
    """A stray binding would swallow Escape from the browser's own uses."""
    html = tenant.get("/dashboard/ui").text
    assert 'data-key="Escape"' not in html


# --- the palette ---------------------------------------------------------

def test_the_console_carries_a_command_palette(tenant):
    html = tenant.get("/dashboard/ui").text
    assert 'id="palette"' in html
    assert "<dialog" in html


def test_the_palette_is_server_rendered_not_fetched(tenant):
    """Opening it during an incident must not depend on a round trip, and a
    palette that fetches its own contents is a palette that is empty when
    the network is the thing going wrong."""
    html = tenant.get("/dashboard/ui").text
    dialog = html.split('id="palette"', 1)[1].split("</dialog>", 1)[0]
    assert "/dashboard/ui/allowed" in dialog
    assert "Detection model" in dialog
    assert "data-palette-item" in dialog


def test_the_palette_offers_only_what_this_role_can_reach(tenant):
    """It is a menu of links, so a publisher entry here would be a 403 with
    extra steps and a disclosure of what the other console contains."""
    html = tenant.get("/dashboard/ui").text
    assert "/admin/ui" not in html


def test_the_publisher_palette_is_the_publisher_one(admin):
    html = admin.get("/admin/ui").text
    dialog = html.split('id="palette"', 1)[1].split("</dialog>", 1)[0]
    assert "/admin/ui/tenants" in dialog
    assert "/dashboard/ui" not in dialog


def test_the_palette_is_not_on_the_public_pages(dynamo_resource, cognito_test_keys):
    """Nothing to navigate to, and the landing page reads zero DynamoDB by
    design: a nav menu there would be the first thing to change that.

    Signed out on purpose. Signed in, both of these redirect into the
    console, and the test would be asserting about the console."""
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    anon = TestClient(app, base_url="https://testserver")

    assert 'id="palette"' not in anon.get("/").text
    assert 'id="palette"' not in anon.get("/ui/login").text


def test_the_palette_says_how_to_open_it(tenant):
    """An unannounced shortcut is a shortcut nobody uses."""
    assert "K" in tenant.get("/dashboard/ui").text


def test_the_filter_box_does_not_swallow_escape(tenant):
    """Measured in Chromium: a non-empty `input type="search"` handles
    Escape itself, clearing the field, and the key never reaches the
    <dialog>. The palette stayed open on the first press, which reads as a
    dead key on the control whose whole promise is speed."""
    html = tenant.get("/dashboard/ui").text
    dialog = html.split('id="palette"', 1)[1].split("</dialog>", 1)[0]
    assert 'type="search"' not in dialog
    assert 'type="text"' in dialog
