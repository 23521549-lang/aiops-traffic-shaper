from services.agent.config import load_config, save_config


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    save_config({"tenant_id": "t-1", "api_key": "secret"}, path=path)
    loaded = load_config(path=path)
    assert loaded == {"tenant_id": "t-1", "api_key": "secret"}


def test_load_missing_returns_none(tmp_path):
    assert load_config(path=tmp_path / "does-not-exist.json") is None


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "config.json"
    save_config({"a": 1}, path=path)
    assert path.exists()
