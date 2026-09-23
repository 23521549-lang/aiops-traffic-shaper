"""Allowing several sources at once, and saying how many actually went.

The model over-blocks on the day a customer launches a campaign: twenty
addresses from one office, all genuinely anomalous against last month's
baseline, all genuinely fine. One at a time is twenty confirmations, and
during an incident nobody reads the twentieth.

Two rules this route inherits rather than invents.

Bounded, like `flag_all_for_ip`. Every entry is one synchronous write
against a table on the account-wide 25 WCU pool, so the count a caller may
submit is capped, and a caller over the cap is refused outright rather than
served the first fifty in silence.

And it reports what happened, not what was asked for. A run where three of
five addresses were malformed has to say three, because the operator's next
move is deciding whether to go back for the other two. The outcome rides
home as two integers in the query string: nothing free-text from the request
is ever reflected back into the page.
"""
import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    MitigationStateTable, WhitelistTable, create_all_tables,
)
from services.backend.main import app
from services.backend.tests.conftest import sign_test_token


@pytest.fixture
def client(dynamo_resource, cognito_test_keys):
    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    for i in range(1, 4):
        MitigationStateTable(dynamo_resource).put(
            tenant_id="t-1", ip=f"203.0.113.{i}", tier=2, score=-0.6, z=-6.0,
            reason="behavioral_anomaly", expires_at=0)
    return c


def _csrf(client):
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


def _bulk(client, ips, **extra):
    q = "&".join(f"ip={i}" for i in ips)
    for k, v in extra.items():
        q += f"&{k}={v}"
    return client.post(f"/dashboard/ui/whitelist/bulk?{q}", headers=_csrf(client))


def test_several_sources_are_allowed_in_one_action(client, dynamo_resource):
    _bulk(client, ["203.0.113.1", "203.0.113.2", "203.0.113.3"])

    listed = {e["ip"] for e in WhitelistTable(dynamo_resource).query_by_tenant("t-1")}
    assert listed == {"203.0.113.1", "203.0.113.2", "203.0.113.3"}


def test_the_operator_lands_back_on_the_list_they_acted_from(client):
    r = _bulk(client, ["203.0.113.1", "203.0.113.2"])
    assert r.headers.get("HX-Redirect") == "/dashboard/ui?allowed=2&skipped=0"


def test_a_partial_run_reports_the_number_that_went_through(client, dynamo_resource):
    """Not the number asked for. The difference is what tells the operator
    to go back for the rest."""
    r = _bulk(client, ["203.0.113.1", "not-an-address", "203.0.113.2"])

    assert r.headers.get("HX-Redirect") == "/dashboard/ui?allowed=2&skipped=1"
    assert len(WhitelistTable(dynamo_resource).query_by_tenant("t-1")) == 2


def test_the_page_states_the_outcome_it_was_sent(client):
    page = client.get("/dashboard/ui?allowed=2&skipped=1").text
    assert "2 source" in page
    assert "1" in page


def test_a_run_where_nothing_worked_does_not_read_as_success(client):
    r = _bulk(client, ["nonsense", "also-nonsense"])
    assert r.headers.get("HX-Redirect") == "/dashboard/ui?allowed=0&skipped=2"
    page = client.get("/dashboard/ui?allowed=0&skipped=2").text
    assert "Nothing was allowed" in page


def test_too_many_at_once_is_refused_rather_than_truncated(client, dynamo_resource):
    """Serving the first fifty and dropping the rest in silence is the exact
    failure `flag_all_for_ip` was carrying."""
    r = _bulk(client, [f"198.51.100.{i}" for i in range(1, 60)])

    assert r.status_code == 400
    assert WhitelistTable(dynamo_resource).query_by_tenant("t-1") == []


def test_selecting_nothing_is_not_an_error(client):
    """The bar can be submitted with an empty selection by keyboard before
    the count updates. It must not read as a failure."""
    r = _bulk(client, [])
    assert r.headers.get("HX-Redirect") == "/dashboard/ui?allowed=0&skipped=0"


def test_a_duplicate_address_is_counted_once(client, dynamo_resource):
    """Two checkboxes for the same source is a UI bug, not two allowances,
    and "3 allowed" against two rows would be a lie about a security action."""
    r = _bulk(client, ["203.0.113.1", "203.0.113.1", "203.0.113.2"])
    assert r.headers.get("HX-Redirect") == "/dashboard/ui?allowed=2&skipped=0"


def test_the_outcome_in_the_url_cannot_carry_anything_but_numbers(client):
    """It is reflected into the page, so it is checked on the way out."""
    page = client.get("/dashboard/ui?allowed=<script>&skipped=0").text
    assert "<script>" not in page


def test_bulk_allow_never_touches_another_tenant(client, dynamo_resource):
    _bulk(client, ["203.0.113.1"])
    assert WhitelistTable(dynamo_resource).query_by_tenant("t-2") == []


def test_bulk_is_not_swallowed_by_the_single_source_route(client, dynamo_resource):
    """`/whitelist/bulk` and `/whitelist/{ip}` are both POSTs on the same
    prefix, so the only thing keeping them apart is registration order. If
    that ever flips, this route silently becomes an attempt to allow an
    address literally named "bulk" and every selection is lost with a
    success-shaped response."""
    _bulk(client, ["203.0.113.1"])

    listed = {e["ip"] for e in WhitelistTable(dynamo_resource).query_by_tenant("t-1")}
    assert listed == {"203.0.113.1"}
    assert "bulk" not in listed
