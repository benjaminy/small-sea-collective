#

import sys
import os

from fastapi import FastAPI, Form, Request, HTTPException

from small_sea_local_hub_config import settings
from small_sea_backend import SmallSeaBackend

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Code to run on startup, such as initializing a DB connection
    app.state.backend = SmallSeaBackend(
        settings.app_name,
        settings.small_sea_root_dir_suffix)
    app.state.logger = app.state.backend.logger
    logger = app.state.backend.logger

    if True:
        logger.info("Starting up...")
    else:
        logger.debug("This is a debug message (only in file).")
        logger.info("This is an info message (console + file).")
        logger.warning("This is a warning message.")
        logger.error("This is an error message.")
        logger.critical("This is a critical message.")


@app.on_event("shutdown")
async def shutdown_event():
    # Code to run on shutdown, such as closing DB connections
    print("Shutting down...")
    # Example: database.disconnect()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post( "/participants" )
async def new_user_form( request: Request ):
    req_data = await request.json()
    if not "nickname" in req_data:
        raise HTTPException( status=400, detail=f"Missing 'nickname'" )
    small_sea = app.state.backend
    id_hex = small_sea.create_new_participant( req_data[ "nickname" ] )
    return { "message": id_hex }



@app.get("/session/user/{ident}")
async def start_session_user( ident ):
    small_sea = app.state.backend
    session_suid = small_sea.start_session_user( ident )
    session_hex = "".join( f"{b:02x}" for b in session_suid )
    return session_hex

@app.get("/session/team/{ident}/{team_id}")
async def start_session_team( ident, team_id ):
    return {"message": f"Hello World app: {ident} {team_id}"}

@app.get("/session/app/{ident}/{app_id}")
async def start_session_app_meta( ident, app_id ):
    return {"message": f"Hello World app: {ident} {app_id}"}

@app.get("/session/app-team/{ident}/{app_id}/{team_id}")
async def start_session_app_team( ident, app_id, team_id ):
    return {"message": f"Hello World team: {ident} {app_id} {team_id}"}

@app.post( "/synthesize_new_team" )
async def new_team( request: Request ):
    req_data = await request.json()
    if not ( "session" in req_data and "team_name" in req_data ):
        raise HTTPException( status=400, detail=f"Missing 'session' and/or 'team_name'" )
    small_sea = app.state.backend
    id_hex = small_sea.new_team( req_data[ "session" ], req_data[ "team_name" ] )
    return { "message": id_hex }


@app.post( "/add_cloud_location" )
async def add_cloud( request: Request ):
    req_data = await request.json()
    if not ( "session" in req_data and "url" in req_data ):
        raise HTTPException( status=400, detail=f"Missing 'session' and/or 'url'" )
    small_sea = app.state.backend
    id_hex = small_sea.add_cloud_location( req_data[ "session" ], req_data[ "url" ] )
    return { "message": id_hex }


@app.get("//")
async def read_item(skip: int = 0, limit: int = 10):
    return {"skip": skip, "limit": limit}


# if __name__ == "__main__":
#     # import argparse

#     # parser = argparse.ArgumentParser( program_title )
#     # parser.add_argument( "command", type=str )
#     # parser.add_argument( "--root_data_dir", type=str, default=None )
#     # parser.add_argument( "more_args", nargs=argparse.REMAINDER )

#     # args = parser.parse_args()

#     # cc = CooperativeClique( root_dir=args.root_data_dir )
#     # exit_code = cc.main( args.command, args.more_args )
#     # sys.exit( exit_code )

#     main()
