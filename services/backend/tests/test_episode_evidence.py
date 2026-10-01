"""Why a source was blocked has to outlive the block.

The seven-feature vector lives only on the MitigationState row, whose TTL is
five minutes or one hour. MitigationEpisode keeps thirty days and had no
features at all, so the product's one unmatched capability - decomposing a
decision against this tenant's own baseline - could be shown only while the
block was still in force, and never at the moment a customer actually asks,
which is afterwards.

The fix is bytes on a write that already happens: ~184 B to ~284 B, still
one 1 KB write unit, still 1 WCU, zero additional operations.

`stats_version` travels with them because `z` is frozen at decision time
while the baseline columns are read from the currently loaded model. One
nightly retrain between deciding and viewing makes "your normal" and the
sigma beside it describe two different models, silently, on the one screen
that is the whole differentiator.
"""
import pytest

from services.backend.core.tables import TenantHistoryTable, create_all_tables

HOUR = 1758700800
VECTOR = [8.4, 0.71, 90.0, 0.003, 0.02, 0.9, 0.94]


@pytest.fixture
def history(dynamo_resource):
    create_all_tables(dynamo_resource)
    return TenantHistoryTable(dynamo_resource)


def test_the_feature_vector_is_stored_with_the_episode(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert [float(f) for f in episode["last_features"]] == VECTOR


def test_the_baseline_it_was_measured_against_is_named(history):
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert episode["stats_version"] == "v-2026-09-24"


def test_a_decision_with_no_features_still_records(history):
    """A tenant with no model scores nothing and has no vector. The episode
    must still exist - the decision was taken."""
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=1,
                            now=HOUR + 5, score=-0.12, z=None)

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]

    assert episode.get("last_features") in (None, [])


def test_the_episode_row_stays_under_one_write_unit(history):
    """The rule for this row: a fixed-size numeric record, capped at 1 KB, no
    variable-length text ever. `record_decision` fires once per IP per hour,
    so a 3,000-IP hour at 2 WCU would be 6,000 WCU against a 2-WCU table."""
    history.record_decision("t-1", "203.0.113.7", hour_start=HOUR, tier=2,
                            now=HOUR + 5, score=-0.31, z=-8.2,
                            features=VECTOR, stats_version="v-2026-09-24")

    episode = history.query_episodes("t-1", HOUR, HOUR + 3600)[0]
    size = sum(len(str(k)) + len(str(v)) for k, v in episode.items())

    assert size < 1024, f"episode row is {size} B; the cap is 1024"


def test_the_stats_carry_a_version_for_the_episode_to_record(dynamo_resource):
    """Without this the field is written as None forever, and the guarantee
    it exists for - that "your normal" and the sigma beside it describe the
    same model - is silently absent."""
    import numpy as np

    from services.backend.ml.registry import load_model_and_stats
    from services.backend.ml.training import train_and_save

    create_all_tables(dynamo_resource)
    rng = np.random.default_rng(11)
    rows = [[float(rng.uniform(0.8, 1.6)), float(rng.uniform(0.0, 0.06)),
             float(rng.uniform(600, 6000)), float(rng.uniform(0.02, 0.35)),
             float(rng.uniform(0.4, 0.8)), float(rng.uniform(0.6, 1.4)),
             float(rng.uniform(0.02, 0.14))] for _ in range(200)]
    train_and_save(dynamo_resource, "t-1", rows, stage="production")

    _, stats = load_model_and_stats(dynamo_resource, "t-1")

    assert stats.version
