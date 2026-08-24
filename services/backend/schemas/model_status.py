from pydantic import BaseModel, ConfigDict


class ModelStatus(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # "model_ready"/"model_config" name
    # collision with Pydantic's own reserved model_* prefix — this field name is fixed by
    # docs/api-contract.md, so silence the warning rather than rename a public API field.

    model_ready: bool
    shadow_mode: bool
    version: str | None = None
    trained_at: str | None = None
    training_samples: int | None = None
    contamination: float | None = None
    score_mean: float | None = None
    score_std: float | None = None
