"""One feature, and only what was actually measured about it.

The per-tenant statistics are a mean and a standard deviation per feature.
That is a position and it is not a distribution: a bell curve drawn from those
two numbers would be an assertion about the shape of this tenant's traffic
that nothing in the system has ever measured. What IS measured is the value
frozen onto each hourly episode, which is a real series.
"""
from services.backend.schemas.history import MitigationEpisode
from services.backend.ui.charts import (
    FEATURE_TRACK_HEIGHT, feature_runs, feature_track,
)


def _episode(hour, features=None):
    return MitigationEpisode(ip="10.0.0.7", hour_start=hour, first_ts=hour,
                             last_ts=hour, tier1_count=1,
                             last_features=features or [])


def _episodes():
    return [_episode(3600, [1.0, 0.0, 700.0, 0.1, 0.5, 1.0, 0.05]),
            _episode(7200, [3.0, 0.0, 700.0, 0.1, 0.5, 1.0, 0.05])]


def test_a_point_per_episode_in_the_chosen_dimension():
    track = feature_track(_episodes(), index=0, mean=1.0, std=1.0)

    assert [p.sigma for p in track] == [0.0, 2.0]


def test_an_episode_with_no_vector_is_a_hole_not_a_zero():
    """Episodes written before Phase 0 carry no features. Plotting them at
    the mean would draw a source sitting exactly on this tenant's normal in
    an hour when it was being blocked."""
    episodes = _episodes() + [_episode(10800)]

    assert feature_track(episodes, index=0, mean=1.0, std=1.0)[-1].sigma is None


def test_a_degenerate_baseline_measures_nothing_rather_than_dividing():
    """std is zero when every training bucket had the same value on this
    feature. There is no sigma to report, and reporting zero would say the
    source is normal."""
    track = feature_track(_episodes(), index=0, mean=1.0, std=0.0)

    assert all(p.sigma is None for p in track)


def test_a_short_vector_is_refused_rather_than_mismatched():
    """A vector shorter than the feature list would line the wrong number up
    with the wrong baseline, and the result would look entirely plausible."""
    episodes = [_episode(3600, [1.0, 2.0])]

    assert feature_track(episodes, index=5, mean=1.0, std=1.0)[0].sigma is None


def test_the_track_is_ordered_by_hour():
    """query_episodes returns newest first, because a log is read from the
    top. A chart is read left to right."""
    track = feature_track(list(reversed(_episodes())), index=0, mean=1.0, std=1.0)

    assert [p.hour_start for p in track] == [3600, 7200]


def test_x_is_time_and_y_is_distance():
    track = feature_track(_episodes(), index=0, mean=1.0, std=1.0)

    assert track[0].x == 0
    assert track[-1].x > track[0].x
    assert track[1].y < track[0].y       # further out is higher on screen


def test_one_episode_is_a_point_not_a_division_by_zero():
    track = feature_track(_episodes()[:1], index=0, mean=1.0, std=1.0)

    assert len(track) == 1 and track[0].x == 0


def test_no_episodes_is_an_empty_track():
    assert feature_track([], index=0, mean=1.0, std=1.0) == []


def test_a_source_below_its_normal_is_drawn_as_far_out_as_one_above_it():
    """Distance from normal, not direction. A feature four sigma BELOW this
    tenant's baseline is exactly as anomalous as one four sigma above, and
    the sigma value keeps the sign so the table beside it can say which."""
    low = feature_track([_episode(3600, [-3.0, 0, 0, 0, 0, 0, 0])],
                        index=0, mean=1.0, std=1.0)
    high = feature_track([_episode(3600, [5.0, 0, 0, 0, 0, 0, 0])],
                         index=0, mean=1.0, std=1.0)

    assert low[0].sigma == -4.0 and high[0].sigma == 4.0
    assert low[0].y == high[0].y


def test_a_reading_past_the_ceiling_is_clamped_to_it_rather_than_drawn_off_screen():
    track = feature_track([_episode(3600, [99.0, 0, 0, 0, 0, 0, 0])],
                          index=0, mean=1.0, std=1.0)

    assert track[0].y == 0
    assert track[0].sigma == 98.0        # the reading itself is never clipped


def test_every_point_stays_inside_the_drawing_area():
    track = feature_track(_episodes(), index=0, mean=1.0, std=1.0)

    assert all(0 <= p.y <= FEATURE_TRACK_HEIGHT for p in track)


def test_a_hole_genuinely_breaks_the_line():
    """One polyline through everything draws a straight segment across the
    hours with no reading, which asserts the source was somewhere it was
    never measured."""
    track = feature_track([_episode(3600, [1.0, 0, 0, 0, 0, 0, 0]),
                           _episode(7200),
                           _episode(10800, [5.0, 0, 0, 0, 0, 0, 0])],
                          index=0, mean=1.0, std=1.0)

    runs = feature_runs(track)

    assert [len(r) for r in runs] == [1, 1]


def test_consecutive_readings_stay_one_line():
    runs = feature_runs(feature_track(_episodes(), index=0, mean=1.0, std=1.0))

    assert [len(r) for r in runs] == [2]


def test_a_track_with_no_readings_at_all_draws_nothing():
    track = feature_track([_episode(3600), _episode(7200)],
                          index=0, mean=1.0, std=1.0)

    assert feature_runs(track) == []


def test_a_leading_hole_does_not_open_an_empty_run():
    track = feature_track([_episode(3600), _episode(7200, [2.0, 0, 0, 0, 0, 0, 0])],
                          index=0, mean=1.0, std=1.0)

    assert [len(r) for r in feature_runs(track)] == [1]


# --- the screen ------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.backend.api.cognito_auth import get_jwks  # noqa: E402
from services.backend.core.dynamo import get_dynamo_resource  # noqa: E402
from services.backend.core.tables import (  # noqa: E402
    AgentsTable, MitigationStateTable, TenantHistoryTable, TenantsTable,
    create_all_tables,
)
from services.backend.main import app  # noqa: E402
from services.backend.ml.model import ModelManager  # noqa: E402
from services.backend.tests.conftest import sign_test_token  # noqa: E402


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
        cognito_test_keys["private_pem"], {"custom:tenant_id": "t-1"})})
    return c


def _selected(resource):
    import numpy as np

    from services.backend.ml.training import train_and_save

    rng = np.random.default_rng(13)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(resource, "t-1", rows, stage="production")
    MitigationStateTable(resource).put(
        tenant_id="t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        reason="behavioral_anomaly", expires_at=2_000_000_000,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99])
    now = int(datetime.now(timezone.utc).timestamp())
    TenantHistoryTable(resource).record_decision(
        "t-1", ip="10.0.0.7", tier=2, score=-0.4, z=-6.2,
        hour_start=TenantHistoryTable.hour_of(now), now=now,
        features=[40.0, 0.9, 90_000.0, 9.0, 0.99, 6.0, 0.99])


def test_every_feature_label_is_the_way_into_its_own_screen(client, dynamo_resource):
    _selected(dynamo_resource)

    assert "feature=post_ratio" in client.get("/dashboard/ui?ip=10.0.0.7").text


def test_the_feature_screen_names_the_feature(client, dynamo_resource):
    _selected(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7&feature=post_ratio").text

    assert "c-feature" in page
    assert "POST ratio" in page


def test_the_feature_screen_replaces_the_seven_rather_than_repeating_them(client, dynamo_resource):
    """Both answer "why", at two scales. Showing them together would put the
    same number on the screen twice in two different shapes."""
    _selected(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7&feature=post_ratio").text

    assert "c-why" not in page


def test_no_distribution_curve_is_drawn(client, dynamo_resource):
    """A mean and a standard deviation locate a point and say nothing about
    shape. A bell drawn from them would assert something about this tenant's
    traffic that nothing has measured."""
    _selected(dynamo_resource)

    page = client.get("/dashboard/ui?ip=10.0.0.7&feature=post_ratio").text

    assert "<path" not in page[page.index("c-feature"):]


def test_an_invented_feature_name_shows_the_seven_rather_than_erroring(client, dynamo_resource):
    """It indexes a vector. A name off the list must not reach that far, and
    a 500 on a deep link someone pasted into a ticket is the worst of the
    available failures."""
    _selected(dynamo_resource)
    response = client.get("/dashboard/ui?ip=10.0.0.7&feature=../../etc/passwd")

    assert response.status_code == 200
    assert "c-feature-track" not in response.text


def test_a_feature_without_a_selected_source_is_ignored(client, dynamo_resource):
    _selected(dynamo_resource)

    assert "c-feature-track" not in client.get("/dashboard/ui?feature=post_ratio").text


def test_the_screen_says_it_is_read_only(client, dynamo_resource):
    """The gate is measured on all seven together. An affordance implying a
    per-feature threshold would promise a second model."""
    _selected(dynamo_resource)

    assert "read only" in client.get(
        "/dashboard/ui?ip=10.0.0.7&feature=post_ratio").text.lower()


def test_a_source_with_no_track_says_so_instead_of_drawing_an_empty_chart(client, dynamo_resource):
    _selected(dynamo_resource)
    MitigationStateTable(dynamo_resource).put(
        tenant_id="t-1", ip="10.0.0.8", tier=1, score=-0.2, z=-4.4,
        reason="behavioral_anomaly", expires_at=2_000_000_000,
        features=[2.0, 0.1, 900.0, 0.2, 0.6, 1.1, 0.06])

    page = client.get("/dashboard/ui?ip=10.0.0.8&feature=post_ratio").text

    assert "c-feature-track" not in page
    assert "no hourly record" in page.lower()
