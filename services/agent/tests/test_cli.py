import json

from click.testing import CliRunner

from services.agent.cli import cli


def test_register_success_saves_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"

    def fake_post_json(url, payload, headers=None):
        assert url == "http://backend/agent/v1/register"
        assert payload == {"agent_label": "prod-web-1"}
        assert headers == {"Authorization": "Bearer test-token"}
        return {"tenant_id": "t-1", "agent_id": "a-1", "api_key": "raw-key"}

    monkeypatch.setattr("services.agent.cli.post_json", fake_post_json)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "register", "--backend-url", "http://backend", "--token", "test-token",
        "--label", "prod-web-1", "--config-path", str(config_path),
    ])

    assert result.exit_code == 0, result.output
    assert "Registered agent a-1" in result.output
    saved = json.loads(config_path.read_text())
    assert saved == {"backend_url": "http://backend", "tenant_id": "t-1",
                      "agent_id": "a-1", "api_key": "raw-key"}


def test_register_backend_error_exits_nonzero(tmp_path, monkeypatch):
    from services.agent.http_client import BackendError

    def fake_post_json(url, payload, headers=None):
        raise BackendError("401: invalid token")

    monkeypatch.setattr("services.agent.cli.post_json", fake_post_json)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "register", "--backend-url", "http://backend", "--token", "bad-token",
        "--config-path", str(tmp_path / "config.json"),
    ])
    assert result.exit_code != 0
    assert "Registration failed" in result.output


def test_status_not_registered(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--config-path", str(tmp_path / "missing.json")])
    assert result.exit_code != 0
    assert "Not registered" in result.output


def test_status_registered(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"tenant_id": "t-1", "agent_id": "a-1"}))

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--config-path", str(config_path)])
    assert result.exit_code == 0
    assert "t-1" in result.output
    assert "a-1" in result.output
