import gzip
import io
import logging
from dataclasses import asdict, dataclass

import joblib
from sklearn.ensemble import IsolationForest

from services.backend.core.tables import ModelsTable

logger = logging.getLogger(__name__)


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

    def to_dict(self) -> dict:
        return asdict(self)


def save_model(resource, tenant_id: str, model: IsolationForest,
                metadata: ModelMetadata, stage: str = "staging") -> None:
    buf = io.BytesIO()
    joblib.dump(model, buf)
    blob = gzip.compress(buf.getvalue())

    table = ModelsTable(resource)
    table.put_model_blob(tenant_id, stage, blob, **metadata.to_dict())


@dataclass(frozen=True)
class ScoreStats:
    """Where this model's own training scores sat. Anomaly tiers are measured
    in standard deviations from that mean, because decision_function has no
    fixed meaning across models - see docs/adr/006-score-calibration.md."""
    mean: float
    std: float


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
        stats = ScoreStats(mean=float(item["score_mean"]), std=float(item["score_std"]))
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
