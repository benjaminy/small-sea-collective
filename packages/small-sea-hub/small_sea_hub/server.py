#

from typing import Optional, Union

import sys
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, HTTPException
import pydantic

from small_sea_hub.config import Settings
from small_sea_hub.backend import SmallSeaBackend

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "backend"):
        settings = Settings()
        app.state.backend = SmallSeaBackend(root_dir=settings.get_root_dir())
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
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None

@app.post( "/cloud_locations" )
async def add_cloud(req: AddCloudLocReq):
    small_sea = app.state.backend
    id_hex = small_sea.add_cloud_location(
        req.session, req.backend, req.url,
        access_key=req.access_key, secret_key=req.secret_key,
        client_id=req.client_id, client_secret=req.client_secret,
        refresh_token=req.refresh_token )
    return { "message": id_hex }


class CloudUploadReq(pydantic.BaseModel):
    session: str
    path: str
    data: str  # base64-encoded

@app.post("/cloud_file")
async def upload_to_cloud(req: CloudUploadReq):
    import base64
    small_sea = app.state.backend
    decoded_data = base64.b64decode(req.data)
    ok, etag, msg = small_sea.upload_to_cloud(req.session, req.path, decoded_data)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return { "ok": True, "etag": etag, "message": msg }


@app.get("/cloud_file")
async def download_from_cloud(session: str, path: str):
    import base64
    small_sea = app.state.backend
    ok, data, etag = small_sea.download_from_cloud(session, path)
    if not ok:
        raise HTTPException(status_code=404, detail=etag)
    return { "ok": True, "data": base64.b64encode(data).decode(), "etag": etag }


# ---- Sync ----

class CloudSyncReq(pydantic.BaseModel):
    session: str

@app.post( "/sync_to_cloud" )
async def sync_to_cloud(req: CloudSyncReq):
    small_sea = app.state.backend
    id_hex = small_sea.sync_to_cloud(req.session)
    return { "message": id_hex }
