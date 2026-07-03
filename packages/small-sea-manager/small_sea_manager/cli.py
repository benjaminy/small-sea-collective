"""Click CLI for the Small Sea Manager."""

import json
import pathlib
import sys
import tomllib

import click

from small_sea_manager.manager import TeamManager

_CONFIG_PATH = pathlib.Path.home() / ".config" / "small-sea" / "manager.toml"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


@click.group()
@click.option(
    "--hub-port", type=int, default=None,
    help="Port for the local Small Sea Hub (default: 11437)",
)
@click.option(
    "--root-dir", default=None,
    help=f"Participant root directory (or set in {_CONFIG_PATH})",
)
@click.option(
    "--participant-hex", default=None,
    help=f"Participant hex ID (or set in {_CONFIG_PATH})",
)
@click.pass_context
def cli(ctx, hub_port, root_dir, participant_hex):
    """Small Sea Manager"""
    cfg = _load_config()
    ctx.ensure_object(dict)
    ctx.obj["root_dir"] = root_dir or cfg.get("root_dir")
    ctx.obj["participant_hex"] = participant_hex or cfg.get("participant_hex")
    ctx.obj["hub_port"] = hub_port or cfg.get("hub_port", 11437)


def _make_manager(ctx) -> TeamManager:
    root_dir = ctx.obj["root_dir"]
    participant_hex = ctx.obj["participant_hex"]
    if not root_dir or not participant_hex:
        click.echo(
            f"Error: --root-dir and --participant-hex are required "
            f"(or set them in {_CONFIG_PATH}).",
            err=True,
        )
        sys.exit(1)
    return TeamManager(root_dir, participant_hex, ctx.obj["hub_port"])


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, show_default=True)
@click.option("--open/--no-open", "open_browser", default=True,
              help="Open browser on start")
@click.pass_context
def serve_cmd(ctx, host, port, open_browser):
    """Start the Manager web UI."""
    import threading
    import webbrowser

    import uvicorn

    from small_sea_manager.web import create_app

    root_dir = ctx.obj["root_dir"]
    participant_hex = ctx.obj["participant_hex"]
    hub_port = ctx.obj["hub_port"]

    if not root_dir or not participant_hex:
        click.echo(
            f"Error: --root-dir and --participant-hex are required "
            f"(or set them in {_CONFIG_PATH}).",
            err=True,
        )
        sys.exit(1)

    app = create_app(root_dir, participant_hex, hub_port)
    url = f"http://{host}:{port}"

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    click.echo(f"Manager UI → {url}")
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# team operations
# ---------------------------------------------------------------------------

@cli.command("create")
@click.argument("team_name")
@click.pass_context
def create_team(ctx, team_name):
    """Create a new team."""
    manager = _make_manager(ctx)
    manager.create_team(team_name)
    click.echo(f"Created team '{team_name}'")


@cli.command("list")
@click.pass_context
def list_teams(ctx):
    """List teams you belong to."""
    manager = _make_manager(ctx)
    teams = manager.list_teams()
    if not teams:
        click.echo("No teams found.")
        return
    for team in teams:
        click.echo(f"  {team['name']}")


@cli.command("teammates")
@click.argument("team_name")
@click.pass_context
def list_teammates(ctx, team_name):
    """List teammates of a team."""
    manager = _make_manager(ctx)
    teammates = manager.list_teammates(team_name)
    if not teammates:
        click.echo(f"No teammates in '{team_name}'.")
        return
    for teammate in teammates:
        roles = teammate.get("berth_roles", [])
        role_str = roles[0]["role"] if roles else "no role"
        click.echo(f"  {teammate['id'][:12]}…  {role_str}")


@cli.command("invite")
@click.argument("team_name")
@click.option("--label", default=None, help="Human note for who this invitation is for")
@click.option(
    "--role",
    default="steward",
    type=click.Choice(["steward", "contributor"]),
    help="Role to grant on acceptance (default: steward)",
)
@click.pass_context
def invite(ctx, team_name, label, role):
    """Create an invitation token for a team. Prints the token to stdout."""
    manager = _make_manager(ctx)
    token = manager.create_invitation(team_name, invitee_label=label, role=role)
    click.echo(token)


@cli.command("invitations")
@click.argument("team_name")
@click.pass_context
def list_invitations(ctx, team_name):
    """List invitations for a team."""
    manager = _make_manager(ctx)
    invitations = manager.list_invitations(team_name)
    if not invitations:
        click.echo(f"No invitations for '{team_name}'.")
        return
    for inv in invitations:
        label = inv.get("invitee_label") or "(unlabelled)"
        click.echo(f"  {inv['id'][:12]}…  {inv['status']}  {label}  ({inv.get('role', '?')})")


@cli.command("accept")
@click.argument("token_b64")
@click.pass_context
def accept_invitation(ctx, token_b64):
    """Accept an invitation token (invitee side). Prints the acceptance token to stdout."""
    manager = _make_manager(ctx)
    acceptance = manager.accept_invitation(token_b64)
    click.echo(acceptance)


@cli.command("complete-acceptance")
@click.argument("team_name")
@click.argument("acceptance_b64")
@click.pass_context
def complete_acceptance(ctx, team_name, acceptance_b64):
    """Complete an acceptance (inviter side), given the acceptance token from the invitee."""
    manager = _make_manager(ctx)
    manager.complete_invitation_acceptance(team_name, acceptance_b64)
    click.echo(f"Acceptance complete for team '{team_name}'")


@cli.command("revoke")
@click.argument("team_name")
@click.argument("invitation_id")
@click.pass_context
def revoke_invitation(ctx, team_name, invitation_id):
    """Revoke a pending invitation."""
    manager = _make_manager(ctx)
    manager.revoke_invitation(team_name, invitation_id)
    click.echo(f"Revoked invitation '{invitation_id}'")


@cli.command("remove-teammate")
@click.argument("team_name")
@click.argument("teammate")
@click.pass_context
def remove_teammate(ctx, team_name, teammate):
    """Remove a teammate from a team."""
    manager = _make_manager(ctx)
    result = manager.remove_teammate(team_name, teammate)
    click.echo(json.dumps(result, indent=2, sort_keys=True))


@cli.command("set-role")
@click.argument("team_name")
@click.argument("teammate")
@click.argument("role", type=click.Choice(["steward", "contributor"]))
@click.pass_context
def set_role(ctx, team_name, teammate, role):
    """Set a teammate's role in a team."""
    manager = _make_manager(ctx)
    manager.set_teammate_role(team_name, teammate, role)
    click.echo(f"Set '{teammate}' role to '{role}' in '{team_name}'")


@cli.command("set-notification-service")
@click.argument("protocol", type=click.Choice(["ntfy", "gotify"]))
@click.argument("url")
@click.option("--access-key", default=None, help="Auth token (ntfy) or app token (gotify)")
@click.option("--access-token", default=None, help="Client token (gotify subscribe)")
@click.pass_context
def set_notification_service(ctx, protocol, url, access_key, access_token):
    """Configure a push notification service (replaces any existing entry of the same protocol)."""
    manager = _make_manager(ctx)
    ns_id = manager.set_notification_service(
        protocol, url, access_key=access_key, access_token=access_token
    )
    click.echo(f"Notification service ({protocol}) set: {ns_id[:12]}…")


if __name__ == "__main__":
    cli()
