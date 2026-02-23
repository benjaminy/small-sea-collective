"""Click CLI for the Small Sea Shared File Vault."""

import click

from shared_file_vault import vault


@click.group()
def cli():
    """Small Sea Shared File Vault"""


@cli.command("init")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
def init_cmd(vault_root, participant_hex, team_name):
    """Initialize a vault for a participant/team."""
    db = vault.init_vault(vault_root, participant_hex, team_name)
    click.echo(f"Vault initialized: {db}")


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
    """Check out a niche to a filesystem location."""
    dest = vault.checkout_niche(vault_root, participant_hex, team_name, niche_name, dest_path)
    click.echo(f"Checked out to {dest}")


@cli.command("list")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
def list_cmd(vault_root, participant_hex, team_name):
    """List all niches."""
    niches = vault.list_niches(vault_root, participant_hex, team_name)
    if not niches:
        click.echo("No niches.")
        return
    for n in niches:
        co = n["checkout_path"] or "(not checked out)"
        click.echo(f"  {n['name']}  {co}  [{n['id'][:8]}]")


@cli.command("status")
@click.argument("vault_root")
@click.argument("participant_hex")
@click.argument("team_name")
@click.argument("niche_name")
def status_cmd(vault_root, participant_hex, team_name, niche_name):
    """Show working tree status for a niche."""
    entries = vault.status(vault_root, participant_hex, team_name, niche_name)
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
@click.option("-m", "--message", default=None, help="Commit message")
@click.argument("files", nargs=-1)
def publish_cmd(vault_root, participant_hex, team_name, niche_name, message, files):
    """Publish changes (stage + commit)."""
    commit_hash = vault.publish(
        vault_root, participant_hex, team_name, niche_name,
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
