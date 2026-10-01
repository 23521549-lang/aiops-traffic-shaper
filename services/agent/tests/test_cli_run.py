"""`traffic-shaper run`, which the landing page has been advertising.

The install block on the marketing site ends with `traffic-shaper run`. The
CLI had two commands, `register` and `status`, and neither of them read a
log or sent anything. A customer who followed the quickstart to the letter
got a registered agent, a console that said the agent had never reported,
and no error anywhere to explain it.
"""
import json

from click.testing import CliRunner

from services.agent.cli import cli


def _config(tmp_path, log_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "backend_url": "https://backend.example",
        "tenant_id": "t-1",
        "agent_id": "a-1",
        "api_key": "secret",
        "access_log": str(log_path),
    }))
    return path


def test_running_without_registering_says_so_rather_than_crashing(tmp_path):
    result = CliRunner().invoke(cli, ["run", "--config-path", str(tmp_path / "none.json")])

    assert result.exit_code == 1
    assert "register" in result.output


def test_run_reads_the_log_and_posts_what_it_finds(tmp_path, monkeypatch):
    log = tmp_path / "access.log"
    log.write_text("")
    cfg = _config(tmp_path, log)

    sent = []
    monkeypatch.setattr("services.agent.collector.post_json",
                        lambda url, payload, headers=None, **kw: sent.append(payload)
                        or {"decisions": []})

    line = json.dumps({
        "time_iso8601": "2026-09-23T10:00:00+07:00", "remote_addr": "203.0.113.7",
        "request_method": "GET", "request_uri": "/", "status": "200",
        "body_bytes_sent": "10", "request_time": "0.01",
        "http_user_agent": "curl/8.4.0"}) + "\n"
    log.write_text(line)

    result = CliRunner().invoke(cli, [
        "run", "--config-path", str(cfg), "--once", "--from-start"])

    assert result.exit_code == 0, result.output
    assert sent and sent[0]["logs"][0]["remote_addr"] == "203.0.113.7"


def test_the_log_path_can_be_given_on_the_command_line(tmp_path, monkeypatch):
    """Not every install puts it at /var/log/nginx/access.log, and an agent
    that cannot be pointed at the right file is an agent that silently
    watches an empty one."""
    log = tmp_path / "elsewhere.log"
    log.write_text("")
    cfg = _config(tmp_path, tmp_path / "wrong.log")

    monkeypatch.setattr("services.agent.collector.post_json",
                        lambda url, payload, headers=None, **kw: {"decisions": []})

    result = CliRunner().invoke(cli, [
        "run", "--config-path", str(cfg), "--access-log", str(log), "--once"])

    assert result.exit_code == 0, result.output
    assert str(log) in result.output


def test_run_says_which_enforcers_it_found(tmp_path, monkeypatch):
    """Silence about enforcement is how an operator ends up believing they
    are protected by a run that detected nothing and can block nobody."""
    log = tmp_path / "access.log"
    log.write_text("")
    cfg = _config(tmp_path, log)

    monkeypatch.setattr("services.agent.cli.detect_adapters", lambda: [])
    monkeypatch.setattr("services.agent.collector.post_json",
                        lambda url, payload, headers=None, **kw: {"decisions": []})

    result = CliRunner().invoke(cli, ["run", "--config-path", str(cfg), "--once"])

    assert result.exit_code == 0, result.output
    assert "no enforcement" in result.output.lower()


def test_register_records_where_the_log_is(tmp_path, monkeypatch):
    """So `run` needs no arguments on the machine it was registered on."""
    monkeypatch.setattr("services.agent.cli.post_json",
                        lambda url, payload, headers=None, **kw: {
                            "tenant_id": "t-1", "agent_id": "a-1", "api_key": "k"})
    cfg = tmp_path / "config.json"

    result = CliRunner().invoke(cli, [
        "register", "--backend-url", "https://b.example", "--token", "t",
        "--label", "web-01", "--access-log", "/var/log/nginx/access.log",
        "--config-path", str(cfg)])

    assert result.exit_code == 0, result.output
    assert json.loads(cfg.read_text())["access_log"] == "/var/log/nginx/access.log"


def test_connect_saves_what_the_console_handed_over(tmp_path):
    """The command the console prints has to be one the CLI has. It printed
    `traffic-shaper register --tenant-id ... --api-key ...` first, and
    `register` has no such flags: the quickstart would have failed on the
    line the operator pasted."""
    cfg = tmp_path / "config.json"

    result = CliRunner().invoke(cli, [
        "connect", "--backend-url", "https://b.example", "--tenant-id", "t-1",
        "--agent-id", "a-1", "--api-key", "k-9", "--config-path", str(cfg)])

    assert result.exit_code == 0, result.output
    assert json.loads(cfg.read_text()) == {
        "backend_url": "https://b.example", "tenant_id": "t-1",
        "agent_id": "a-1", "api_key": "k-9",
        "access_log": "/var/log/nginx/access.log"}


def test_connect_makes_no_network_call(tmp_path, monkeypatch):
    """Registration already happened in the browser. A call here would need
    a credential the operator does not have."""
    def explode(*a, **kw):
        raise AssertionError("connect must not talk to the backend")

    monkeypatch.setattr("services.agent.cli.post_json", explode)
    result = CliRunner().invoke(cli, [
        "connect", "--backend-url", "https://b.example", "--tenant-id", "t-1",
        "--agent-id", "a-1", "--api-key", "k-9",
        "--config-path", str(tmp_path / "c.json")])

    assert result.exit_code == 0, result.output
