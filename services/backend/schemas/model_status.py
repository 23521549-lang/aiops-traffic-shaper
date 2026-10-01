from pydantic import BaseModel, ConfigDict, Field


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
    # What normal looks like on each of the seven axes, for this tenant. On
    # the model item since Phase 0 and never carried off it, so the one
    # sentence this product exists to be able to say - "here is the shape of
    # your normal" - had nowhere to be said.
    feature_means: list[float] = Field(default_factory=list)
    feature_stds: list[float] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    # The tenant's own gates, copied onto the model each night. Without these
    # the model screen prints the shipped defaults at every customer who has
    # moved one.
    tier1_z: float | None = None
    tier2_z: float | None = None
