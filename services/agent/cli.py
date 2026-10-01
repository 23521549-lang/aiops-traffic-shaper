from pathlib import Path

import click

from services.agent.collector import Collector
from services.agent.config import DEFAULT_CONFIG_PATH, load_config, save_config
from services.agent.enforcer import DecisionStore, detect_adapters
from services.agent.http_client import BackendError, post_json
from services.agent.logsource import follow
from services.agent.runner import run_loop

DEFAULT_ACCESS_LOG = "/var/log/nginx/access.log"


@click.group()
def cli():
    """AI Traffic Shaper agent CLI."""


@cli.command()
@click.option("--backend-url", required=True,
              help="Base URL of the backend, e.g. https://xxx.lambda-url.ap-southeast-1.on.aws")
@click.option("--token", required=True,
              help="Cognito ID token for the tenant owner, obtained via the dashboard login. "
                   "A full CLI login flow (device-code OAuth) is a known v1 gap, not built here — "
                   "pasting a token from the dashboard is the deliberate v1 shortcut.")
@click.option("--label", default="agent", show_default=True, help="Human-readable label for this agent.")
@click.option("--access-log", default=DEFAULT_ACCESS_LOG, show_default=True,
              help="The nginx access log to read. Saved with the credentials so "
                   "'run' needs no arguments on this machine.")
@click.option("--config-path", default=None, help="Override the config file location (mainly for tests).")
def register(backend_url: str, token: str, label: str, access_log: str,
             config_path: str | None):
    """Register this agent with the backend and save its credentials locally."""
    try:
        result = post_json(
            f"{backend_url.rstrip('/')}/agent/v1/register",
            {"agent_label": label},
            # X-Id-Token, not Authorization: the backend sits behind CloudFront,
            # whose origin access control replaces the Authorization header with
            # its own SigV4 signature, so a Bearer token never arrives (ADR-005).
            headers={"X-Id-Token": token},
        )
    except BackendError as e:
        click.echo(f"Registration failed: {e}", err=True)
        raise SystemExit(1)

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    save_config({
        "backend_url": backend_url,
        "tenant_id": result["tenant_id"],
        "agent_id": result["agent_id"],
        "api_key": result["api_key"],
        "access_log": access_log,
    }, path=path)
    click.echo(f"Registered agent {result['agent_id']} for tenant {result['tenant_id']}.")
    click.echo(f"Credentials saved to {path}")
    click.echo("Next: traffic-shaper run")


@cli.command()
@click.option("--backend-url", required=True, help="Base URL of the backend.")
@click.option("--tenant-id", required=True)
@click.option("--agent-id", required=True)
@click.option("--api-key", required=True,
              help="The key the console showed once when it created this agent.")
@click.option("--access-log", default=DEFAULT_ACCESS_LOG, show_default=True,
              help="The nginx access log to read.")
@click.option("--config-path", default=None, help="Override the config file location (mainly for tests).")
def connect(backend_url: str, tenant_id: str, agent_id: str, api_key: str,
            access_log: str, config_path: str | None):
    """Save credentials this agent was issued in the console.

    The counterpart to registering from the browser. `register` mints a new
    agent using the tenant owner's ID token, which means finding that token,
    which the product never had anywhere to show. The console is already
    signed in as the tenant, so it mints the agent there and this command
    only writes down what it was handed - no token, no network call, and
    nothing that expires while the operator is still reading it.
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    save_config({
        "backend_url": backend_url,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "api_key": api_key,
        "access_log": access_log,
    }, path=path)
    click.echo(f"Credentials saved to {path}")
    click.echo("Next: traffic-shaper run")


@cli.command()
@click.option("--config-path", default=None, help="Override the config file location (mainly for tests).")
def status(config_path: str | None):
    """Show current registration status."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = load_config(path=path)
    if config is None:
        click.echo("Not registered. Run 'agent register' first.")
        raise SystemExit(1)
    click.echo(f"tenant_id: {config['tenant_id']}")
    click.echo(f"agent_id: {config['agent_id']}")


@cli.command()
@click.option("--config-path", default=None, help="Override the config file location (mainly for tests).")
@click.option("--access-log", default=None,
              help="Override the log recorded at registration. Not every install "
                   "puts it at the default path, and an agent pointed at the wrong "
                   "file watches an empty one in silence.")
@click.option("--once", is_flag=True,
              help="Read what is there, send it, and exit. For checking an install.")
@click.option("--from-start", is_flag=True,
              help="Read the existing log from the beginning. Off by default: "
                   "replaying a log that predates the agent would post months of "
                   "traffic as though it were arriving now.")
def run(config_path: str | None, access_log: str | None, once: bool,
        from_start: bool):
    """Read the access log, send metadata, enforce what comes back."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = load_config(path=path)
    if config is None:
        click.echo("Not registered. Run 'traffic-shaper register' first.", err=True)
        raise SystemExit(1)

    log_path = Path(access_log or config.get("access_log") or DEFAULT_ACCESS_LOG)
    adapters = detect_adapters()

    click.echo(f"Reading {log_path}")
    if adapters:
        click.echo("Enforcing through: " + ", ".join(a.name for a in adapters))
    else:
        # Never silent about this. An operator who is told nothing assumes
        # they are protected by a run that detected nothing and can block
        # nobody.
        click.echo("No enforcement available here: decisions will be reported "
                   "to the console but nothing will be applied locally. "
                   "Install nginx or iptables, or run with the privileges to "
                   "use them.")

    collector = Collector(config["backend_url"], config["tenant_id"],
                          config["api_key"],
                          # Reported on every batch, written by the backend
                          # only when it changes. An empty list is the answer
                          # that matters and is sent as one: the console
                          # cannot otherwise tell "protecting nothing" from
                          # "we have not been told".
                          enforcers=[a.name for a in adapters])
    stream = follow(log_path, poll_seconds=0 if once else 0.5,
                    skip_history=not from_start)
    if once:
        stream = _drain(stream)

    run_loop(stream, collector, DecisionStore(), adapters)


def _drain(stream, quiet_ticks: int = 3):
    """Everything readable right now, then stop.

    `follow` never ends on its own, which is correct for a service and
    useless for checking an install. Three consecutive quiet ticks means the
    file has nothing more to give.
    """
    quiet = 0
    for item in stream:
        if item is None:
            quiet += 1
            if quiet >= quiet_ticks:
                return
        else:
            quiet = 0
        yield item


if __name__ == "__main__":
    cli()
