#

import sys
import os

from fastapi import FastAPI, Form, Request, HTTPException

from small_sea_backend import SmallSeaBackend

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Code to run on startup, such as initializing a DB connection
    print("Starting up...")
    # Example: database.connect()


@app.on_event("shutdown")
async def shutdown_event():
    # Code to run on shutdown, such as closing DB connections
    print("Shutting down...")
    # Example: database.disconnect()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post( "/synthesize_new_user" )
async def new_user_form( request: Request ):
    req_data = await request.json()
    if not "nickname" in req_data:
        raise HTTPException( status=400, detail=f"Missing 'nickname'" )
    small_sea = SmallSeaBackend()
    id_hex = small_sea.new_identity( req_data[ "nickname" ] )
    return { "message": id_hex }



@app.get("/session/user/{ident}")
async def start_session_user( ident ):
    small_sea = SmallSeaBackend()
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
