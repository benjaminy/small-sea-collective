#

from typing import Optional, Union

import sys
import os

from fastapi import FastAPI, Form, Request, HTTPException
import pydantic

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
async def create_new_participant(
        request: Request):
    req_data = await request.json()
    if not "nickname" in req_data:
        raise HTTPException( status=400, detail=f"Missing 'nickname'" )
    small_sea = app.state.backend
    id_hex = small_sea.create_new_participant( req_data[ "nickname" ] )
    return { "message": id_hex }


class SessionReq(pydantic.BaseModel):
    participant: str
    app: str
    team: str
    client: str

@app.post("/sessions")
async def open_session(req: SessionReq):
    small_sea = app.state.backend
    try:
        session_suid = small_sea.open_session(
            req.participant,
            req.app,
            req.team,
            req.client)
        session_hex = "".join( f"{b:02x}" for b in session_suid )
        return session_hex
    except Exception as exn:
        return f"error {str(exn)}"


class NewTeamReq(pydantic.BaseModel):
    session: str
    name: str

@app.post( "/teams" )
async def new_team(req: NewTeamReq):
    small_sea = app.state.backend
    id_hex = small_sea.new_team(
        req.session,
        req.name)
    return { "message": id_hex }


class AddCloudLocReq(pydantic.BaseModel):
    session: str
    protocol: str
    url: str

@app.post( "/cloud_locations" )
async def add_cloud(req: AddCloudLocReq):
    small_sea = app.state.backend
    id_hex = small_sea.add_cloud_location( req.session, req.protocol, req.url )
    return { "message": id_hex }

class PutBlobReq(pydantic.BaseModel):
    session: str
    path: str
    blob: Union[str, bytes]
    if_match: Optional[str]
    if_none_match: Optional[str]

@app.post( "/blobs" )
async def put_blob(req: PutBlobReq):
    small_sea = app.state.backend
    id_hex = small_sea.put_blob( req.session, req.protocol, req.url )
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
