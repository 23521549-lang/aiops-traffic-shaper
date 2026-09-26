"""What allowing an IP costs, said in the tenant's own numbers.

Allowing a source removes it from the definition of normal, permanently.
`collect_training_vectors` skips every whitelisted IP's buckets, which is
correct and documented, and it means a customer clearing each CDN address
during an incident is deleting their largest traffic source from their own
baseline. Sigma for everything that remains goes up, and the next false block
follows. The product does this today and says nothing about it, which is
exactly the failure principle 1.4 exists to catch.

The figure is counted where the exclusion happens, inside a nightly job that
is already walking every bucket. Nothing here is estimated.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.backend.api.cognito_auth import get_jwks
from services.backend.core.dynamo import get_dynamo_resource
from services.backend.core.tables import (
    AgentsTable, TelemetryEventsTable, TenantsTable, WhitelistTable,
    create_all_tables,
)
from services.backend.main import app
from services.backend.ml.feature_engineering import collect_training_vectors
from services.backend.ml.model import ModelManager
from services.backend.tests.conftest import sign_test_token

BUCKET = 5


def _bucket(resource, ip, start, requests=20, flagged=False):
    TelemetryEventsTable(resource).add_aggregate(
        "t-1", ip, start,
        {"request_count": requests, "error_count": 1, "post_count": 2,
         "total_bytes": 4000, "total_time": 0.4,
         "distinct_uri_count": 3, "distinct_ua_count": 1})
    if flagged:
        TelemetryEventsTable(resource).mark_flagged("t-1", ip, start)


@pytest.fixture
def seeded(dynamo_resource):
    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    return dynamo_resource


def test_the_retrain_counts_what_the_whitelist_removed(seeded):
    """Three buckets, one of them from an allowed IP. The baseline is built
    from two and the count says which."""
    _bucket(seeded, "10.0.0.1", 100)
    _bucket(seeded, "10.0.0.2", 105)
    _bucket(seeded, "10.0.0.9", 110)

    result = collect_training_vectors(seeded, "t-1", bucket_seconds=BUCKET,
                                      exclude_ips={"10.0.0.9"})

    assert len(result.vectors) == 2
    assert result.excluded_whitelist == 1


def test_a_flagged_bucket_is_counted_separately(seeded):
    """Excluded for a different reason, and it must not be reported as the
    cost of allowing: one is the customer's choice and the other is the
    system defending itself."""
    _bucket(seeded, "10.0.0.1", 100)
    _bucket(seeded, "10.0.0.8", 105, flagged=True)
    _bucket(seeded, "10.0.0.9", 110)

    result = collect_training_vectors(seeded, "t-1", bucket_seconds=BUCKET,
                                      exclude_ips={"10.0.0.9"})

    assert result.excluded_flagged == 1
    assert result.excluded_whitelist == 1


def test_a_tenant_with_nothing_allowed_reports_zero_not_nothing(seeded):
    """Zero is a measurement. Absent reads as "we have not looked", and the
    screen has to be able to tell those apart."""
    _bucket(seeded, "10.0.0.1", 100)

    result = collect_training_vectors(seeded, "t-1", bucket_seconds=BUCKET)

    assert result.excluded_whitelist == 0


def test_the_count_reaches_the_model_item(seeded):
    """Counted in the walk, carried on the item, read by a projection that
    already happens. No new query anywhere."""
    from services.backend.core.tables import ModelsTable
    from services.backend.ml.training import train_and_save

    import numpy as np

    rng = np.random.default_rng(23)
    vectors = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
                float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                float(rng.uniform(0.02, 0.14))] for _ in range(200)]

    train_and_save(seeded, "t-1", vectors, stage="production",
                   excluded_whitelist=17)

    item = ModelsTable(seeded).get_metadata(tenant_id="t-1",
                                            stage_version="production")

    assert int(item["excluded_whitelist_buckets"]) == 17
    assert int(item["training_samples"]) == 200


def test_the_retrain_passes_the_count_through(seeded):
    """The end-to-end path. Counting it and then not writing it would be the
    same defect as list_series counting bins it never carried."""
    from services.backend.core.tables import ModelsTable
    from services.backend.retrain_handler import retrain_tenant

    WhitelistTable(seeded).put(tenant_id="t-1", ip="10.0.0.9",
                               added_at="2026-09-01T00:00:00Z",
                               reason="our CDN", added_by="ops@example.com")
    for i in range(120):
        _bucket(seeded, "10.0.0.1", 100 + i * BUCKET)
    for i in range(30):
        _bucket(seeded, "10.0.0.9", 100 + i * BUCKET)

    retrain_tenant(seeded, "t-1")

    item = ModelsTable(seeded).get_metadata(tenant_id="t-1",
                                            stage_version="staging")

    assert int(item["excluded_whitelist_buckets"]) == 30


# --- on the screen ---------------------------------------------------------


@pytest.fixture
def client(seeded, cognito_test_keys):
    ModelManager._cache.clear()
    stamp = datetime.now(timezone.utc).isoformat()
    AgentsTable(seeded).put(tenant_id="t-1", agent_id="a-1",
                            agent_label="web-01", registered_at=stamp,
                            last_seen_at=stamp, agent_version="1.4.0",
                            api_key_hash="h", status="active")
    app.dependency_overrides[get_dynamo_resource] = lambda: seeded
    app.dependency_overrides[get_jwks] = lambda: cognito_test_keys["jwks"]
    c = TestClient(app, base_url="https://testserver")
    c.post("/ui/login", data={"id_token": sign_test_token(
        cognito_test_keys["private_pem"],
        {"custom:tenant_id": "t-1", "email": "ops@example.com"})})
    return c


def _allow(resource, ip, at, reason="our CDN", by="ops@example.com"):
    WhitelistTable(resource).put(tenant_id="t-1", ip=ip, added_at=at,
                                 reason=reason, added_by=by)


def test_the_screen_states_the_mechanism_and_not_just_a_number(client, seeded):
    """A percentage with no explanation is a statistic. The sentence that
    matters is why removing traffic from the baseline moves everything
    else."""
    _allow(seeded, "10.0.0.9", "2026-09-01T00:00:00Z")

    page = client.get("/dashboard/ui/allowed").text.lower()

    assert "baseline" in page
    assert "vĩnh viễn" in page


def test_the_register_names_who_added_each_entry(client, seeded):
    """Spec 4.5: the address, who allowed it, when, and why. `added_by` has
    been written since Phase 0 and the screen showed a reason and no
    actor."""
    _allow(seeded, "10.0.0.9", "2026-09-01T00:00:00Z", by="ops@example.com")

    assert "ops@example.com" in client.get("/dashboard/ui/allowed").text


def test_entries_are_ordered_newest_first(client, seeded):
    """The pattern the spec describes is a cluster added inside one
    incident. A list sorted by address hides exactly that."""
    _allow(seeded, "10.0.0.1", "2026-09-01T00:00:00Z")
    _allow(seeded, "10.0.0.9", "2026-09-20T00:00:00Z")

    page = client.get("/dashboard/ui/allowed").text

    assert page.index("10.0.0.9") < page.index("10.0.0.1")


def test_the_measured_share_is_shown_once_a_retrain_has_measured_it(client, seeded):
    from services.backend.ml.training import train_and_save

    import numpy as np

    _allow(seeded, "10.0.0.9", "2026-09-01T00:00:00Z")
    rng = np.random.default_rng(29)
    vectors = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
                float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                float(rng.uniform(0.02, 0.14))] for _ in range(300)]
    train_and_save(seeded, "t-1", vectors, stage="production",
                   excluded_whitelist=100)

    page = client.get("/dashboard/ui/allowed").text

    # 100 excluded against 300 used: a quarter of what was measured.
    assert "25%" in page


def test_a_model_trained_before_this_existed_says_so(client, seeded):
    """No count on the item. "Not measured yet" and "zero" are different
    claims and the screen may not merge them."""
    from services.backend.ml.training import train_and_save

    import numpy as np

    _allow(seeded, "10.0.0.9", "2026-09-01T00:00:00Z")
    rng = np.random.default_rng(31)
    vectors = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
                float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
                float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
                float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(seeded, "t-1", vectors, stage="production")
    from services.backend.core.tables import ModelsTable
    ModelsTable(seeded).update(
        key={"tenant_id": "t-1", "stage_version": "production"},
        update_expression="REMOVE excluded_whitelist_buckets")

    page = client.get("/dashboard/ui/allowed").text

    assert "Chưa được đo" in page


def test_a_tenant_with_nothing_allowed_is_not_warned_about_a_cost_it_has_not_paid(
        client, seeded):
    """The warning is about a real consequence of a real action. On an empty
    register it is a lecture."""
    page = client.get("/dashboard/ui/allowed").text.lower()

    assert "permanently" not in page
