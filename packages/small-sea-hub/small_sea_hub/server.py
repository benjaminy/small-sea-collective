#

from typing import Optional, Union

import sys
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, HTTPException
import pydantic

from small_sea_hub.config import settings
from small_sea_hub.backend import SmallSeaBackend

@asynccontextmanager
async def lifespan(app: FastAPI):
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

    yield

    print("Shutting down...")

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Hello World"}


# ---- Session management ----

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


# ---- Cloud storage ----

class AddCloudLocReq(pydantic.BaseModel):
    session: str
    backend: str
    url: str

@app.post( "/cloud_locations" )
async def add_cloud(req: AddCloudLocReq):
    small_sea = app.state.backend
    id_hex = small_sea.add_cloud_location( req.session, req.backend, req.url )
    return { "message": id_hex }


class CloudUploadReq(pydantic.BaseModel):
    session: str
    backend: str
    url: str

@app.post("/cloud_file")
async def upload_to_cloud():
    raise NotImplementedError("upload")


class CloudDownloadReq(pydantic.BaseModel):
    session: str
    backend: str
    url: str

@app.get("/cloud_file")
async def download_from_cloud():
    raise NotImplementedError("download")


# ---- Sync ----

class CloudSyncReq(pydantic.BaseModel):
    session: str

@app.post( "/sync_to_cloud" )
async def sync_to_cloud(req: CloudSyncReq):
    small_sea = app.state.backend
    id_hex = small_sea.sync_to_cloud(req.session)
    return { "message": id_hex }
