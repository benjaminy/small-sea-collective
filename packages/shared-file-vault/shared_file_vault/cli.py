"""Click CLI for the Small Sea Shared File Vault."""

import pathlib
import sys
import tomllib

import click

from shared_file_vault import vault

_CONFIG_PATH = pathlib.Path.home() / ".config" / "small-sea" / "vault.toml"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


@click.group()
def cli():
    """Small Sea Shared File Vault"""


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command("serve")
@click.option("--vault-root", default=None, help="Override vault root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True,
              help="Open browser on start")
def serve_cmd(vault_root, participant, host, port, open_browser):
    """Start the web UI."""
    import uvicorn
    from shared_file_vault.web import create_app

    cfg = _load_config()
    vault_root = vault_root or cfg.get("vault_root")
    participant = participant or cfg.get("participant_hex")

    if not vault_root or not participant:
        click.echo(
            "Error: vault_root and participant_hex are required.\n"
            f"Set them in {_CONFIG_PATH} or pass --vault-root / --participant.",
            err=True,
        )
        sys.exit(1)

    app = create_app(vault_root, participant)
    url = f"http://{host}:{port}"

    if open_browser:
        import threading, webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    click.echo(f"Vault UI → {url}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# vault operations
# ---------------------------------------------------------------------------

@cli.command("init")
@click.argument("vault_root")
@click.argument("participant_hex")
def init_cmd(vault_root, participant_hex):
    """Initialize a vault for a participant."""
    vault.init_vault(vault_root, participant_hex)
    click.echo(f"Vault initialized at {vault_root}")


@cli.command("create")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
def create_cmd(vault_root, participant_hex, team_name, niche_name):
    """Create a new niche."""
    niche_id = vault.create_niche(vault_root, participant_hex, team_name, niche_name)
    click.echo(f"Created niche '{niche_name}' ({niche_id})")


@cli.command("checkout")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("dest_path")
def checkout_cmd(vault_root, participant_hex, team_name, niche_name, dest_path):
    """Add a checkout of a niche at a filesystem path."""
    vault.add_checkout(vault_root, participant_hex, team_name, niche_name, dest_path)
    click.echo(f"Checkout added at {dest_path}")


@cli.command("list")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
def list_cmd(vault_root, participant_hex, team_name):
    """List all niches for a team."""
    niches = vault.list_niches(vault_root, participant_hex, team_name)
    if not niches:
        click.echo("No niches.")
        return
    for n in niches:
        checkouts = vault.list_checkouts(vault_root, participant_hex, team_name, n["name"])
        co_str = ", ".join(checkouts) if checkouts else "(no checkouts)"
        click.echo(f"  {n['name']}  {co_str}  [{n['id'][:8]}]")


@cli.command("status")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("checkout_path")
def status_cmd(vault_root, participant_hex, team_name, niche_name, checkout_path):
    """Show working tree status for a niche checkout."""
    entries = vault.status(vault_root, participant_hex, team_name, niche_name, checkout_path)
    if not entries:
        click.echo("Clean.")
        return
    for e in entries:
        click.echo(f"  {e['status']}  {e['path']}")


@cli.command("publish")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("checkout_path")
@click.option("-m", "--message", default=None, help="Commit message")
@click.argument("files", nargs=-1)
def publish_cmd(vault_root, participant_hex, team_name, niche_name, checkout_path,
                message, files):
    """Publish changes from a checkout (stage + commit)."""
    commit_hash = vault.publish(
        vault_root, participant_hex, team_name, niche_name, checkout_path,
        files=list(files) if files else None,
        message=message,
    )
    click.echo(f"Published: {commit_hash[:8]}")


@cli.command("log")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
def log_cmd(vault_root, participant_hex, team_name, niche_name):
    """Show commit log for a niche."""
    entries = vault.log(vault_root, participant_hex, team_name, niche_name)
    if not entries:
        click.echo("No commits.")
        return
    for e in entries:
        click.echo(f"  {e['hash']}  {e['message']}")
