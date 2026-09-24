"""The console explained a decision it did not let anyone change.

TIER1_Z and TIER2_Z were module constants, so the product could say "this
source is 6.4 standard deviations outside your normal" and offer no way to
say "for me, act at 4.5". That is why two competent redesigns still read as
dashboards: every verb was in a corner. Making the threshold a per-tenant
value is the structural change the rest of the interface is built on.

The value lives on Tenants, which assert_tenant_active already reads on every
authenticated agent request, and the nightly retrain copies it onto the
production Models item so classify() finds it in the stats already cached for
scoring. Zero reads, zero RCU, zero WCU on the hot path.

Storing it ONLY on Models fails: save_model rewrites that item every night
and would silently revert whatever the operator set.
"""
from services.backend.ml.model import TIER1_Z, TIER2_Z, AnomalyTier, classify
from services.backend.ml.registry import ScoreStats


def _stats(**over):
    base = dict(mean=-0.05, std=0.01)
    base.update(over)
    return ScoreStats(**base)


def test_the_default_is_the_shipped_threshold():
    """A tenant that has never set one behaves exactly as before."""
    stats = _stats()

    assert stats.tier1_z == TIER1_Z
    assert stats.tier2_z == TIER2_Z


def test_a_tenant_can_be_harder_to_trip():
    """z = -4.5 tiers as RATE_LIMIT by default and NORMAL for this tenant."""
    score = -0.05 + (-4.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.RATE_LIMIT
    assert classify(score, _stats(tier1_z=-5.0, tier2_z=-6.0)) is AnomalyTier.NORMAL


def test_a_tenant_can_be_easier_to_trip():
    score = -0.05 + (-3.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.NORMAL
    assert classify(score, _stats(tier1_z=-3.0, tier2_z=-4.0)) is AnomalyTier.RATE_LIMIT


def test_the_block_tier_uses_the_tenants_own_second_threshold():
    score = -0.05 + (-5.5 * 0.01)

    assert classify(score, _stats()) is AnomalyTier.HARD_BLOCK
    assert classify(score, _stats(tier1_z=-5.0, tier2_z=-6.0)) is AnomalyTier.RATE_LIMIT


def test_degenerate_spread_still_falls_back_to_absolute_thresholds():
    """score_std of zero means every training bucket scored identically.
    Dividing by it on the request path would be a crash, so classify falls
    back - and a per-tenant z cannot change that, because there is no z."""
    assert classify(-0.5, _stats(std=0.0, tier1_z=-3.0)) is AnomalyTier.HARD_BLOCK


def test_the_duplicated_defaults_have_not_drifted():
    """registry cannot import ml.model - ml.model imports registry - so the
    defaults are written twice. This is the only thing keeping them equal."""
    from services.backend.ml import registry

    assert registry.TIER1_Z_DEFAULT == TIER1_Z
    assert registry.TIER2_Z_DEFAULT == TIER2_Z


def test_saving_a_model_does_not_revert_the_operators_threshold(dynamo_resource):
    """The nightly retrain rewrites the production Models item. If the
    threshold lived only there, every night would silently undo the
    operator - which is why the value of record is on Tenants."""
    import numpy as np

    from services.backend.core.tables import TenantsTable, create_all_tables
    from services.backend.ml.registry import load_model_and_stats
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z",
                                      tier1_z=-4.5, tier2_z=-5.5)
    rng = np.random.default_rng(3)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]

    train_and_save(dynamo_resource, "t-1", rows, stage="production")
    _, stats = load_model_and_stats(dynamo_resource, "t-1")

    assert stats.tier1_z == -4.5
    assert stats.tier2_z == -5.5


def test_a_tenant_that_never_set_one_gets_the_default_after_a_retrain(dynamo_resource):
    """Every tenant alive today is in this case. A retrain must not write
    None onto the model and make classify fall over."""
    import numpy as np

    from services.backend.core.tables import TenantsTable, create_all_tables
    from services.backend.ml.registry import load_model_and_stats
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    TenantsTable(dynamo_resource).put(tenant_id="t-1", name="Acme",
                                      status="active",
                                      created_at="2026-08-21T00:00:00Z")
    rng = np.random.default_rng(5)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]

    train_and_save(dynamo_resource, "t-1", rows, stage="production")
    _, stats = load_model_and_stats(dynamo_resource, "t-1")

    assert stats.tier1_z == TIER1_Z
    assert stats.tier2_z == TIER2_Z
