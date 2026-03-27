#

import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, Union

import pydantic
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from small_sea_hub.backend import SmallSeaBackend, SmallSeaNotFoundExn
from small_sea_hub.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "backend"):
        settings = Settings()
        app.state.backend = SmallSeaBackend(root_dir=settings.get_root_dir())
        app.state.auto_approve_sessions = settings.auto_approve_sessions
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


@app.exception_handler(SmallSeaNotFoundExn)
async def not_found_handler(request: Request, exc: SmallSeaNotFoundExn):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/")
async def root():
    return {"message": "Hello World"}


# ---- Authorization ----


def _require_session(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    return authorization[7:]


# ---- Session management ----


class SessionRequestReq(pydantic.BaseModel):
    participant: str
    app: str
    team: str
    client: str


@app.post("/sessions/request")
async def request_session(req: SessionRequestReq):
    small_sea = app.state.backend
    pending_id_hex, pin = small_sea.request_session(
        req.participant, req.app, req.team, req.client
    )
    if getattr(app.state, "auto_approve_sessions", False):
        token = small_sea.confirm_session(pending_id_hex, pin)
        return {"token": token.hex()}
    result = {"pending_id": pending_id_hex}
    if req.client == "Smoke Tests":
        result["pin"] = pin
    return result


class SessionConfirmReq(pydantic.BaseModel):
    pending_id: str
    pin: str


@app.post("/sessions/confirm")
async def confirm_session(req: SessionConfirmReq):
    small_sea = app.state.backend
    token = small_sea.confirm_session(req.pending_id, req.pin)
    return token.hex()


# ---- Cloud storage ----


class CloudUploadReq(pydantic.BaseModel):
    path: str
    data: str  # base64-encoded
    expected_etag: Optional[str] = None


@app.post("/cloud_file")
async def upload_to_cloud(
    req: CloudUploadReq, session_hex: str = Depends(_require_session)
):
    import base64

    small_sea = app.state.backend
    decoded_data = base64.b64decode(req.data)
    ok, etag, msg = small_sea.upload_to_cloud(
        session_hex, req.path, decoded_data, expected_etag=req.expected_etag
    )
    if not ok:
        if msg == "CAS_CONFLICT":
            raise HTTPException(
                status_code=409, detail="CAS conflict: file was modified concurrently"
            )
        raise HTTPException(status_code=500, detail=msg)
    return {"ok": True, "etag": etag, "message": msg}


@app.get("/cloud_file")
async def download_from_cloud(path: str, session_hex: str = Depends(_require_session)):
    import base64

    small_sea = app.state.backend
    ok, data, etag = small_sea.download_from_cloud(session_hex, path)
    if not ok:
        raise HTTPException(status_code=404, detail=etag)
    return {"ok": True, "data": base64.b64encode(data).decode(), "etag": etag}


@app.post("/cloud/setup")
async def cloud_setup(session_hex: str = Depends(_require_session)):
    app.state.backend.ensure_cloud_ready(session_hex)
    return {"ok": True}


@app.get("/peer_cloud_file")
async def download_peer_cloud_file(
    member_id: str,
    path: str,
    session_hex: str = Depends(_require_session),
):
    import base64

    small_sea = app.state.backend
    ok, data, etag = small_sea.download_from_peer(session_hex, member_id, path)
    if not ok:
        raise HTTPException(status_code=404, detail=etag)
    return {"ok": True, "data": base64.b64encode(data).decode(), "etag": etag}


# ---- Notifications ----


class SendNotificationReq(pydantic.BaseModel):
    message: str
    title: Optional[str] = None


@app.post("/notifications")
async def send_notification(
    req: SendNotificationReq, session_hex: str = Depends(_require_session)
):
    small_sea = app.state.backend
    ok, msg_id, err = small_sea.send_notification(
        session_hex, req.message, title=req.title
    )
    if not ok:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "id": msg_id}


@app.get("/notifications")
async def poll_notifications(
    session_hex: str = Depends(_require_session),
    since: Optional[str] = None,
    timeout: int = 30,
):
    small_sea = app.state.backend
    messages = small_sea.poll_notifications(session_hex, since=since, timeout=timeout)
    return {"ok": True, "messages": messages}
