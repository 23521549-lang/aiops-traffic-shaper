import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".aiops-agent" / "config.json"


def save_config(config: dict, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    try:
        path.chmod(0o600)  # config holds the raw api_key — owner-only
    except (NotImplementedError, OSError):
        pass  # best-effort; chmod semantics on some filesystems (e.g. Windows) are limited


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())
