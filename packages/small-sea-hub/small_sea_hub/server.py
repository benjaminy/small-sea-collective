#

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, Union

import pydantic
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from small_sea_hub.backend import SmallSeaBackend, SmallSeaNotFoundExn
from small_sea_hub.config import Settings

PEER_WATCHER_INTERVAL = 60  # seconds between poll rounds


async def _peer_watcher_loop(app: FastAPI):
    """Background task: poll registered peers' signal files for changes.

    Peers are registered in app.state.watched_peers by _register_session_peers()
    when a session is opened. Each entry tracks the last-seen etag and signal
    counts so only genuinely new data triggers a notification.
    """
    logger = app.state.logger
    while True:
        await asyncio.sleep(PEER_WATCHER_INTERVAL)
        peers = getattr(app.state, "watched_peers", {})
        for key, state in list(peers.items()):
            session_hex, member_id_hex = key
            try:
                signals, etag = app.state.backend.get_peer_signal(
                    session_hex, member_id_hex
                )
                if signals is None:
                    continue
                if etag == state.get("etag"):
                    continue  # unchanged
                prev = state.get("signals", {})
                for station_id, count in signals.items():
                    if station_id == "version":
                        continue
                    if count > prev.get(station_id, 0):
                        logger.info(
                            f"Peer {member_id_hex[:8]} has new data on station "
                            f"{station_id[:8]} (count {count})"
                        )
                        # TODO: fire ntfy / mailbox notification here (issue 0023 follow-on)
                state["etag"] = etag
                state["signals"] = {k: v for k, v in signals.items() if k != "version"}
            except Exception as exc:
                logger.warning(f"Peer watcher error for {member_id_hex[:8]}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "backend"):
        settings = Settings()
        app.state.backend = SmallSeaBackend(root_dir=settings.get_root_dir())
        app.state.auto_approve_sessions = settings.auto_approve_sessions
    if not hasattr(app.state, "watched_peers"):
        app.state.watched_peers = {}
    app.state.logger = app.state.backend.logger
    logger = app.state.backend.logger
    logger.info("Starting up...")

    watcher_task = asyncio.create_task(_peer_watcher_loop(app))

    yield

    watcher_task.cancel()
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


def _register_session_peers(session_hex: str):
    """Add the session's team peers to the watcher after a session is confirmed."""
    try:
        ss_session = app.state.backend._lookup_session(session_hex)
        if ss_session.team_name == "NoteToSelf":
            return  # NoteToSelf has no peers
        import sqlite3 as _sqlite3
        team_db = str(
            ss_session.participant_path / ss_session.team_name / "Sync" / "core.db"
        )
        conn = _sqlite3.connect(team_db)
        try:
            rows = conn.execute("SELECT member_id FROM peer").fetchall()
        finally:
            conn.close()
        for (member_id_bytes,) in rows:
            key = (session_hex, member_id_bytes.hex())
            app.state.watched_peers.setdefault(key, {"etag": None, "signals": {}})
    except Exception as exc:
        app.state.logger.warning(f"_register_session_peers failed: {exc}")


@app.post("/sessions/request")
async def request_session(req: SessionRequestReq):
    small_sea = app.state.backend
    pending_id_hex, pin = small_sea.request_session(
        req.participant, req.app, req.team, req.client
    )
    if getattr(app.state, "auto_approve_sessions", False):
        token = small_sea.confirm_session(pending_id_hex, pin)
        token_hex = token.hex()
        _register_session_peers(token_hex)
        return {"token": token_hex}
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
    token_hex = token.hex()
    _register_session_peers(token_hex)
    return token_hex


# ---- Cloud storage ----


class CloudUploadReq(pydantic.BaseModel):
    path: str
    data: str  # base64-encoded
    expected_etag: Optional[str] = None
    notify: bool = False  # bump signals.yaml and notify teammates after upload


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
    if req.notify:
        try:
            small_sea._bump_signal(session_hex)
        except Exception as exc:
            app.state.logger.warning(f"_bump_signal failed: {exc}")
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


@app.get("/peer_signal")
async def get_peer_signal(
    member_id: str,
    session_hex: str = Depends(_require_session),
    if_none_match: Optional[str] = Header(default=None),
):
    small_sea = app.state.backend
    signals, etag = small_sea.get_peer_signal(session_hex, member_id)
    if signals is None:
        raise HTTPException(status_code=404, detail="No signal file found for peer")
    if if_none_match and etag == if_none_match:
        from fastapi.responses import Response
        return Response(status_code=304)
    return {"version": signals.get("version", 1), "stations": {
        k: v for k, v in signals.items() if k != "version"
    }, "etag": etag}


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
