import gzip
import io
import logging
from dataclasses import asdict, dataclass, field

import joblib
from sklearn.ensemble import IsolationForest

from services.backend.core.tables import ModelsTable

logger = logging.getLogger(__name__)

# Duplicated from ml.model rather than imported: ml.model imports THIS
# module, so the dependency only runs one way. test_per_tenant_thresholds.py
# asserts the two copies have not drifted.
TIER1_Z_DEFAULT = -4.0
TIER2_Z_DEFAULT = -5.0


@dataclass
class ModelMetadata:
    version: str
    trained_at: str
    training_samples: int
    contamination: float
    score_mean: float
    score_std: float
    features: list[str]
    stage: str
    # What normal looks like on each of the seven axes, for THIS tenant.
    # One numpy call each on the array the trainer already built, written
    # into an item already being written, read in a GetItem already being
    # made - and it is the difference between "6.4 standard deviations out"
    # and "repeated failing POSTs to one path at seven times your usual
    # rate". Empty on models trained before this field existed.
    feature_means: list[float] = field(default_factory=list)
    feature_stds: list[float] = field(default_factory=list)
    # Copied from Tenants each night so classify() finds the tenant's gate in
    # the stats it already has cached. The value of record is on Tenants: a
    # model-only home would be silently reverted by the very retrain that
    # writes this item.
    tier1_z: float = TIER1_Z_DEFAULT
    tier2_z: float = TIER2_Z_DEFAULT
    # How many measured buckets the tenant's allowed list kept out of this
    # baseline. None on models trained before the count existed, and on that
    # item the console says "not measured yet" - which is not the same claim
    # as zero, and merging them would be principle 1.4 with a number on it.
    excluded_whitelist_buckets: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def save_model(resource, tenant_id: str, model: IsolationForest,
                metadata: ModelMetadata, stage: str = "staging") -> None:
    buf = io.BytesIO()
    joblib.dump(model, buf)
    blob = gzip.compress(buf.getvalue())

    table = ModelsTable(resource)
    # None means "not measured", and an absent attribute says that better
    # than a stored NULL: a projection that finds nothing and a projection
    # that finds NULL both have to be read as the same thing downstream, so
    # only one of them should ever be written.
    fields = {k: v for k, v in metadata.to_dict().items() if v is not None}
    table.put_model_blob(tenant_id, stage, blob, **fields)


@dataclass(frozen=True)
class ScoreStats:
    """Where this model's own training scores sat. Anomaly tiers are measured
    in standard deviations from that mean, because decision_function has no
    fixed meaning across models - see docs/adr/006-score-calibration.md."""
    mean: float
    std: float
    feature_means: list[float] = field(default_factory=list)
    feature_stds: list[float] = field(default_factory=list)
    # Which saved model these figures came from. Written onto every episode
    # beside the feature vector, because `z` is frozen at decision time while
    # the baseline is read live - without this the two halves of the
    # explanation can describe different models and nothing would say so.
    version: str | None = None
    # The tenant's own gate, defaulting to the shipped one. It rides with the
    # stats because those are already in ModelManager._cache when classify()
    # runs, so a per-tenant threshold costs nothing on the scoring path.
    tier1_z: float = TIER1_Z_DEFAULT
    tier2_z: float = TIER2_Z_DEFAULT


def load_model_and_stats(resource, tenant_id: str, stage: str = "production",
                          ) -> tuple[IsolationForest | None, ScoreStats | None]:
    """ONE GetItem for both. The model item is ~238KB, roughly 60 RCU against
    an account budget of 25 RCU/second, and the caller needs the stats to tier
    a score - fetching them separately would double the most expensive read in
    the system."""
    item = ModelsTable(resource).get(tenant_id=tenant_id, stage_version=stage)
    if item is None:
        return None, None
    stats = None
    if "score_mean" in item and "score_std" in item:
        stats = ScoreStats(
            mean=float(item["score_mean"]), std=float(item["score_std"]),
            # Absent on models trained before per-feature statistics existed.
            # An empty list is what lets the detail pane say so instead of
            # inventing a reason for a real enforcement decision.
            feature_means=[float(v) for v in item.get("feature_means", [])],
            feature_stds=[float(v) for v in item.get("feature_stds", [])],
            # Carried so every decision can name the baseline it was measured
            # against. The item has always had it; it simply never travelled.
            version=item.get("version"),
            tier1_z=float(item["tier1_z"]) if "tier1_z" in item else TIER1_Z_DEFAULT,
            tier2_z=float(item["tier2_z"]) if "tier2_z" in item else TIER2_Z_DEFAULT,
        )
    return _deserialize(item, tenant_id, stage), stats


def load_model(resource, tenant_id: str, stage: str = "production") -> IsolationForest | None:
    return load_model_and_stats(resource, tenant_id, stage)[0]


def _deserialize(item: dict, tenant_id: str, stage: str) -> IsolationForest | None:
    try:
        raw = gzip.decompress(bytes(item["model_blob"]))
        return joblib.load(io.BytesIO(raw))
    except Exception as e:
        # A corrupted blob or a joblib/sklearn version mismatch between the
        # training Lambda and the serving Lambda must fall back to shadow
        # mode (return None), not crash the whole telemetry request with an
        # unhandled 500 — restores the behavior of the superseded ai_engine/ml/registry.py
        # (removed in Phase 6; see git history and ADR-002)
        # had and this port initially dropped.
        logger.error("Failed to deserialize model: tenant=%s stage=%s: %s",
                     tenant_id, stage, e)
        return None


def model_exists(resource, tenant_id: str, stage: str = "production") -> bool:
    """Projected: asks for the key only, never the 238KB blob. An unprojected
    get_item here meant a cold start read the model item twice."""
    resp = ModelsTable(resource)._table.get_item(
        Key={"tenant_id": tenant_id, "stage_version": stage},
        ProjectionExpression="stage_version")
    return resp.get("Item") is not None
