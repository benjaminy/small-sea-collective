# Top Matter

import click

import os
import json

import small_sea_client_lib as SmallSeaLib

@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--hub_port", type=int, default=11437, help="Port number for the local hub")
@click.pass_context
def cli(ctx, verbose, hub_port):
    """ Small Sea Core CLI tool """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["hub_port"] = hub_port


@cli.command()
@click.argument("nickname")
@click.option("--cloud_backend", type=click.Choice(["s3", "webdav","drive"]))
@click.option("--cloud_url")
@click.pass_context
def new_participant(ctx, nickname, cloud_backend, cloud_url):
    """ Create a new participant identity in the SmallSea universe.

    In normal day to day operations, it should be an uncommon command
    """
    small_sea = SmallSeaLib.SmallSeaClient()
    result = small_sea.create_new_participant( nickname )

    if (cloud_backend is None) != (cloud_url is None):
        click.echo("ERROR. Only one of backend and url provided")
    elif cloud_url is not None:
        click.echo("SHOULD ADD CLOUD")


@cli.command()
@click.argument("nickname")
@click.argument("team_name")
@click.pass_context
def open_session(ctx, nickname, team_name ):
    def encode_sessions( ss ):
        """ First serialize the keys, then the whole dict """
        step1 = { json.dumps(k): v for k, v in ss.items() }
        return json.dumps(step1)
    def decode_sessions( ss_str ):
        """ First deserialize the dict, then the keys """
        step1 = json.loads(ss_str)
        return { json.loads(k): v for k, v in step1 }

    sessions_env_str = os.getenv("SMALL_SEA_COLLECTIVE_CORE_TUI_SESSIONS", json.dumps({}))
    session = None
    format_error = True
    try:
        sessions = decode_sessions(sessions_env_str)
        if isinstance(sessions, dict):
            session = sessions.get((nickname, team_name))
            format_error = False
    except json.decoder.JSONDecodeError as exn:
        pass

    if format_error:
        click.echo(f"Small Sea TUI Sessions env var broken {sessions_env_str}")
        sessions = {}

    if session is None:
        click.echo( f"No session for {nickname} {team_name}. Requesting a session." )
        small_sea = SmallSeaLib.SmallSeaClient()
        session = small_sea.open_session( nickname, "small_sea_collective_core_app", team_name, "small_sea_tui" )
        sessions[ ( nickname, team_name ) ] = session
        os.putenv("SMALL_SEA_COLLECTIVE_CORE_TUI_SESSIONS", encode_sessions(sessions))

    session = sessions[(nickname, team_name)]
    click.echo( f"OMG {session}" )
    return session


@cli.command()
@click.argument("nickname")
@click.argument("backend", type=click.Choice(["s3", "webdav","drive"]))
@click.argument("url")
@click.pass_context
def add_cloud(ctx, nickname, backend, url ):
    small_sea = SmallSeaLib.SmallSeaClient()
    session = open_session( nickname, "NoteToSelf" )
    small_sea.add_cloud_location( session, backend, url )


@cli.command()
@click.argument("nickname")
@click.argument("team_name")
@click.pass_context
def create_new_team(ctx, nickname, team_name ):
    small_sea = SmallSeaLib.SmallSeaClient()
    session = open_session( nickname, "NoteToSelf" )
    small_sea.create_new_team( session, team_name )


@cli.command()
@click.argument("nickname")
@click.argument("team_name")
@click.option("--abort_on_stale", is_flag=True, help="If the local copy is stale, abort instead of automatically reconciling")
@click.pass_context
def upload_changes(ctx, nickname, team_name):
    small_sea = SmallSeaLib.SmallSeaClient()
    session = open_session(nickname, team_name)
    small_sea.upload_changes(session)


@cli.command()
@click.argument("nickname")
@click.pass_context
def make_device_link_invitation(ctx, nickname):
    pass


@cli.command()
@click.argument("nickname")
@click.argument("team_name")
@click.argument("invitee")
@click.pass_context
def make_team_invitation(ctx, nickname, team_name, invitee):
    pass


if __name__ == "__main__":
    if False:
        print("Registered commands:", list(cli.commands.keys()))
    # prog_name="Small Sea Core CLI"
    cli()
