"""Click CLI for the Small Sea Manager."""

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


@cli.command("members")
@click.argument("team_name")
@click.pass_context
def list_members(ctx, team_name):
    """List members of a team."""
    manager = _make_manager(ctx)
    members = manager.list_members(team_name)
    if not members:
        click.echo(f"No members in '{team_name}'.")
        return
    for member in members:
        roles = member.get("station_roles", [])
        role_str = roles[0]["role"] if roles else "no role"
        click.echo(f"  {member['id'][:12]}…  {role_str}")


@cli.command("invite")
@click.argument("team_name")
@click.option("--label", default=None, help="Human note for who this invitation is for")
@click.option(
    "--role",
    default="admin",
    type=click.Choice(["admin", "contributor", "observer"]),
    help="Role to grant on acceptance (default: admin)",
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
    import base64
    import json

    from cod_sync.testing import PublicS3Remote, S3Remote

    manager = _make_manager(ctx)
    token = json.loads(base64.b64decode(token_b64).decode())
    ic = token["inviter_cloud"]
    inviter_remote = PublicS3Remote(ic["url"], token["inviter_bucket"])
    cloud = manager._cloud()
    acceptor_remote = S3Remote(
        cloud["url"], token["inviter_bucket"], cloud["access_key"], cloud["secret_key"]
    )
    acceptance = manager.accept_invitation(token_b64, inviter_remote, acceptor_remote)
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


@cli.command("remove-member")
@click.argument("team_name")
@click.argument("member")
@click.pass_context
def remove_member(ctx, team_name, member):
    """Remove a member from a team."""
    manager = _make_manager(ctx)
    manager.remove_member(team_name, member)
    click.echo(f"Removed '{member}' from '{team_name}'")


@cli.command("set-role")
@click.argument("team_name")
@click.argument("member")
@click.argument("role", type=click.Choice(["admin", "observer"]))
@click.pass_context
def set_role(ctx, team_name, member, role):
    """Set a member's role in a team."""
    manager = _make_manager(ctx)
    manager.set_member_role(team_name, member, role)
    click.echo(f"Set '{member}' role to '{role}' in '{team_name}'")


if __name__ == "__main__":
    cli()
