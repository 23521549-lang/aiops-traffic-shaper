import numpy as np

from services.backend.core.tables import create_all_tables
from services.backend.ml import registry
from services.backend.ml.training import train_and_save, MIN_TRAINING_SAMPLES


def test_train_and_save_below_min_samples_returns_none(dynamo_resource):
    create_all_tables(dynamo_resource)
    vectors = np.random.rand(10, 7).tolist()  # below MIN_TRAINING_SAMPLES
    result = train_and_save(dynamo_resource, "t-1", vectors, stage="staging")
    assert result is None
    assert not registry.model_exists(dynamo_resource, "t-1", stage="staging")


def test_train_and_save_trains_n_estimators_50(dynamo_resource):
    create_all_tables(dynamo_resource)
    vectors = np.random.rand(MIN_TRAINING_SAMPLES + 20, 7).tolist()
    meta = train_and_save(dynamo_resource, "t-1", vectors, stage="staging")
    assert meta is not None
    assert meta.training_samples == MIN_TRAINING_SAMPLES + 20

    model = registry.load_model(dynamo_resource, "t-1", stage="staging")
    assert model is not None
    assert model.n_estimators == 50  # ADR-002 measured constraint, not 100


def test_train_and_save_is_per_tenant(dynamo_resource):
    create_all_tables(dynamo_resource)
    vectors = np.random.rand(MIN_TRAINING_SAMPLES + 5, 7).tolist()
    train_and_save(dynamo_resource, "t-1", vectors, stage="production")
    assert registry.model_exists(dynamo_resource, "t-1", stage="production")
    assert not registry.model_exists(dynamo_resource, "t-2", stage="production")
