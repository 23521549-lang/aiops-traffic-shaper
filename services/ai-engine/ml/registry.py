import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.ensemble import IsolationForest

from services.ai_engine.core.config import settings

logger = logging.getLogger(__name__)

MODEL_BASE_PATH = Path("/app/models")
PRODUCTION_PATH = MODEL_BASE_PATH / "production"
STAGING_PATH    = MODEL_BASE_PATH / "staging"
ARCHIVE_PATH    = MODEL_BASE_PATH / "archive"
MODEL_FILENAME  = "isolation_forest.joblib"
METADATA_FILE   = "metadata.json"


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


def _ensure_dirs() -> None:
    for path in [PRODUCTION_PATH, STAGING_PATH, ARCHIVE_PATH]:
        path.mkdir(parents=True, exist_ok=True)


def save_model(
    model: IsolationForest,
    metadata: ModelMetadata,
    stage: str = "staging",
) -> None:
    _ensure_dirs()
    target = PRODUCTION_PATH if stage == "production" else STAGING_PATH

    model_path    = target / MODEL_FILENAME
    metadata_path = target / METADATA_FILE

    joblib.dump(model, model_path)
    with open(metadata_path, "w") as f:
        json.dump(metadata.to_dict(), f, indent=2)

    logger.info(
        "Model saved: stage=%s version=%s samples=%d",
        stage,
        metadata.version,
        metadata.training_samples,
    )


def load_model(stage: str = "production") -> IsolationForest | None:
    _ensure_dirs()
    target     = PRODUCTION_PATH if stage == "production" else STAGING_PATH
    model_path = target / MODEL_FILENAME

    if not model_path.exists():
        logger.info("No model found at %s", model_path)
        return None

    try:
        model = joblib.load(model_path)
        logger.info("Model loaded from %s", model_path)
        return model
    except Exception as e:
        logger.error("Failed to load model from %s: %s", model_path, e)
        return None


def load_metadata(stage: str = "production") -> ModelMetadata | None:
    _ensure_dirs()
    target        = PRODUCTION_PATH if stage == "production" else STAGING_PATH
    metadata_path = target / METADATA_FILE

    if not metadata_path.exists():
        return None

    try:
        with open(metadata_path) as f:
            data = json.load(f)
        return ModelMetadata(**data)
    except Exception as e:
        logger.error("Failed to load metadata from %s: %s", metadata_path, e)
        return None


def promote_staging_to_production() -> bool:
    _ensure_dirs()
    staging_model    = STAGING_PATH / MODEL_FILENAME
    staging_metadata = STAGING_PATH / METADATA_FILE

    if not staging_model.exists():
        logger.error("No staging model to promote")
        return False

    prod_model    = PRODUCTION_PATH / MODEL_FILENAME
    prod_metadata = PRODUCTION_PATH / METADATA_FILE

    if prod_model.exists():
        meta = load_metadata("production")
        version = meta.version if meta else "unknown"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        archive_name = f"{version}_{ts}"
        archive_dir  = ARCHIVE_PATH / archive_name
        archive_dir.mkdir(parents=True, exist_ok=True)
        prod_model.rename(archive_dir / MODEL_FILENAME)
        if prod_metadata.exists():
            prod_metadata.rename(archive_dir / METADATA_FILE)
        logger.info("Previous production model archived: %s", archive_name)

    import shutil
    shutil.copy2(staging_model, prod_model)
    if staging_metadata.exists():
        shutil.copy2(staging_metadata, prod_metadata)

    logger.info("Staging model promoted to production")
    return True


def model_exists(stage: str = "production") -> bool:
    target = PRODUCTION_PATH if stage == "production" else STAGING_PATH
    return (target / MODEL_FILENAME).exists()
