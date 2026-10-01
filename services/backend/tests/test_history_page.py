"""The screen that closes PRD US-6's second acceptance criterion.

"View recent mitigation history" has been in the PRD since 2026-08-21 and
was unmeetable against the schema designed after it: `MitigationState` is
keyed `(tenant_id, ip)`, so a repeat decision overwrote the previous one and
TTL deleted the rest. Nothing flagged the contradiction for a year.
"""
import time

from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import TenantHistoryTable, create_all_tables
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token

HOUR = 3600


def _client(dynamo_resource, cognito_test_keys, tenant_id="t-1"):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    token = sign_test_token(cognito_test_keys["private_pem"],
                            {"custom:tenant_id": tenant_id})
    client.post("/ui/login", data={"id_token": token})
    return client


def _seed(resource, tenant_id="t-1", hours_ago=2, ip="198.51.100.66",
          tier=2, z=-5.9, count=3):
    table = TenantHistoryTable(resource)
    hour = TenantHistoryTable.hour_of(int(time.time()) - hours_ago * HOUR)
    for n in range(count):
        table.record_decision(tenant_id, ip, hour_start=hour, tier=tier,
                              now=hour + 60 + n * 30, score=-0.2, z=z)
    table.record_traffic(tenant_id, hour_start=hour, requests=400,
                         tier1=0 if tier == 2 else count,
                         tier2=count if tier == 2 else 0)
    return hour


def test_the_page_lists_what_the_product_did(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource)

    page = client.get("/dashboard/ui/history").text
    assert "198.51.100.66" in page
    assert "Đang chặn" in page


def test_repeated_decisions_read_as_one_episode_with_a_count(
        dynamo_resource, cognito_test_keys):
    """"17 decisions" as a bare number reads like 17 separate attacks. The
    split between blocked and slowed is what shows an escalation."""
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource, count=7)

    page = client.get("/dashboard/ui/history").text
    # Not a substring count on the address: the row links to the source
    # detail, so it appears in the href and in the link text. Not a count of
    # row headers either, because the chart ships a text-equivalent table
    # whose rows carry them too. `data-tier` appears on episode rows and
    # nowhere else.
    assert page.count('class="src-h"') == 1
    assert "7 quyết định" in page
    # The count was printed twice, so the cell read "7 7 blocked". The
    # assertion above passed throughout, because the substring it looks for
    # was genuinely present. Only a screenshot showed it. Pin the whole
    # cell, not a fragment of it.
    assert "7 7" not in page


def test_severity_is_shown_in_sigma_not_raw_score(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource, z=-5.9)

    page = client.get("/dashboard/ui/history").text
    assert "5.9σ" in page
    assert "-0.2" not in page


def test_an_unmeasurable_episode_says_so(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource, z=None, tier=1)

    page = client.get("/dashboard/ui/history").text
    # The table column is a figure now; "không đo được" is the phrase
    # the detail pane uses. A source with no usable spread reads "n/a".
    assert "n/a" in page


def test_history_does_not_leak_across_tenants(dynamo_resource, cognito_test_keys):
    """The guarantee the whole product rests on, on a newly added read
    path."""
    client = _client(dynamo_resource, cognito_test_keys, "t-1")
    _seed(dynamo_resource, tenant_id="t-2", ip="9.9.9.9")

    page = client.get("/dashboard/ui/history").text
    assert "9.9.9.9" not in page


def test_a_quiet_window_with_telemetry_still_renders_the_full_chrome(
        dynamo_resource, cognito_test_keys):
    """The healthy state of this product is empty, so the empty state is the
    most-viewed screen. The bands and the gates ARE the evidence that the
    system was watching; a blank panel is not.

    "Im lặng" here means telemetry arrived and nothing crossed a gate. That is
    the distinction the test this replaces did not draw: it seeded nothing at
    all and then demanded the chrome, which under principle 1.2 is an
    instrument with no feed rendering a reading.
    """
    client = _client(dynamo_resource, cognito_test_keys)
    TenantHistoryTable(dynamo_resource).record_traffic(
        "t-1", TenantHistoryTable.hour_of(int(time.time()) - HOUR),
        requests=400)

    page = client.get("/dashboard/ui/history").text

    assert "Bảy ngày qua" in page or "1 ngày qua" in page
    assert "Không có gì vượt cổng của bạn trong khoảng này." in page


def test_a_window_with_no_telemetry_at_all_renders_no_reading(
        dynamo_resource, cognito_test_keys):
    """Principle 1.2, and the reason the test above had to be split. Nothing
    was measured, so bands and gates drawn across an empty plot would be an
    instrument with no feed showing a scale as though it meant something."""
    client = _client(dynamo_resource, cognito_test_keys)

    page = client.get("/dashboard/ui/history").text

    assert "c-seg" not in page
    assert "không có telemetry" in page.lower()


def test_the_page_says_where_history_begins(dynamo_resource, cognito_test_keys):
    """There is no backfill and none is possible — the data that would have
    filled it was overwritten and TTL-deleted. Saying so beats letting a
    customer conclude the feature is broken."""
    client = _client(dynamo_resource, cognito_test_keys)
    page = client.get("/dashboard/ui/history").text
    assert "Lịch sử bắt đầu từ ngày tính năng này được bật" in page


def test_new_episodes_are_marked_until_they_are_read(
        dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource)

    assert "Đánh dấu 1 đã đọc" in client.get("/dashboard/ui/history").text

    client.post("/dashboard/ui/history/mark-read",
                headers={"X-CSRF-Token": client.cookies["csrf_token"]})
    assert "Đánh dấu 1 đã đọc" not in client.get("/dashboard/ui/history").text


def test_marking_read_needs_the_csrf_token(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    assert client.post("/dashboard/ui/history/mark-read").status_code == 403


# --- the API underneath --------------------------------------------------

def test_the_window_is_capped(dynamo_resource, cognito_test_keys):
    """A capacity control, not a UX preference: 30 days of a busy tenant is
    ~15 RCU in one query against a table provisioned at 2. Refusing with a
    message that names the cap beats truncating silently — which is exactly
    how the retrain ended up training on a partial window for months."""
    client = _client(dynamo_resource, cognito_test_keys)
    now = int(time.time())
    resp = client.get(f"/dashboard/v1/history?since={now - 30 * 86400}&until={now}")
    assert resp.status_code == 400
    assert "7 days" in resp.json()["detail"]


def test_a_backwards_window_is_refused(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    now = int(time.time())
    assert client.get(
        f"/dashboard/v1/history?since={now}&until={now - 3600}").status_code == 400


def test_the_series_fills_quiet_hours_with_zero(dynamo_resource, cognito_test_keys):
    """DynamoDB has no row for an hour in which nothing happened. A chart
    that skips those hours draws a flat line across an outage instead of a
    hole."""
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource, hours_ago=6)

    now = int(time.time())
    points = client.get(f"/dashboard/v1/series?since={now - 12 * HOUR}&until={now}").json()
    assert len(points) >= 12
    assert any(p["requests"] == 0 for p in points)
    assert any(p["requests"] > 0 for p in points)


def test_filtering_by_ip_narrows_the_list(dynamo_resource, cognito_test_keys):
    client = _client(dynamo_resource, cognito_test_keys)
    _seed(dynamo_resource, ip="198.51.100.66")
    _seed(dynamo_resource, ip="203.0.113.9", hours_ago=3)

    rows = client.get("/dashboard/v1/history?ip=203.0.113.9").json()
    assert [r["ip"] for r in rows] == ["203.0.113.9"]
