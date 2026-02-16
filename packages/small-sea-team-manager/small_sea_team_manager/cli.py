# Top Matter

import click

from small_sea_team_manager.manager import TeamManager


@click.group()
@click.option("--hub-port", type=int, default=11437, help="Port for the local Small Sea Hub")
@click.pass_context
def cli(ctx, hub_port):
    """Small Sea Team Manager CLI"""
    ctx.ensure_object(dict)
    ctx.obj["hub_port"] = hub_port


def _make_manager(ctx, nickname):
    manager = TeamManager(hub_port=ctx.obj["hub_port"])
    manager.connect(nickname)
    return manager


@cli.command("create")
@click.argument("nickname")
@click.argument("team_name")
@click.pass_context
def create_team(ctx, nickname, team_name):
    """Create a new team."""
    manager = _make_manager(ctx, nickname)
    manager.create_team(team_name)
    click.echo(f"Created team '{team_name}'")


@cli.command("list")
@click.argument("nickname")
@click.pass_context
def list_teams(ctx, nickname):
    """List teams you belong to."""
    manager = _make_manager(ctx, nickname)
    teams = manager.list_teams()
    if not teams:
        click.echo("No teams found.")
        return
    for team in teams:
        role = team.get("role", "?")
        members = team.get("members", "?")
        click.echo(f"  {team['name']}  (role: {role}, members: {members})")


@cli.command("members")
@click.argument("nickname")
@click.argument("team_name")
@click.pass_context
def list_members(ctx, nickname, team_name):
    """List members of a team."""
    manager = _make_manager(ctx, nickname)
    members = manager.list_members(team_name)
    if not members:
        click.echo(f"No members in '{team_name}'.")
        return
    for member in members:
        role = member.get("role", "?")
        click.echo(f"  {member['nickname']}  ({role})")


@cli.command("invite")
@click.argument("nickname")
@click.argument("team_name")
@click.argument("invitee")
@click.pass_context
def invite(ctx, nickname, team_name, invitee):
    """Invite someone to join a team."""
    manager = _make_manager(ctx, nickname)
    manager.create_invitation(team_name, invitee)
    click.echo(f"Invited '{invitee}' to '{team_name}'")


@cli.command("invitations")
@click.argument("nickname")
@click.argument("team_name")
@click.pass_context
def list_invitations(ctx, nickname, team_name):
    """List pending invitations for a team."""
    manager = _make_manager(ctx, nickname)
    invitations = manager.list_invitations(team_name)
    if not invitations:
        click.echo(f"No pending invitations for '{team_name}'.")
        return
    for inv in invitations:
        click.echo(f"  {inv.get('id', '?')} -> {inv.get('invitee', '?')}")


@cli.command("accept")
@click.argument("nickname")
@click.argument("invitation_id")
@click.pass_context
def accept_invitation(ctx, nickname, invitation_id):
    """Accept a team invitation."""
    manager = _make_manager(ctx, nickname)
    manager.accept_invitation(invitation_id)
    click.echo(f"Accepted invitation '{invitation_id}'")


@cli.command("remove-member")
@click.argument("nickname")
@click.argument("team_name")
@click.argument("member")
@click.pass_context
def remove_member(ctx, nickname, team_name, member):
    """Remove a member from a team."""
    manager = _make_manager(ctx, nickname)
    manager.remove_member(team_name, member)
    click.echo(f"Removed '{member}' from '{team_name}'")


@cli.command("set-role")
@click.argument("nickname")
@click.argument("team_name")
@click.argument("member")
@click.argument("role", type=click.Choice(["admin", "observer"]))
@click.pass_context
def set_role(ctx, nickname, team_name, member, role):
    """Set a member's role in a team."""
    manager = _make_manager(ctx, nickname)
    manager.set_member_role(team_name, member, role)
    click.echo(f"Set '{member}' role to '{role}' in '{team_name}'")


if __name__ == "__main__":
    cli()
