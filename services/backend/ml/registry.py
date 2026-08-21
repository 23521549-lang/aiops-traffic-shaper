import gzip
import io
from dataclasses import asdict, dataclass

import joblib
from sklearn.ensemble import IsolationForest

from services.backend.core.tables import ModelsTable


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


def load_model(resource, tenant_id: str, stage: str = "production") -> IsolationForest | None:
    table = ModelsTable(resource)
    item = table.get(tenant_id=tenant_id, stage_version=stage)
    if item is None:
        return None
    raw = gzip.decompress(bytes(item["model_blob"]))
    return joblib.load(io.BytesIO(raw))


def model_exists(resource, tenant_id: str, stage: str = "production") -> bool:
    table = ModelsTable(resource)
    return table.get(tenant_id=tenant_id, stage_version=stage) is not None
