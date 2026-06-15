"""Click CLI for Small Sea Files."""

import click

from ssc_files import files as files_core
from ssc_files import sync


def _config() -> dict:
    return sync.load_config()


def _resolve_common(files_root=None, participant=None):
    cfg = _config()
    return (
        files_root or cfg.get("files_root"),
        participant or cfg.get("participant_hex"),
    )


def _resolve_sync(files_root=None, participant=None, hub_port=None):
    cfg = _config()
    return (
        files_root or cfg.get("files_root"),
        participant or cfg.get("participant_hex"),
        hub_port if hub_port is not None else cfg.get("hub_port", 11437),
    )


def _die(message: str) -> None:
    click.echo(f"Error: {message}", err=True)
    raise SystemExit(1)


def _team_context(files_root: str, participant_hex: str, team_name: str):
    try:
        return sync.resolve_team_context(files_root, participant_hex, team_name)
    except sync.FilesSyncError as exc:
        _die(str(exc))


@click.group()
def cli():
    """Small Sea Files"""


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command("serve")
@click.option("--files-root", default=None, help="Override files root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True, help="Open browser on start")
def serve_cmd(files_root, participant, host, port, open_browser):
    """Start the web UI."""
    import threading
    import webbrowser

    import uvicorn
    from ssc_files.web import create_app

    files_root, participant = _resolve_common(files_root, participant)

    if not files_root or not participant:
        _die(
            "files_root and participant_hex are required.\n"
            f"Set them in {sync.config_path()} or pass --files-root / --participant."
        )

    hub_port = _config().get("hub_port", 11437)
    app = create_app(files_root, participant, hub_port=hub_port)
    url = f"http://{host}:{port}"

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    click.echo(f"Files UI -> {url}")
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
    files_root, participant, hub_port = _resolve_sync(None, participant, hub_port)
    try:
        files_root = sync.require_value(files_root, "files_root")
        participant = sync.require_value(participant, "participant_hex")
        result = sync.login_team(
            files_root,
            team_name,
            participant,
            hub_port=hub_port,
            pin_reader=lambda _: click.prompt("PIN", prompt_suffix=": "),
        )
    except sync.FilesSyncError as exc:
        _die(str(exc))

    mode = "auto-approved" if result.auto_approved else "confirmed with PIN"
    click.echo(f"Logged into {team_name} ({mode}).")


@cli.command("push")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--files-root", default=None, help="Override files root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def push_cmd(team_name, niche_name, files_root, participant, hub_port):
    """Push a niche and its registry through the Hub."""
    files_root, participant, hub_port = _resolve_sync(files_root, participant, hub_port)
    try:
        files_root = sync.require_value(files_root, "files_root")
        participant = sync.require_value(participant, "participant_hex")
        sync.push_via_hub(
            files_root,
            participant,
            team_name,
            niche_name,
            hub_port=hub_port,
        )
    except (sync.FilesSyncError, OSError) as exc:
        _die(str(exc))

    click.echo(f"Pushed niche '{niche_name}' for team '{team_name}'.")


@cli.command("fetch")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--from-teammate", "from_teammate", required=True, help="Peer teammate ID hex")
@click.option("--files-root", default=None, help="Override files root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def fetch_cmd(team_name, niche_name, from_teammate, files_root, participant, hub_port):
    """Fetch updates from a peer without merging.

    Parks fetched content locally. No checkout is required. Use this as the
    first step of the join flow:

      1. fetch --from-teammate PEER_ID   (no checkout needed)
      2. checkout ... PATH             (attach a local directory)
      3. merge --from-teammate PEER_ID   (integrate fetched content)
    """
    files_root, participant, hub_port = _resolve_sync(files_root, participant, hub_port)
    try:
        files_root = sync.require_value(files_root, "files_root")
        participant = sync.require_value(participant, "participant_hex")
        result = sync.fetch_via_hub(
            files_root,
            participant,
            team_name,
            niche_name,
            from_teammate,
            hub_port=hub_port,
        )
    except (sync.FilesSyncError, OSError) as exc:
        _die(str(exc))

    if result.niche_sha:
        click.echo(f"Fetched niche updates from {from_teammate} ({result.niche_sha[:8]}). Ready to merge.")
    else:
        click.echo(f"No new niche updates from {from_teammate}.")


@cli.command("merge")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--from-teammate", "from_teammate", required=True, help="Peer teammate ID hex")
@click.option("--files-root", default=None, help="Override files root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def merge_cmd(team_name, niche_name, from_teammate, files_root, participant, hub_port):
    """Merge previously fetched peer updates into the attached checkout.

    Requires a clean checkout. If the niche has no checkout yet, run
    'checkout' first to attach one.
    """
    files_root, participant, hub_port = _resolve_sync(files_root, participant, hub_port)
    try:
        files_root = sync.require_value(files_root, "files_root")
        participant = sync.require_value(participant, "participant_hex")
        sync.merge_via_hub(
            files_root,
            participant,
            team_name,
            niche_name,
            from_teammate,
            hub_port=hub_port,
        )
    except sync.DirtyCheckoutError as exc:
        click.echo("Merge blocked: checkout has uncommitted changes.", err=True)
        if exc.paths:
            click.echo("Clean up these files before merging:", err=True)
            for path in exc.paths:
                click.echo(f"  {path}", err=True)
        raise SystemExit(1)
    except sync.StaleCheckoutError as exc:
        _die(str(exc))
    except sync.NoCheckoutError as exc:
        _die(str(exc))
    except sync.PullConflictError as exc:
        click.echo(f"Merge left unresolved conflicts in the {exc.scope}.", err=True)
        if exc.paths:
            click.echo("Conflicting files:", err=True)
            for path in exc.paths:
                click.echo(f"  {path}", err=True)
        raise SystemExit(1)
    except (sync.FilesSyncError, OSError) as exc:
        _die(str(exc))

    click.echo(f"Merged updates from {from_teammate} into '{niche_name}'.")


@cli.command("pull")
@click.argument("team_name")
@click.argument("niche_name")
@click.option("--from-teammate", "from_teammate", required=True, help="Peer teammate ID hex")
@click.option("--files-root", default=None, help="Override files root from config")
@click.option("--participant", default=None, help="Override participant hex from config")
@click.option("--hub-port", type=int, default=None, help="Override Hub port from config")
def pull_cmd(team_name, niche_name, from_teammate, files_root, participant, hub_port):
    """Convenience wrapper for fetch + merge (requires an attached checkout).

    For initial join (no checkout yet), use fetch → checkout → merge instead.
    """
    files_root, participant, hub_port = _resolve_sync(files_root, participant, hub_port)
    try:
        files_root = sync.require_value(files_root, "files_root")
        participant = sync.require_value(participant, "participant_hex")
        sync.pull_via_hub(
            files_root,
            participant,
            team_name,
            niche_name,
            from_teammate,
            hub_port=hub_port,
        )
    except sync.DirtyCheckoutError as exc:
        click.echo("Pull blocked: checkout has uncommitted changes.", err=True)
        if exc.paths:
            click.echo("Clean up these files before pulling:", err=True)
            for path in exc.paths:
                click.echo(f"  {path}", err=True)
        raise SystemExit(1)
    except sync.StaleCheckoutError as exc:
        _die(str(exc))
    except sync.NoCheckoutError as exc:
        _die(str(exc))
    except sync.PullConflictError as exc:
        click.echo(f"Pull left unresolved conflicts in the {exc.scope}.", err=True)
        if exc.paths:
            click.echo("Conflicting files:", err=True)
            for path in exc.paths:
                click.echo(f"  {path}", err=True)
        raise SystemExit(1)
    except (sync.FilesSyncError, OSError) as exc:
        _die(str(exc))

    click.echo(
        f"Pulled niche '{niche_name}' for team '{team_name}' from teammate {from_teammate}."
    )


# ---------------------------------------------------------------------------
# files operations
# ---------------------------------------------------------------------------


@cli.command("init")
@click.argument("files_root")
@click.argument("participant_hex")
def init_cmd(files_root, participant_hex):
    """Initialize Files storage for a participant."""
    files_core.init_files(files_root, participant_hex)
    click.echo(f"Files initialized at {files_root}")


@cli.command("create")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
def create_cmd(files_root, participant_hex, team_name, niche_name):
    """Create a new niche."""
    context = _team_context(files_root, participant_hex, team_name)
    niche_id = files_core.create_niche(files_root, participant_hex, context, niche_name)
    click.echo(f"Created niche '{niche_name}' ({niche_id})")


@cli.command("checkout")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("dest_path")
def checkout_cmd(files_root, participant_hex, team_name, niche_name, dest_path):
    """Attach a checkout of a niche at a filesystem path.

    Each niche may have at most one checkout. Remove the existing checkout
    before attaching a new location.
    """
    context = _team_context(files_root, participant_hex, team_name)
    try:
        files_core.add_checkout(files_root, participant_hex, context, niche_name, dest_path)
    except files_core.DuplicateCheckoutError as exc:
        _die(str(exc))
    click.echo(f"Checkout attached at {dest_path}")


@cli.command("list")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
def list_cmd(files_root, participant_hex, team_name):
    """List all niches for a team."""
    context = _team_context(files_root, participant_hex, team_name)
    niches = files_core.list_niches(files_root, participant_hex, context)
    if not niches:
        click.echo("No niches.")
        return
    for niche in niches:
        residency = niche.get("residency", "")
        checkout = files_core.get_checkout(files_root, participant_hex, context, niche["name"])
        if checkout:
            state_str = f"{checkout}  ({residency})"
        else:
            state_str = f"({residency})"
        click.echo(f"  {niche['name']}  {state_str}  [{niche['id'][:8]}]")


@cli.command("status")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("checkout_path")
def status_cmd(files_root, participant_hex, team_name, niche_name, checkout_path):
    """Show working tree status for a niche checkout."""
    context = _team_context(files_root, participant_hex, team_name)
    entries = files_core.status(files_root, participant_hex, context, niche_name, checkout_path)
    if not entries:
        click.echo("Clean.")
        return
    for entry in entries:
        click.echo(f"  {entry['status']}  {entry['path']}")


@cli.command("publish")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
@click.argument("checkout_path")
@click.option("-m", "--message", default=None, help="Commit message")
@click.argument("files", nargs=-1)
def publish_cmd(files_root, participant_hex, team_name, niche_name, checkout_path, message, files):
    """Publish changes from a checkout (stage + commit)."""
    context = _team_context(files_root, participant_hex, team_name)
    commit_hash = files_core.publish(
        files_root,
        participant_hex,
        context,
        niche_name,
        checkout_path,
        files=list(files) if files else None,
        message=message,
    )
    click.echo(f"Published: {commit_hash[:8]}")


@cli.command("log")
@click.argument("files_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
def log_cmd(files_root, participant_hex, team_name, niche_name):
    """Show commit log for a niche."""
    context = _team_context(files_root, participant_hex, team_name)
    entries = files_core.log(files_root, participant_hex, context, niche_name)
    if not entries:
        click.echo("No commits.")
        return
    for entry in entries:
        click.echo(f"  {entry['hash']}  {entry['message']}")
