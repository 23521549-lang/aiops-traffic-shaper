"""Anomaly tiers measured in standard deviations, not in absolute score.

IsolationForest's decision_function is calibrated against the data it was
fitted to: `contamination` sets an offset at that training set's own 1%
quantile. The score therefore has no fixed meaning across models, but the
shipped thresholds (-0.1 and -0.3) were fixed constants.

Four production models, all for tenant acme-demo, all scoring the SAME
brute-force attack:

    2026-09-21 03:47  182 samples          -0.204   rate limited
    2026-09-21 05:24  187 samples          -0.105   rate limited, barely
    2026-09-21 18:00  192 samples, poisoned   n/a   no decision at all
    2026-09-23 02:19  177 clean samples    -0.092   NOT DETECTED

The last one is the point: a model trained on clean data, on the same traffic
as the first, missed the same attack by 0.008. Detection near the threshold
was a coin flip.

Measured over 20 baselines with 300 held-out normal buckets each:

    threshold      brute force   mild abuse   false positives
    raw < -0.10        14/20         2/20          0.00%     <- shipped
    raw < -0.05        20/20        17/20          0.40%
    z   < -4.0         20/20        17/20          0.27%     <- chosen
    z   < -5.0         11/20         0/20          0.00%     <- chosen for tier 2

And `raw < -0.15` caught 0/20, so the shipped tier-2 threshold of -0.3 was
not merely strict - it was unreachable. The hard-block path had never fired
and could not have.
"""
import numpy as np
import pytest

from services.backend.ml.model import (
    TIER1_Z,
    TIER2_Z,
    AnomalyTier,
    ModelManager,
    ScoreStats,
    classify,
)


def _stats(mean=0.0, std=1.0):
    return ScoreStats(mean=mean, std=std)


def test_tiers_are_measured_in_standard_deviations():
    stats = _stats(mean=0.10, std=0.05)
    assert classify(0.10, stats) == AnomalyTier.NORMAL
    assert classify(0.10 + TIER1_Z * 0.05 + 0.001, stats) == AnomalyTier.NORMAL
    assert classify(0.10 + TIER1_Z * 0.05 - 0.001, stats) == AnomalyTier.RATE_LIMIT
    assert classify(0.10 + TIER2_Z * 0.05 - 0.001, stats) == AnomalyTier.HARD_BLOCK


def test_the_attack_production_missed_is_caught():
    """The exact numbers from the 2026-09-23 production model: the attack
    scored -0.0923 against a threshold of -0.1 and went undetected. Its
    distance from that model's own normal traffic was 5.3 standard
    deviations."""
    stats = _stats(mean=0.1717, std=0.0502)
    assert classify(-0.0923, stats) != AnomalyTier.NORMAL


def test_the_same_attack_is_tiered_the_same_way_by_different_models():
    """The property the absolute thresholds did not have. Two models whose
    score scales differ by a factor of three put the same relative anomaly in
    the same tier."""
    wide = classify(-0.204, _stats(mean=0.1759, std=0.0544))
    narrow = classify(-0.0923, _stats(mean=0.1717, std=0.0502))
    assert wide == narrow != AnomalyTier.NORMAL


def test_hard_block_is_reachable():
    """decision_function on a contamination=0.01 model lives roughly in
    [-0.2, +0.25], so the shipped tier-2 threshold of -0.3 could never be
    crossed by anything. A tier that cannot fire is not a tier."""
    stats = _stats(mean=0.17, std=0.05)
    worst = -0.2  # about as low as the raw score goes in practice
    assert classify(worst, stats) == AnomalyTier.HARD_BLOCK


def test_normal_traffic_a_few_deviations_out_is_not_punished():
    """Held-out normal buckets sat between -2.7 and -4.6 standard deviations
    at their most extreme; the tier-1 line at -4.0 is deliberately past the
    bulk of that."""
    stats = _stats(mean=0.10, std=0.05)
    for z in (-1.0, -2.0, -3.0, -3.9):
        assert classify(0.10 + z * 0.05, stats) == AnomalyTier.NORMAL


@pytest.mark.parametrize("std", [0.0, -1.0])
def test_a_degenerate_spread_falls_back_to_the_absolute_thresholds(std):
    """score_std is zero only if every training bucket scored identically -
    a tenant with one repeated shape of traffic. Dividing by it would be a
    crash on the request path, so the old absolute thresholds stand in."""
    assert classify(-0.5, _stats(mean=0.0, std=std)) == AnomalyTier.HARD_BLOCK
    assert classify(-0.15, _stats(mean=0.0, std=std)) == AnomalyTier.RATE_LIMIT
    assert classify(0.05, _stats(mean=0.0, std=std)) == AnomalyTier.NORMAL


def test_no_stats_at_all_also_falls_back(dynamo_resource):
    assert classify(-0.5, None) == AnomalyTier.HARD_BLOCK
    assert classify(0.5, None) == AnomalyTier.NORMAL


# --- one read, not two -----------------------------------------------------

def test_loading_a_model_reads_the_item_once(dynamo_resource):
    """ModelManager.load called registry.model_exists() and then
    registry.load_model(), and model_exists did a full GetItem with no
    projection - so every cold start fetched the ~238KB model item TWICE,
    about 120 RCU against an account budget of 25 RCU/second. ADR-002's
    hardening pass exists because of exactly this kind of read."""
    from services.backend.core.tables import create_all_tables
    from services.backend.ml.training import train_and_save
    from services.backend.tests.test_perf_budget import _OpCounter

    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(0)
    vectors = (rng.random((150, 7)) * 0.1).tolist()
    train_and_save(dynamo_resource, "t-1", vectors, stage="production")

    ModelManager._cache.clear()
    counter = _OpCounter(dynamo_resource)
    mgr = ModelManager()
    assert mgr.load(dynamo_resource, "t-1") is True

    assert counter.counts.get("GetItem", 0) == 1, counter.counts


def test_a_loaded_model_carries_its_own_stats(dynamo_resource):
    """The stats have to travel with the model or the tier cannot be computed
    without a second read - which is what the cache exists to avoid."""
    from services.backend.core.tables import create_all_tables
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(0)
    vectors = (rng.random((150, 7)) * 0.1).tolist()
    meta = train_and_save(dynamo_resource, "t-1", vectors, stage="production")

    ModelManager._cache.clear()
    mgr = ModelManager()
    mgr.load(dynamo_resource, "t-1")

    assert mgr.stats.mean == pytest.approx(meta.score_mean)
    assert mgr.stats.std == pytest.approx(meta.score_std)


def test_the_cache_keeps_the_stats_too(dynamo_resource):
    from services.backend.core.tables import create_all_tables
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(0)
    train_and_save(dynamo_resource, "t-1", (rng.random((150, 7)) * 0.1).tolist(),
                   stage="production")

    ModelManager._cache.clear()
    ModelManager().load(dynamo_resource, "t-1")

    from services.backend.tests.test_perf_budget import _OpCounter
    counter = _OpCounter(dynamo_resource)
    warm = ModelManager()
    assert warm.load(dynamo_resource, "t-1") is True
    assert warm.stats is not None
    assert counter.counts.get("GetItem", 0) == 0, "a warm load must not read DynamoDB"
