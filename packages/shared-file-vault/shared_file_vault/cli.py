"""Click CLI for the Small Sea Shared File Vault."""

import click

from shared_file_vault import sync, vault


def _config() -> dict:
    return sync.load_config()


def _resolve_common(vault_root=None, participant=None):
    cfg = _config()
    return (
        vault_root or cfg.get("vault_root"),
        participant or cfg.get("participant_hex"),
    )


def _resolve_sync(vault_root=None, participant=None, hub_port=None):
    cfg = _config()
    return (
        vault_root or cfg.get("vault_root"),
        participant or cfg.get("participant_hex"),
        hub_port if hub_port is not None else cfg.get("hub_port", 11437),
    )


def _die(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    raise SystemExit(1)


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
@click.option("--open/--no-open", "open_browser", default=True, help="Open browser on start")
def serve_cmd(vault_root, participant, host, port, open_browser):
    """Start the web UI."""
    import threading
    import webbrowser

    import uvicorn
    from shared_file_vault.web import create_app

    vault_root, participant = _resolve_common(vault_root, participant)

    if not vault_root or not participant:
        _die(
            "vault_root and participant_hex are required.\n"
            f"Set them in {sync.config_path()} or pass --vault-root / --participant."
        )

    hub_port = _config().get("hub_port", 11437)
    app = create_app(vault_root, participant, hub_port=hub_port)
    url = f"http://{host}:{port}"

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    click.echo(f"Vault UI -> {url}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# login + sync
# ---------------------------------------------------------------------------


@cli.command("login")
@click.argument("team_name")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def login_cmd(team_name, participant, hub_port):
    """Open and cache a Hub session for a team."""
    _vault_root, participant, hub_port = _resolve_sync(None, participant, hub_port)
    try:
        participant = sync.require_value(participant, "participant_hex")
        result = sync.login_team(
            team_name,
            participant,
            hub_port=hub_port,
            pin_reader=lambda _: click.prompt("PIN", prompt_suffix=": "),
        )
    except sync.VaultSyncError as exc:
        _die(str(exc))

    mode = "auto-approved" if result.auto_approved else "confirmed with PIN"
    click.echo(f"Logged into {team_name} ({mode}).")


@cli.command("push")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--vault-root", default=None, help="Override vault root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def push_cmd(team_name, niche_name, vault_root, participant, hub_port):
    """Push a niche and its registry through the Hub."""
    vault_root, participant, hub_port = _resolve_sync(vault_root, participant, hub_port)
    try:
        vault_root = sync.require_value(vault_root, "vault_root")
        participant = sync.require_value(participant, "participant_hex")
        sync.push_via_hub(
            vault_root,
            participant,
            team_name,
            niche_name,
            hub_port=hub_port,
        )
    except (sync.VaultSyncError, OSError) as exc:
        _die(str(exc))

    click.echo(f"Pushed niche '{niche_name}' for team '{team_name}'.")


@cli.command("pull")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--from-member", "from_member", required=True, help="Peer member ID hex")
@click.option("--vault-root", default=None, help="Override vault root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def pull_cmd(team_name, niche_name, from_member, vault_root, participant, hub_port):
    """Pull a niche and its registry from a peer through the Hub."""
    vault_root, participant, hub_port = _resolve_sync(vault_root, participant, hub_port)
    try:
        vault_root = sync.require_value(vault_root, "vault_root")
        participant = sync.require_value(participant, "participant_hex")
        sync.pull_via_hub(
            vault_root,
            participant,
            team_name,
            niche_name,
            from_member,
            hub_port=hub_port,
        )
    except sync.PullConflictError as exc:
        click.echo(f"Pull left unresolved conflicts in the {exc.scope}.", err=True)
        if exc.paths:
            click.echo("Conflicting files:", err=True)
            for path in exc.paths:
                click.echo(f"  {path}", err=True)
        raise SystemExit(1)
    except (sync.VaultSyncError, OSError) as exc:
        _die(str(exc))

    click.echo(
        f"Pulled niche '{niche_name}' for team '{team_name}' from member {from_member}."
    )


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
    for niche in niches:
        checkouts = vault.list_checkouts(vault_root, participant_hex, team_name, niche["name"])
        co_str = ", ".join(checkouts) if checkouts else "(no checkouts)"
        click.echo(f"  {niche['name']}  {co_str}  [{niche['id'][:8]}]")


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
    for entry in entries:
        click.echo(f"  {entry['status']}  {entry['path']}")


@cli.command("publish")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("checkout_path")
@click.option("-m", "--message", default=None, help="Commit message")
@click.argument("files", nargs=-1)
def publish_cmd(vault_root, participant_hex, team_name, niche_name, checkout_path, message, files):
    """Publish changes from a checkout (stage + commit)."""
    commit_hash = vault.publish(
        vault_root,
        participant_hex,
        team_name,
        niche_name,
        checkout_path,
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
    for entry in entries:
        click.echo(f"  {entry['hash']}  {entry['message']}")
