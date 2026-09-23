"""Why this source, and not just how far out it is.

The product can already say "6.4 standard deviations outside your normal".
That is more than a rule engine can say, and it is still not an answer: the
first time a hard block fires on a real customer they will want to know what
the machine actually noticed, before they trust it.

Every competitor's best answer is a rule id. Cloudflare says "matched
managed rule 942100". CrowdSec says "this address is on the community
blocklist", which is an appeal to authority about someone else's traffic.
None of them can decompose a decision against YOUR baseline, because none of
them has one.

This one does, and the data is a `np.mean(X, axis=0)` away: the nightly
retrain already builds the array, already fits the model, and already writes
a metadata item. Fourteen more floats on a write that already happens, and
seven more on the decision, buys:

    error ratio      0.71   your normal 0.03 +/- 0.02    +6.2 sigma  <-
    request rate     8.4/s  your normal 1.2  +/- 0.9     +4.1 sigma  <-
    post ratio       0.94   your normal 0.08 +/- 0.06    +4.3 sigma  <-
    unique uri       0.02   your normal 0.61 +/- 0.18    -3.3 sigma

    Reads as: repeated failing POSTs to one path at seven times your usual
    rate. A login brute force.
"""
import numpy as np
import pytest

from services.backend.core.tables import create_all_tables
from services.backend.ml.feature_engineering import FEATURE_NAMES
from services.backend.ml.training import train_and_save
from services.backend.ui.presenters import decompose


def _normal(rng, n=200):
    """A tenant with a recognisable shape: quiet, few errors, varied URIs."""
    return [[
        float(rng.uniform(0.8, 1.6)),      # request_rate
        float(rng.uniform(0.0, 0.06)),     # error_ratio
        float(rng.uniform(600, 6000)),     # avg_bytes_sent
        float(rng.uniform(0.02, 0.35)),    # avg_request_time
        float(rng.uniform(0.4, 0.8)),      # unique_uri_ratio
        float(rng.uniform(0.6, 1.4)),      # user_agent_entropy
        float(rng.uniform(0.02, 0.14)),    # post_ratio
    ] for _ in range(n)]


@pytest.fixture
def trained(dynamo_resource):
    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(7)
    meta = train_and_save(dynamo_resource, "t-1", _normal(rng), stage="production")
    assert meta is not None
    return meta


# --- the statistics are stored -------------------------------------------

def test_the_model_records_what_normal_looks_like_per_feature(trained):
    """One numpy call each on an array already in memory at training time."""
    assert len(trained.feature_means) == len(FEATURE_NAMES)
    assert len(trained.feature_stds) == len(FEATURE_NAMES)
    # request_rate was drawn from [0.8, 1.6]
    assert 0.9 < trained.feature_means[0] < 1.5
    assert all(s >= 0 for s in trained.feature_stds)


def test_the_statistics_survive_a_round_trip(trained, dynamo_resource):
    from services.backend.ml.registry import load_model_and_stats

    _, stats = load_model_and_stats(dynamo_resource, "t-1")
    assert stats.feature_means == pytest.approx(trained.feature_means)
    assert stats.feature_stds == pytest.approx(trained.feature_stds)


# --- the decomposition ----------------------------------------------------

def test_a_brute_force_names_the_features_that_drove_it(trained):
    """The vector is the real shape of a login brute force: a high request
    rate, almost all errors, almost all POSTs, one URI."""
    attack = [8.4, 0.71, 90.0, 0.003, 0.02, 0.9, 0.94]

    rows = decompose(attack, trained.feature_means, trained.feature_stds)
    by_name = {r["name"]: r for r in rows}

    assert by_name["error_ratio"]["drives"] is True
    assert by_name["post_ratio"]["drives"] is True
    assert by_name["request_rate"]["drives"] is True
    # Fewer distinct URIs than normal: a real signal, in the other direction.
    assert by_name["unique_uri_ratio"]["sigma"] < 0


def test_the_strongest_signal_comes_first(trained):
    """An operator reads the top of the list and stops. Ordering by absolute
    distance is what makes that the right thing to do."""
    attack = [8.4, 0.71, 90.0, 0.003, 0.02, 0.9, 0.94]
    rows = decompose(attack, trained.feature_means, trained.feature_stds)
    magnitudes = [abs(r["sigma"]) for r in rows]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_ordinary_traffic_drives_nothing(trained):
    """Marking every row as a driver would make the marker meaningless."""
    ordinary = [1.2, 0.03, 3000.0, 0.15, 0.6, 1.0, 0.08]
    rows = decompose(ordinary, trained.feature_means, trained.feature_stds)
    assert not any(r["drives"] for r in rows)


def test_a_feature_with_no_spread_is_not_infinite(trained):
    """A tenant whose traffic never varies on one axis gives that feature a
    standard deviation of zero. Dividing by it would be a crash on a page
    someone opened during an incident."""
    means = list(trained.feature_means)
    stds = list(trained.feature_stds)
    stds[1] = 0.0

    rows = decompose([1.2, 0.9, 3000.0, 0.15, 0.6, 1.0, 0.08], means, stds)
    flat = next(r for r in rows if r["name"] == "error_ratio")
    assert flat["sigma"] is None
    assert flat["display"] == "not measurable"


def test_missing_statistics_produce_nothing_rather_than_guesses(trained):
    """Models trained before this field existed have no per-feature figures.
    An empty list lets the pane say so; a fabricated one would invent a
    reason for a real enforcement decision."""
    assert decompose([1.0] * 7, [], []) == []
    assert decompose([1.0] * 7, None, None) == []


def test_every_feature_is_named_in_words(trained):
    """`unique_uri_ratio` is a column in a dataframe. "Distinct URLs" is
    something a person reading an incident can act on."""
    rows = decompose([1.2, 0.03, 3000.0, 0.15, 0.6, 1.0, 0.08],
                     trained.feature_means, trained.feature_stds)
    for row in rows:
        assert row["label"] != row["name"]
        assert "_" not in row["label"]


def test_a_short_vector_is_refused_rather_than_misaligned(trained):
    """Six values against seven statistics would silently compare the wrong
    feature to the wrong baseline, and the result would look plausible."""
    assert decompose([1.0] * 6, trained.feature_means, trained.feature_stds) == []
