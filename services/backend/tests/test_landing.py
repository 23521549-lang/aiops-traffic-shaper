"""The public surface.

Until now `/` was a 302 to a form asking for a pasted JWT. Not one
comparable product sends an anonymous visitor to a credential field, and for
a product whose entire value is a screen, the landing page IS the screen.

Everything asserted here follows from one property: **the landing page reads
zero DynamoDB.** That is what makes it cacheable, unmeterable and incapable
of leaking tenant data.
"""
import re

from fastapi.testclient import TestClient

from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import UsageCountersTable, create_all_tables
from services.backend.core.usage import _today
from services.backend.main import app


def _client(dynamo_resource):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    return TestClient(app, base_url="https://testserver")


def _requests_today(resource) -> int:
    row = UsageCountersTable(resource).get(date=_today())
    return int(row["total_requests"]) if row else 0


def test_an_anonymous_visitor_gets_the_product_not_a_login_form(dynamo_resource):
    resp = _client(dynamo_resource).get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert "Traffic của bạn có một mức bình thường" in resp.text


def test_a_signed_in_visitor_goes_straight_to_their_console(dynamo_resource):
    """Showing a pitch to someone who already uses the product is a small
    insult. The check is the mere PRESENCE of the cookie, never its
    validity: verifying it would mean a JWKS fetch on an anonymous page,
    which makes the page uncacheable and hands an unauthenticated caller a
    lever on a network round trip. An expired cookie lands on the console
    and is bounced to login one hop later, which is correct by a longer
    route."""
    client = _client(dynamo_resource)
    client.cookies.set("id_token", "anything-at-all")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard/ui"


def test_the_landing_page_costs_no_dynamodb_write(dynamo_resource):
    """It is the most exposed URL the deployment has. Metering it would let
    a drive-by visitor spend the free-tier write budget the whole product is
    built around."""
    client = _client(dynamo_resource)
    before = _requests_today(dynamo_resource)
    for _ in range(5):
        client.get("/", follow_redirects=False)
    assert _requests_today(dynamo_resource) == before


def test_the_page_carries_no_inline_style_attribute(dynamo_resource):
    """`style-src 'self'` blocks inline style attributes as well as <style>
    blocks, and a blocked style is silently ignored rather than reported —
    far harder to notice than a broken build. This is the test that keeps
    the policy tight."""
    html = _client(dynamo_resource).get("/").text
    assert not re.search(r'\sstyle="', html)
    assert "<style" not in html


def test_the_hero_illustration_is_the_products_own_chart(dynamo_resource):
    """The landing page's pictures are the console's components fed
    representative numbers. A page whose illustrations are bespoke marketing
    graphics drifts away from the product it is selling; this one cannot."""
    html = _client(dynamo_resource).get("/").text
    assert 'class="c-deviation"' in html
    assert "c-bar--blocked" in html
    assert "c-bar--limited" in html


def test_the_chart_is_readable_without_seeing_it(dynamo_resource):
    """Every chart ships a text equivalent. An SVG whose values a screen
    reader cannot reach is not a chart, it is a picture of one."""
    html = _client(dynamo_resource).get("/").text
    assert "<title>" in html and "<desc>" in html
    assert "Nguồn khác thường nhất" in html


def test_the_page_states_what_the_product_cannot_do(dynamo_resource):
    """This section is where comparable products put a customer logo wall.
    Keeping it is the strongest anti-generic decision on the page, and it
    matches the tone the repo already uses in its own README."""
    html = _client(dynamo_resource).get("/").text
    assert "Những thứ nó chưa làm được" in html
    assert "Chưa khách hàng bên ngoài nào dùng" in html
    assert "Chúng tôi có lưu địa chỉ IP của người dùng cuối" in html


def test_the_false_positive_rate_is_published_with_its_caveat(dynamo_resource):
    """ADR-006 measured it and buried it in a markdown file. Publishing
    0.27% without "measured on modelled traffic" would be the dishonest
    half of the same act."""
    html = _client(dynamo_resource).get("/").text
    assert "0.27%" in html
    assert "traffic mô phỏng" in html


def test_the_page_is_reachable_without_javascript(dynamo_resource):
    """The FAQ is <details>/<summary>, the nav is anchors, the theme toggle
    is a form. Nothing on this page requires a script to be readable."""
    html = _client(dynamo_resource).get("/").text
    assert "<details>" in html
    assert html.count("<summary>") >= 8


# --- theme --------------------------------------------------------------

def test_the_theme_is_a_cookie_not_a_script(dynamo_resource):
    """The usual implementation is a <script> in <head> that reads
    localStorage before first paint. Under `script-src 'self'` with no
    inline script there is nowhere to put it — and a server-rendered
    attribute is better anyway: no flash of the wrong theme at all, and it
    works with JavaScript off."""
    client = _client(dynamo_resource)
    # Query string, not a form body. A body-less POST needs no
    # x-amz-content-sha256 payload hash at the CloudFront edge (ADR-005), so
    # this is the one shape that works through the real distribution without
    # the signing shim.
    resp = client.post("/ui/prefs/theme?theme=dark", follow_redirects=False)
    assert resp.status_code == 303
    assert client.cookies.get("theme") == "dark"


def test_choosing_system_clears_the_preference(dynamo_resource):
    client = _client(dynamo_resource)
    client.post("/ui/prefs/theme?theme=dark", follow_redirects=False)
    client.post("/ui/prefs/theme?theme=system", follow_redirects=False)
    assert not client.cookies.get("theme")


def test_an_unknown_theme_falls_back_rather_than_being_stored(dynamo_resource):
    client = _client(dynamo_resource)
    client.post("/ui/prefs/theme?theme=neon", follow_redirects=False)
    assert not client.cookies.get("theme")


def test_the_theme_toggle_cannot_be_used_as_an_open_redirect(dynamo_resource):
    """An unauthenticated endpoint that redirects anywhere is a phishing
    primitive: a link on our domain that lands on someone else's."""
    client = _client(dynamo_resource)
    for hostile in ("https://evil.example/x", "//evil.example/x"):
        resp = client.post(f"/ui/prefs/theme?theme=dark&next_url={hostile}",
                           follow_redirects=False)
        assert resp.headers["location"] == "/"


# --- typography ----------------------------------------------------------

def test_no_em_dash_reaches_any_page(dynamo_resource, cognito_test_keys):
    """The product owner asked for the em dash to be gone from the UI.

    It is easy to remove once and easy to reintroduce, because an em dash is
    the natural thing to reach for when joining two clauses. This walks every
    page a visitor or a signed-in user can reach and fails on the character
    itself, so the next person who types one finds out here rather than on a
    screenshot.

    Jinja comments and Python docstrings are not covered and do not need to
    be: they never render.
    """
    from services.backend.api.cognito_auth import get_jwks
    from services.backend.core.tables import TenantHistoryTable
    from services.backend.tests.conftest import sign_test_token

    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]

    # Populated rather than empty, so the tables, badges and chart captions
    # are actually in the output being checked.
    import time
    hour = TenantHistoryTable.hour_of(int(time.time()) - 3600)
    TenantHistoryTable(dynamo_resource).record_decision(
        "t-1", "198.51.100.66", hour_start=hour, tier=2,
        now=hour + 60, score=-0.2, z=-5.9)

    public = TestClient(app, base_url="https://testserver")
    tenant = TestClient(app, base_url="https://testserver")
    tenant.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    admin = TestClient(app, base_url="https://testserver")
    admin.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"cognito:groups": ["admin"]})})

    pages = [
        (public, "/"), (public, "/ui/login"), (public, "/not-a-page"),
        (tenant, "/dashboard/ui"), (tenant, "/dashboard/ui/history"),
        (tenant, "/dashboard/ui/allowed"), (tenant, "/dashboard/ui/model"),
        (admin, "/admin/ui"), (admin, "/admin/ui/tenants"),
        (admin, "/admin/ui/tenants/new"), (admin, "/admin/ui/agents"),
    ]

    offenders = []
    for client, path in pages:
        body = client.get(path, headers={"accept": "text/html"}).text
        for line in body.splitlines():
            if "—" in line:
                offenders.append(f"{path}: {line.strip()[:70]}")
    assert not offenders, "em dash rendered on:\n" + "\n".join(offenders)
