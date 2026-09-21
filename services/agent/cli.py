from pathlib import Path

import click

from services.agent.config import DEFAULT_CONFIG_PATH, load_config, save_config
from services.agent.http_client import BackendError, post_json


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
@click.option("--config-path", default=None, help="Override the config file location (mainly for tests).")
def register(backend_url: str, token: str, label: str, config_path: str | None):
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
    }, path=path)
    click.echo(f"Registered agent {result['agent_id']} for tenant {result['tenant_id']}.")
    click.echo(f"Credentials saved to {path}")


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


if __name__ == "__main__":
    cli()
