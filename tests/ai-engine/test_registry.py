import pytest
from sklearn.ensemble import IsolationForest

from services.ai_engine.ml import registry


@pytest.fixture(autouse=True)
def isolated_model_paths(tmp_path, monkeypatch):
    base = tmp_path / "models"
    monkeypatch.setattr(registry, "MODEL_BASE_PATH", base)
    monkeypatch.setattr(registry, "PRODUCTION_PATH", base / "production")
    monkeypatch.setattr(registry, "STAGING_PATH", base / "staging")
    monkeypatch.setattr(registry, "ARCHIVE_PATH", base / "archive")
    return base


def _make_metadata(version: str = "v1", stage: str = "staging") -> registry.ModelMetadata:
    return registry.ModelMetadata(
        version=version,
        trained_at="2026-01-01T00:00:00+00:00",
        training_samples=100,
        contamination=0.01,
        score_mean=-0.01,
        score_std=0.05,
        features=["request_rate"],
        stage=stage,
    )


def _tiny_model() -> IsolationForest:
    model = IsolationForest(n_estimators=5, random_state=42)
    model.fit([[0.0], [1.0], [2.0], [-1.0]])
    return model


class TestSaveAndLoadModel:
    def test_round_trips_staging_model(self):
        model = _tiny_model()
        registry.save_model(model, _make_metadata(), stage="staging")

        loaded = registry.load_model("staging")

        assert loaded is not None
        assert isinstance(loaded, IsolationForest)

    def test_round_trips_production_model(self):
        model = _tiny_model()
        registry.save_model(model, _make_metadata(stage="production"), stage="production")

        assert registry.load_model("production") is not None

    def test_load_model_returns_none_when_missing(self):
        assert registry.load_model("production") is None


class TestMetadata:
    def test_round_trips_metadata(self):
        model = _tiny_model()
        meta = _make_metadata(version="v20260101120000")
        registry.save_model(model, meta, stage="staging")

        loaded = registry.load_metadata("staging")

        assert loaded is not None
        assert loaded.version == "v20260101120000"
        assert loaded.training_samples == 100

    def test_load_metadata_returns_none_when_missing(self):
        assert registry.load_metadata("staging") is None


class TestModelExists:
    def test_false_when_no_model_saved(self):
        assert registry.model_exists("staging") is False

    def test_true_after_saving(self):
        registry.save_model(_tiny_model(), _make_metadata(), stage="staging")
        assert registry.model_exists("staging") is True


class TestPromoteStagingToProduction:
    def test_returns_false_when_no_staging_model(self):
        assert registry.promote_staging_to_production() is False

    def test_promotes_when_no_existing_production(self):
        registry.save_model(_tiny_model(), _make_metadata(version="v1"), stage="staging")

        result = registry.promote_staging_to_production()

        assert result is True
        assert registry.model_exists("production") is True
        meta = registry.load_metadata("production")
        assert meta.version == "v1"

    def test_archives_previous_production_before_promoting(self):
        registry.save_model(
            _tiny_model(), _make_metadata(version="v_old", stage="production"), stage="production"
        )
        registry.save_model(
            _tiny_model(), _make_metadata(version="v_new", stage="staging"), stage="staging"
        )

        result = registry.promote_staging_to_production()

        assert result is True
        meta = registry.load_metadata("production")
        assert meta.version == "v_new"

        archived = list(registry.ARCHIVE_PATH.iterdir())
        assert len(archived) == 1
        assert archived[0].name.startswith("v_old_")
        assert (archived[0] / registry.MODEL_FILENAME).exists()
