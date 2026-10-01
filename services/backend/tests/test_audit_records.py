"""Four actions in this product can be disputed later, with money, blame or
security attached. Those four get an append-only record. Nothing else does,
and nothing gets an undo - all four already have an inverse action.

The systems review said "no audit trail" and the BA's success criteria
demanded one for every mutating action. Both narrowed: this is four
append-only rows, not an audit subsystem, and it fits at 1 WCU per change on
a table that already exists, using a fourth sort-key prefix in the same
single-table design.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

NOW = 1758700805


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


def test_a_threshold_change_records_who_when_and_both_values(history):
    """The value that changes enforcement for all future traffic on a live
    site. Non-negotiable."""
    history.record_setting("t-1", actor="ops@example.com", what="tier1_z",
                           old=-4.0, new=-4.5, now=NOW)

    rows = history.query_settings("t-1", NOW - 60, NOW + 60)

    assert len(rows) == 1
    assert rows[0]["actor"] == "ops@example.com"
    assert rows[0]["what"] == "tier1_z"
    assert float(rows[0]["old"]) == -4.0
    assert float(rows[0]["new"]) == -4.5


def test_records_are_append_only_and_do_not_overwrite(history):
    history.record_setting("t-1", actor="a@x", what="tier1_z", old=-4.0,
                           new=-4.5, now=NOW)
    history.record_setting("t-1", actor="b@x", what="tier1_z", old=-4.5,
                           new=-5.0, now=NOW + 1)

    assert len(history.query_settings("t-1", NOW - 60, NOW + 60)) == 2


def test_one_tenants_records_are_not_another_tenants(history):
    history.record_setting("t-2", actor="a@x", what="tier1_z", old=-4.0,
                           new=-4.5, now=NOW)

    assert history.query_settings("t-1", NOW - 60, NOW + 60) == []


def test_settings_do_not_appear_in_the_episode_or_series_reads(history):
    """A fourth prefix on the same table only works if the three existing
    range reads cannot see it. `mit#`, `agg#` and `set#` sort in that order,
    so a careless `between` would sweep settings into the history page."""
    history.record_setting("t-1", actor="a@x", what="tier1_z", old=-4.0,
                           new=-4.5, now=NOW)

    assert history.query_episodes("t-1", NOW - 7200, NOW + 7200) == []
    assert history.query_series("t-1", NOW - 7200, NOW + 7200, fill=False) == []


def test_the_record_row_carries_no_free_text_from_the_request(history):
    """The episode row rule applies here too: fixed-size, capped. `actor`
    comes from a verified JWT claim, never from a form field."""
    history.record_setting("t-1", actor="ops@example.com", what="tier1_z",
                           old=-4.0, new=-4.5, now=NOW)

    row = history.query_settings("t-1", NOW - 60, NOW + 60)[0]
    size = sum(len(str(k)) + len(str(v)) for k, v in row.items())

    assert size < 1024


def test_the_whitelist_records_who_added_the_entry(dynamo_resource, cognito_test_keys):
    """`added_by` is in docs/schema.md and has never been written. In a
    multi-user tenant, "who let this IP in, and why" had no answer."""
    from fastapi.testclient import TestClient

    from services.backend.api.cognito_auth import get_jwks
    from services.backend.core.dynamo import get_dynamo_resource
    from services.backend.core.tables import WhitelistTable
    from services.backend.main import app
    from services.backend.tests.conftest import sign_test_token

    create_all_tables(dynamo_resource)
    app.dependency_overrides[get_dynamo_resource] = lambda: dynamo_resource
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    client = TestClient(app, base_url="https://testserver")
    client.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})

    client.post("/dashboard/ui/allowed/203.0.113.4",
                headers={"X-CSRF-Token": client.cookies["csrf_token"]})

    entry = WhitelistTable(dynamo_resource).get(tenant_id="t-1", ip="203.0.113.4")
    assert entry["added_by"] == "ops@example.com"
