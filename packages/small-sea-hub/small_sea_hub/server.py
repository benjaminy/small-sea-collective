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


def _pulse_station_event(app: FastAPI, station_id_hex: str):
    """Wake all waiters on a station by replacing its Event and setting the old one."""
    events = getattr(app.state, "peer_signal_events", None)
    if events is None:
        return  # Watcher state not yet initialized
    old_event = events.get(station_id_hex)
    events[station_id_hex] = asyncio.Event()
    if old_event:
        old_event.set()


def _refresh_session_peers(app: FastAPI, session_hex: str):
    """Re-read the team DB peer list for a session and sync watched_peers.

    Adds new peers (pulsing the station event so waiters wake and re-enumerate)
    and removes peers that are no longer in the DB.
    """
    import sqlite3 as _sqlite3

    session_info = app.state.watched_sessions.get(session_hex)
    if session_info is None:
        return

    station_id_hex = session_info["station_id_hex"]
    team_db_path = session_info["team_db_path"]

    try:
        conn = _sqlite3.connect(team_db_path)
        try:
            rows = conn.execute("SELECT member_id FROM peer").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        app.state.logger.warning(f"_refresh_session_peers: DB read failed: {exc}")
        return

    current_member_ids = {row[0].hex() for row in rows}
    existing_member_ids = {
        mid for (sid, mid) in app.state.watched_peers if sid == session_hex
    }

    new_members = current_member_ids - existing_member_ids
    removed_members = existing_member_ids - current_member_ids

    for member_id_hex in new_members:
        key = (session_hex, member_id_hex)
        app.state.watched_peers[key] = {
            "etag": None,
            "signals": {},
            "station_id_hex": station_id_hex,
        }
        app.state.logger.info(
            f"Watcher: new peer {member_id_hex[:8]} on station {station_id_hex[:8]}"
        )

    for member_id_hex in removed_members:
        app.state.watched_peers.pop((session_hex, member_id_hex), None)

    if new_members:
        _pulse_station_event(app, station_id_hex)


def _send_peer_notification(app: FastAPI, session_hex: str, station_id_hex: str, logger):
    """Fire a push notification when a peer's signal count increases.

    Silently skips if no notification service is configured for the session.
    Errors are logged as warnings and do not affect watcher operation.
    """
    from small_sea_hub.backend import SmallSeaNotFoundExn as _NotFound
    try:
        ok, _, err = app.state.backend.send_notification(
            session_hex,
            "A teammate has pushed new data",
            title="Small Sea",
        )
        if not ok:
            logger.warning(
                f"Push notification failed for station {station_id_hex[:8]}: {err}"
            )
    except _NotFound:
        pass  # no notification service configured — normal
    except Exception as exc:
        logger.warning(
            f"Push notification error for station {station_id_hex[:8]}: {exc}"
        )


async def _peer_watcher_loop(app: FastAPI):
    """Background task: poll registered peers' signal files for changes.

    On each round, re-reads the peer list for every active session so that
    membership changes (new or removed peers) are picked up automatically.
    New peers cause an immediate event pulse so any waiting long-pollers wake
    and can re-enumerate the current member list.

    When a peer's signal count increases, updates peer_counts and pulses the
    station event to wake /notifications/watch waiters.

    The first pass runs immediately (no initial sleep) so that peer_counts is
    populated quickly after startup rather than after the full interval.
    """
    logger = app.state.logger
    first_pass = True
    while True:
        if not first_pass:
            await asyncio.sleep(PEER_WATCHER_INTERVAL)
        first_pass = False

        # Refresh peer lists for all active sessions before polling signals.
        for session_hex in list(app.state.watched_sessions):
            _refresh_session_peers(app, session_hex)

        peers = getattr(app.state, "watched_peers", {})
        # Track which stations have already received a push notification this round
        # so we send at most one notification per station regardless of how many
        # sessions or peers triggered the change.
        notified_stations: set = set()
        for key, state in list(peers.items()):
            session_hex, member_id_hex = key
            station_id_hex = state.get("station_id_hex")
            try:
                signals, etag = app.state.backend.get_peer_signal(
                    session_hex, member_id_hex
                )
                if signals is None:
                    continue
                if etag == state.get("etag"):
                    continue  # unchanged

                prev = state.get("signals", {})
                changed = False
                for sid, count in signals.items():
                    if sid == "version":
                        continue
                    if count > prev.get(sid, 0):
                        app.state.peer_counts[(sid, member_id_hex)] = count
                        changed = True
                        logger.info(
                            f"Peer {member_id_hex[:8]} station {sid[:8]}: count={count}"
                        )

                state["etag"] = etag
                state["signals"] = {k: v for k, v in signals.items() if k != "version"}

                if changed and station_id_hex:
                    _pulse_station_event(app, station_id_hex)
                    if station_id_hex not in notified_stations:
                        notified_stations.add(station_id_hex)
                        _send_peer_notification(app, session_hex, station_id_hex, logger)

            except SmallSeaNotFoundExn:
                # Session expired — remove it and all its peers from the watcher.
                app.state.watched_sessions.pop(session_hex, None)
                stale_keys = [k for k in app.state.watched_peers if k[0] == session_hex]
                for k in stale_keys:
                    app.state.watched_peers.pop(k, None)
                logger.info(f"Removed expired session {session_hex[:8]} from watcher")
            except Exception as exc:
                logger.warning(f"Peer watcher error for {member_id_hex[:8]}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "backend"):
        settings = Settings()
        app.state.backend = SmallSeaBackend(
            root_dir=settings.get_root_dir(),
            auto_approve_sessions=settings.auto_approve_sessions,
            sandbox_mode=settings.sandbox_mode,
        )
    if not hasattr(app.state, "watched_sessions"):
        app.state.watched_sessions = {}   # session_hex → {station_id_hex, team_db_path}
    if not hasattr(app.state, "watched_peers"):
        app.state.watched_peers = {}      # (session_hex, member_id_hex) → state
    if not hasattr(app.state, "peer_counts"):
        app.state.peer_counts = {}        # (station_id_hex, member_id_hex) → int
    if not hasattr(app.state, "peer_signal_events"):
        app.state.peer_signal_events = {}  # station_id_hex → asyncio.Event
    app.state.logger = app.state.backend.logger
    logger = app.state.backend.logger
    logger.info("Starting up...")

    # Rebuild watcher state from persisted sessions so a Hub restart does not
    # require apps to re-open their sessions or wait for the first watcher round.
    for token_hex in app.state.backend.all_session_tokens():
        _register_session_peers(token_hex)

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
    """Register a session with the watcher after it is confirmed.

    Records the team DB path and station so the watcher can re-read the peer
    list on every round, picking up membership changes automatically.
    No-ops silently if the watcher state has not been initialized yet (e.g.
    in tests that do not run the full lifespan).
    """
    watched_sessions = getattr(app.state, "watched_sessions", None)
    if watched_sessions is None:
        return  # Watcher state not yet initialized
    try:
        ss_session = app.state.backend._lookup_session(session_hex)
        if ss_session.team_name == "NoteToSelf":
            return  # NoteToSelf has no peers
        station_id_hex = ss_session.station_id.hex()
        team_db_path = str(
            ss_session.participant_path / ss_session.team_name / "Sync" / "core.db"
        )
        watched_sessions[session_hex] = {
            "station_id_hex": station_id_hex,
            "team_db_path": team_db_path,
        }
        app.state.peer_signal_events.setdefault(station_id_hex, asyncio.Event())
        # Do an immediate peer refresh so watched_peers is populated now rather
        # than waiting for the first watcher round.
        _refresh_session_peers(app, session_hex)
    except Exception as exc:
        logger = getattr(app.state, "logger", None)
        if logger:
            logger.warning(f"_register_session_peers failed: {exc}")


@app.post("/sessions/request")
async def request_session(req: SessionRequestReq):
    small_sea = app.state.backend
    # When auto-approving, skip OS notifications by presenting as "Smoke Tests".
    effective_client = (
        "Smoke Tests"
        if app.state.backend.auto_approve_sessions
        else req.client
    )
    pending_id_hex, pin = small_sea.request_session(
        req.participant, req.app, req.team, effective_client
    )
    if app.state.backend.auto_approve_sessions:
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


@app.get("/sessions/pending")
async def list_pending_sessions():
    """List pending sessions with PINs.

    Only available when the Hub is started with SMALL_SEA_SANDBOX_MODE=1.
    Exposing PINs over HTTP is unsafe in production; this endpoint is
    intended solely for the sandbox dashboard.
    """
    if not app.state.backend.sandbox_mode:
        raise HTTPException(status_code=404, detail="Not found")
    return app.state.backend.list_pending_sessions()


@app.get("/session/info")
async def session_info(session_hex: str = Depends(_require_session)):
    """Return metadata for the current session.

    Allows apps to discover their station_id and team context from a session
    token, without reading the SmallSeaCollectiveCore SQLite database directly.
    """
    ss_session = app.state.backend._lookup_session(session_hex)
    return {
        "participant_hex": ss_session.participant_id.hex(),
        "team_name": ss_session.team_name,
        "app_name": ss_session.app_name,
        "station_id": ss_session.station_id.hex(),
        "client": ss_session.client,
    }


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
        _logger = getattr(app.state, "logger", None)
        try:
            small_sea._bump_signal(session_hex)
        except Exception as exc:
            if _logger:
                _logger.warning(f"_bump_signal failed: {exc}")
        # Pulse the local station event so other sessions on this station
        # (e.g. a second browser tab) are also notified.
        try:
            ss_session = small_sea._lookup_session(session_hex)
            _pulse_station_event(app, ss_session.station_id.hex())
        except Exception as exc:
            if _logger:
                _logger.warning(f"local station pulse failed: {exc}")
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


@app.get("/cloud_proxy")
async def proxy_cloud_file(
    protocol: str,
    url: str,
    bucket: str,
    path: str,
    session_hex: str = Depends(_require_session),
):
    """Proxy a file download from an arbitrary cloud location.

    Requires a NoteToSelf session. Allows Bob's Manager to clone Alice's team
    repo during invitation acceptance, before any peer relationship exists.
    Hub authenticates and uses its own credentials — the client never talks to
    cloud storage directly.
    """
    import base64

    small_sea = app.state.backend
    ok, data, etag = small_sea.proxy_cloud_file(session_hex, protocol, url, bucket, path)
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


class WatchNotificationsReq(pydantic.BaseModel):
    known: dict[str, int] = {}  # member_id_hex → last known count
    timeout: int = 30


@app.post("/notifications/watch")
async def watch_notifications(
    req: WatchNotificationsReq,
    session_hex: str = Depends(_require_session),
):
    """Long-poll for peer sync updates.

    The client supplies its current known counts per peer member. If the Hub
    already has higher counts, returns immediately. Otherwise blocks until a
    peer's count increases (or timeout), then returns whatever changed.

    The response is {"updated": {member_id_hex: new_count, ...}}, empty on
    timeout.
    """
    ss_session = app.state.backend._lookup_session(session_hex)
    station_id_hex = ss_session.station_id.hex()

    def _check():
        updated = {}
        for member_id_hex, known_count in req.known.items():
            current = app.state.peer_counts.get((station_id_hex, member_id_hex), 0)
            if current > known_count:
                updated[member_id_hex] = current
        return updated

    # Return immediately if we already know about newer data.
    updated = _check()
    if updated:
        return {"updated": updated}

    # Grab the current event before sleeping — the watcher may replace it
    # while we wait, but we hold the reference so set() still wakes us.
    event = app.state.peer_signal_events.setdefault(station_id_hex, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=req.timeout)
    except asyncio.TimeoutError:
        return {"updated": {}}

    return {"updated": _check()}


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
