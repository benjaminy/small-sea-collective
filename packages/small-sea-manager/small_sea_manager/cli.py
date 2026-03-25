# Top Matter

import click
from small_sea_manager.manager import TeamManager


@click.group()
@click.option(
    "--hub-port", type=int, default=11437, help="Port for the local Small Sea Hub"
)
@click.option(
    "--root-dir",
    envvar="SMALL_SEA_ROOT",
    required=True,
    help="Participant root directory (or set SMALL_SEA_ROOT)",
)
@click.option(
    "--participant-hex",
    envvar="SMALL_SEA_PARTICIPANT",
    required=True,
    help="Participant hex ID (or set SMALL_SEA_PARTICIPANT)",
)
@click.pass_context
def cli(ctx, hub_port, root_dir, participant_hex):
    """Small Sea Manager CLI"""
    ctx.ensure_object(dict)
    ctx.obj["hub_port"] = hub_port
    ctx.obj["root_dir"] = root_dir
    ctx.obj["participant_hex"] = participant_hex


def _make_manager(ctx):
    return TeamManager(
        root_dir=ctx.obj["root_dir"],
        participant_hex=ctx.obj["participant_hex"],
        hub_port=ctx.obj["hub_port"],
    )


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
        click.echo(f"  {member['id']}")


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
    """List pending invitations for a team."""
    manager = _make_manager(ctx)
    invitations = manager.list_invitations(team_name)
    if not invitations:
        click.echo(f"No pending invitations for '{team_name}'.")
        return
    for inv in invitations:
        label = inv.get("invitee_label") or inv.get("id", "?")
        click.echo(f"  {inv['id']}  {label}  ({inv.get('role', '?')})")


@cli.command("accept")
@click.argument("token_b64")
@click.pass_context
def accept_invitation(ctx, token_b64):
    """Accept an invitation token (Bob side). Prints the acceptance token to stdout.

    The inviter's bucket is read anonymously (publicly readable).
    The acceptor's bucket is written via the Hub session.
    """
    import base64, json
    from cod_sync.testing import PublicS3Remote, S3Remote

    manager = _make_manager(ctx)
    token = json.loads(base64.b64decode(token_b64).decode())
    ic = token["inviter_cloud"]
    inviter_remote = PublicS3Remote(ic["url"], token["inviter_bucket"])
    # Acceptor writes to the same bucket name (shared station ID) on their own cloud.
    # TODO: use SmallSeaRemote via Hub session once TeamManager.connect() is wired (0015).
    cloud = manager._cloud()
    acceptor_remote = S3Remote(cloud["url"], token["inviter_bucket"], cloud["access_key"], cloud["secret_key"])
    acceptance = manager.accept_invitation(token_b64, inviter_remote, acceptor_remote)
    click.echo(acceptance)


@cli.command("complete-acceptance")
@click.argument("team_name")
@click.argument("acceptance_b64")
@click.pass_context
def complete_acceptance(ctx, team_name, acceptance_b64):
    """Complete an acceptance (Alice side), given the acceptance token from the invitee."""
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
