#

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, Union

import pathlib

import pydantic
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import small_sea_manager.provisioning as Provisioning
from small_sea_hub.backend import SmallSeaBackend, SmallSeaNotFoundExn
from small_sea_hub.config import Settings

_templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))

PEER_WATCHER_INTERVAL = 60  # seconds between poll rounds


def _pulse_berth_event(app: FastAPI, berth_id_hex: str):
    """Wake all waiters on a berth by replacing its Event and setting the old one."""
    events = getattr(app.state, "peer_signal_events", None)
    if events is None:
        return  # Watcher state not yet initialized
    old_event = events.get(berth_id_hex)
    events[berth_id_hex] = asyncio.Event()
    if old_event:
        old_event.set()


def _refresh_session_peers(app: FastAPI, session_hex: str):
    """Re-read the team DB peer list for a session and sync watched_peers.

    Adds new peers (pulsing the berth event so waiters wake and re-enumerate)
    and removes peers that are no longer in the DB.
    """
    import sqlite3 as _sqlite3

    session_info = app.state.watched_sessions.get(session_hex)
    if session_info is None:
        return
    if session_info.get("watch_self_only"):
        return

    berth_id_hex = session_info["berth_id_hex"]
    team_db_path = session_info["team_db_path"]
    self_in_team = session_info.get("self_in_team")

    try:
        conn = _sqlite3.connect(team_db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM member WHERE id != ?",
                (bytes.fromhex(self_in_team),),
            ).fetchall()
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
            "berth_id_hex": berth_id_hex,
        }
        app.state.logger.info(
            f"Watcher: new peer {member_id_hex[:8]} on berth {berth_id_hex[:8]}"
        )

    for member_id_hex in removed_members:
        app.state.watched_peers.pop((session_hex, member_id_hex), None)

    if new_members:
        _pulse_berth_event(app, berth_id_hex)


def _send_peer_notification(app: FastAPI, session_hex: str, berth_id_hex: str, logger):
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
                f"Push notification failed for berth {berth_id_hex[:8]}: {err}"
            )
    except _NotFound:
        pass  # no notification service configured — normal
    except Exception as exc:
        logger.warning(
            f"Push notification error for berth {berth_id_hex[:8]}: {exc}"
        )


def _team_db_revision(team_db_path: str):
    stat = pathlib.Path(team_db_path).stat()
    return (stat.st_mtime_ns, stat.st_size)


def _run_runtime_reconciliation_for_session(app: FastAPI, session_hex: str):
    session_info = app.state.watched_sessions.get(session_hex)
    if session_info is None:
        return False
    if session_info.get("watch_self_only"):
        return False
    try:
        revision = _team_db_revision(session_info["team_db_path"])
    except FileNotFoundError:
        return False
    if revision == session_info.get("team_db_revision"):
        return False

    ss_session = app.state.backend._lookup_session(session_hex)
    result = Provisioning.reconcile_runtime_state(
        app.state.backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
    )
    for artifact in result.get("redistribution_artifacts", []):
        ok, _etag, msg = app.state.backend.upload_runtime_artifact(
            session_hex,
            artifact["artifact_path"],
            artifact["distribution_payload"].encode("utf-8"),
        )
        if ok:
            Provisioning.mark_redistribution_delivery(
                app.state.backend.root_dir,
                ss_session.participant_id.hex(),
                team_id=bytes.fromhex(result["team_id_hex"]),
                sender_device_key_id=bytes.fromhex(artifact["sender_device_key_id_hex"]),
                sender_chain_id=bytes.fromhex(artifact["sender_chain_id_hex"]),
                target_device_key_id=bytes.fromhex(artifact["target_device_key_id_hex"]),
            )
        else:
            app.state.logger.warning(
                "Runtime artifact upload failed for %s -> %s: %s",
                artifact["sender_device_key_id_hex"][:8],
                artifact["target_device_key_id_hex"][:8],
                msg,
            )
    session_info["team_db_revision"] = revision
    return True


def _process_runtime_inbox_from_member(
    app: FastAPI,
    session_hex: str,
    member_id_hex: str,
    *,
    use_local_bucket: bool,
):
    ss_session = app.state.backend._lookup_session(session_hex)
    root_dir = app.state.backend.root_dir
    participant_hex = ss_session.participant_id.hex()
    team_name = ss_session.team_name
    team_id, self_in_team = Provisioning._team_row(root_dir, participant_hex, team_name)
    _local_private_key, local_team_device_public_key = Provisioning.get_current_team_device_key(
        root_dir,
        participant_hex,
        team_name,
    )
    local_device_key_id = Provisioning.key_id_from_public(local_team_device_public_key)
    trusted_public_keys_by_member = Provisioning.get_trusted_device_keys_by_member(
        root_dir,
        participant_hex,
        team_name,
    )
    member_id = bytes.fromhex(member_id_hex)
    if member_id not in trusted_public_keys_by_member:
        return

    for public_key in trusted_public_keys_by_member[member_id]:
        sender_device_key_id = Provisioning.key_id_from_public(public_key)
        if sender_device_key_id == local_device_key_id:
            continue
        artifact_path = Provisioning.runtime_redistribution_artifact_path(
            local_device_key_id,
            sender_device_key_id,
        )
        if use_local_bucket:
            ok, data, _etag = app.state.backend.download_runtime_artifact_from_cloud(
                session_hex,
                artifact_path,
            )
        else:
            ok, data, _etag = app.state.backend.download_runtime_artifact_from_peer(
                session_hex,
                member_id_hex,
                artifact_path,
            )
        if not ok:
            continue
        distribution_payload = data.decode("utf-8")
        metadata = Provisioning.peek_redistribution_payload_metadata(distribution_payload)
        if Provisioning._receipt_exists(
            root_dir,
            participant_hex,
            team_id=team_id,
            sender_device_key_id=bytes.fromhex(metadata["sender_device_key_id_hex"]),
            sender_chain_id=bytes.fromhex(metadata["sender_chain_id_hex"]),
            target_device_key_id=bytes.fromhex(metadata["target_device_key_id_hex"]),
        ):
            continue
        try:
            received = Provisioning.receive_sender_key_distribution(
                root_dir,
                participant_hex,
                team_name,
                distribution_payload,
            )
        except Exception as exc:
            app.state.logger.warning(
                "Runtime artifact receive failed for sender %s: %s",
                metadata["sender_device_key_id_hex"][:8],
                exc,
            )
            continue
        Provisioning.mark_redistribution_receipt(
            root_dir,
            participant_hex,
            team_id=bytes.fromhex(received["team_id_hex"]),
            sender_device_key_id=bytes.fromhex(received["sender_device_key_id_hex"]),
            sender_chain_id=bytes.fromhex(received["sender_chain_id_hex"]),
            target_device_key_id=bytes.fromhex(received["target_device_key_id_hex"]),
        )


def _refresh_local_runtime_signal(app: FastAPI, session_hex: str):
    session_info = app.state.watched_sessions.get(session_hex)
    if session_info is None:
        return
    if session_info.get("watch_self_only"):
        return
    signals, etag = app.state.backend.get_local_signal(session_hex)
    if signals is None or etag is None:
        return
    if etag == session_info.get("self_signal_etag"):
        return
    ss_session = app.state.backend._lookup_session(session_hex)
    try:
        team_id, self_in_team = Provisioning._team_row(
            app.state.backend.root_dir,
            ss_session.participant_id.hex(),
            ss_session.team_name,
        )
    except Exception:
        return
    _process_runtime_inbox_from_member(
        app,
        session_hex,
        self_in_team.hex(),
        use_local_bucket=True,
    )
    session_info["self_signal_etag"] = etag


def _refresh_note_to_self_self_signal(app: FastAPI, session_hex: str):
    session_info = app.state.watched_sessions.get(session_hex)
    if session_info is None or not session_info.get("watch_self_only"):
        return

    signals, etag = app.state.backend.get_local_signal(session_hex)
    if signals is None or etag is None:
        return
    if etag == session_info.get("self_signal_etag"):
        return

    berth_id_hex = session_info["berth_id_hex"]
    current_count = int(signals.get(berth_id_hex, 0))
    previous_count = int(session_info.get("self_signal_count", 0))
    app.state.self_signal_counts[berth_id_hex] = current_count
    session_info["self_signal_etag"] = etag
    session_info["self_signal_count"] = current_count

    ignored_count = session_info.get("ignore_self_signal_count")
    if ignored_count is not None and current_count >= ignored_count:
        session_info["ignore_self_signal_count"] = None
        return
    if current_count > previous_count:
        _pulse_berth_event(app, berth_id_hex)


def _watcher_pass(app: FastAPI):
    """Single poll round: refresh peer lists and check all peer signals for changes.

    Called from both the polling loop (_peer_watcher_loop) and the ntfy push
    listener (_ntfy_listener_loop) so that a push event triggers an immediate
    signal check without waiting for the next poll interval.
    """
    logger = app.state.logger

    # Refresh peer lists for all active sessions before polling signals.
    for session_hex in list(app.state.watched_sessions):
        _refresh_session_peers(app, session_hex)
        if _run_runtime_reconciliation_for_session(app, session_hex):
            berth_id_hex = app.state.watched_sessions.get(session_hex, {}).get("berth_id_hex")
            if berth_id_hex:
                _pulse_berth_event(app, berth_id_hex)
        _refresh_local_runtime_signal(app, session_hex)
        _refresh_note_to_self_self_signal(app, session_hex)

    peers = getattr(app.state, "watched_peers", {})
    # Track which berths have already received a push notification this round
    # so we send at most one notification per berth regardless of how many
    # sessions or peers triggered the change.
    notified_berths: set = set()
    for key, state in list(peers.items()):
        session_hex, member_id_hex = key
        berth_id_hex = state.get("berth_id_hex")
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
            for bid, count in signals.items():
                if bid == "version":
                    continue
                if count > prev.get(bid, 0):
                    # Key by the watching berth (berth_id_hex) so that
                    # /notifications/watch _check() can find it by session.
                    peer_key = (berth_id_hex, member_id_hex)
                    if count > app.state.peer_counts.get(peer_key, 0):
                        app.state.peer_counts[peer_key] = count
                    changed = True
                    logger.info(
                        f"Peer {member_id_hex[:8]} berth {bid[:8]}: count={count}"
                    )

            state["etag"] = etag
            state["signals"] = {k: v for k, v in signals.items() if k != "version"}

            if changed and berth_id_hex:
                _process_runtime_inbox_from_member(
                    app,
                    session_hex,
                    member_id_hex,
                    use_local_bucket=False,
                )
                _pulse_berth_event(app, berth_id_hex)
                if berth_id_hex not in notified_berths:
                    notified_berths.add(berth_id_hex)
                    _send_peer_notification(app, session_hex, berth_id_hex, logger)

        except SmallSeaNotFoundExn:
            # Session expired — remove it and all its peers from the watcher.
            app.state.watched_sessions.pop(session_hex, None)
            stale_keys = [k for k in app.state.watched_peers if k[0] == session_hex]
            for k in stale_keys:
                app.state.watched_peers.pop(k, None)
            logger.info(f"Removed expired session {session_hex[:8]} from watcher")
        except Exception as exc:
            logger.warning(f"Peer watcher error for {member_id_hex[:8]}: {exc}")


async def _peer_watcher_loop(app: FastAPI):
    """Background task: poll registered peers' signal files for changes.

    On each round, re-reads the peer list for every active session so that
    membership changes (new or removed peers) are picked up automatically.
    New peers cause an immediate event pulse so any waiting long-pollers wake
    and can re-enumerate the current member list.

    When a peer's signal count increases, updates peer_counts and pulses the
    berth event to wake /notifications/watch waiters.

    The first pass runs immediately (no initial sleep) so that peer_counts is
    populated quickly after startup rather than after the full interval.
    """
    first_pass = True
    while True:
        if not first_pass:
            await asyncio.sleep(getattr(app.state, "watcher_interval", PEER_WATCHER_INTERVAL))
        first_pass = False
        _watcher_pass(app)


async def _ntfy_listener_loop(app: FastAPI, ntfy_url: str, berth_id_hex: str):
    """Async task: subscribe to ntfy SSE for a berth and trigger watcher passes on push.

    Reconnects automatically on error with a 5-second back-off. Runs until
    cancelled (e.g. on Hub shutdown).
    """
    from small_sea_hub.adapters.ntfy import SmallSeaNtfyAdapter

    logger = app.state.logger
    topic = f"ss-{berth_id_hex}"
    logger.info(f"ntfy listener starting: {ntfy_url}/{topic}")
    adapter = SmallSeaNtfyAdapter(ntfy_url, topic)
    while True:
        try:
            async for _msg in adapter.subscribe():
                logger.debug(f"ntfy push received on {topic}, running watcher pass")
                _watcher_pass(app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"ntfy listener error ({topic}), reconnecting in 5s: {exc}")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "backend"):
        settings = Settings()
        app.state.backend = SmallSeaBackend(
            root_dir=settings.get_root_dir(),
            auto_approve_sessions=settings.auto_approve_sessions,
            sandbox_mode=settings.sandbox_mode,
            log_level=settings.log_level,
        )
    if not hasattr(app.state, "watched_sessions"):
        app.state.watched_sessions = {}   # session_hex → {berth_id_hex, team_db_path}
    if not hasattr(app.state, "watched_peers"):
        app.state.watched_peers = {}      # (session_hex, member_id_hex) → state
    if not hasattr(app.state, "peer_counts"):
        app.state.peer_counts = {}        # (berth_id_hex, member_id_hex) → int
    if not hasattr(app.state, "self_signal_counts"):
        app.state.self_signal_counts = {}  # berth_id_hex → int
    if not hasattr(app.state, "peer_signal_events"):
        app.state.peer_signal_events = {}  # berth_id_hex → asyncio.Event
    if not hasattr(app.state, "watcher_interval"):
        app.state.watcher_interval = Settings().watcher_interval
    if not hasattr(app.state, "ntfy_listener_tasks"):
        app.state.ntfy_listener_tasks = {}  # berth_id_hex → asyncio.Task
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
    for task in app.state.ntfy_listener_tasks.values():
        task.cancel()
    logger.info("Shutting down...")


app = FastAPI(lifespan=lifespan)


@app.exception_handler(SmallSeaNotFoundExn)
async def not_found_handler(request: Request, exc: SmallSeaNotFoundExn):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
async def hub_status(request: Request):
    backend = app.state.backend
    return _templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            "pending": backend.list_pending_sessions_safe(),
            "active_count": backend.count_active_sessions(),
        },
    )


@app.post("/sessions/{pending_id}/resend-notification")
async def resend_notification(pending_id: str):
    """Re-fire the OS notification for a pending session request.

    Safe to expose without authentication: the PIN never appears in the
    response. Any process on localhost can call this, but the worst outcome
    is the user seeing a repeated notification — not a PIN leak.
    """
    app.state.backend.resend_notification(pending_id)
    return {"ok": True}


# ---- Authorization ----


def _require_session(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    return authorization[7:]


def _require_bootstrap_session(authorization: str = Header(...)):
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
    mode: str = "encrypted"


def _maybe_start_ntfy_listener(app: FastAPI, ss_session, berth_id_hex: str):
    """Start an ntfy SSE listener task for a berth if ntfy is configured and not already running."""
    ntfy_listener_tasks = getattr(app.state, "ntfy_listener_tasks", None)
    if ntfy_listener_tasks is None:
        return
    if berth_id_hex in ntfy_listener_tasks:
        return  # already running for this berth
    try:
        adapter = app.state.backend._make_notification_adapter(ss_session)
        ntfy_url = adapter.base_url
    except Exception:
        return  # no ntfy configured — polling only
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # not in an async context (e.g. tests that skip lifespan)
    task = loop.create_task(_ntfy_listener_loop(app, ntfy_url, berth_id_hex))
    ntfy_listener_tasks[berth_id_hex] = task
    app.state.logger.info(f"ntfy listener task started for berth {berth_id_hex[:8]}")


def _register_session_peers(session_hex: str):
    """Register a session with the watcher after it is confirmed.

    Records the team DB path and berth so the watcher can re-read the peer
    list on every round, picking up membership changes automatically.
    No-ops silently if the watcher state has not been initialized yet (e.g.
    in tests that do not run the full lifespan).
    """
    watched_sessions = getattr(app.state, "watched_sessions", None)
    if watched_sessions is None:
        return  # Watcher state not yet initialized
    try:
        ss_session = app.state.backend._lookup_session(session_hex)
        berth_id_hex = ss_session.berth_id.hex()
        if ss_session.team_name == "NoteToSelf":
            signals, etag = app.state.backend.get_local_signal(session_hex)
            current_count = 0
            if signals is not None:
                current_count = int(signals.get(berth_id_hex, 0))
            app.state.self_signal_counts[berth_id_hex] = current_count
            watched_sessions[session_hex] = {
                "berth_id_hex": berth_id_hex,
                "team_db_path": None,
                "team_db_revision": None,
                "self_signal_etag": etag,
                "self_signal_count": current_count,
                "ignore_self_signal_count": None,
                "watch_self_only": True,
            }
            app.state.peer_signal_events.setdefault(berth_id_hex, asyncio.Event())
            _maybe_start_ntfy_listener(app, ss_session, berth_id_hex)
            return
        team_db_path = str(
            ss_session.participant_path / ss_session.team_name / "Sync" / "core.db"
        )
        watched_sessions[session_hex] = {
            "berth_id_hex": berth_id_hex,
            "team_db_path": team_db_path,
            "team_db_revision": None,
            "self_signal_etag": None,
            "self_in_team": Provisioning._team_row(
                app.state.backend.root_dir,
                ss_session.participant_id.hex(),
                ss_session.team_name,
            )[1].hex(),
            "watch_self_only": False,
        }
        app.state.peer_signal_events.setdefault(berth_id_hex, asyncio.Event())
        # Do an immediate peer refresh so watched_peers is populated now rather
        # than waiting for the first watcher round.
        _refresh_session_peers(app, session_hex)
        # Start an ntfy listener for this berth if one isn't already running.
        _maybe_start_ntfy_listener(app, ss_session, berth_id_hex)
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
        req.participant, req.app, req.team, effective_client, mode=req.mode
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


class BootstrapSessionCreateReq(pydantic.BaseModel):
    protocol: str
    url: str
    bucket: str
    expires_at: Optional[str] = None


@app.post("/bootstrap/sessions")
async def create_bootstrap_session(req: BootstrapSessionCreateReq):
    token = app.state.backend.create_bootstrap_session(
        protocol=req.protocol,
        url=req.url,
        bucket=req.bucket,
        expires_at_iso=req.expires_at,
    )
    return {"token": token.hex()}


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

    Allows apps to discover their berth_id and team context from a session
    token, without reading the SmallSeaCollectiveCore SQLite database directly.
    """
    ss_session = app.state.backend._lookup_session(session_hex)
    return {
        "participant_hex": ss_session.participant_id.hex(),
        "team_name": ss_session.team_name,
        "app_name": ss_session.app_name,
        "berth_id": ss_session.berth_id.hex(),
        "client": ss_session.client,
        "mode": ss_session.mode,
    }


@app.get("/session/peers")
async def session_peers(session_hex: str = Depends(_require_session)):
    """Return peers visible to the current team session."""
    small_sea = app.state.backend
    ss_session = small_sea._lookup_session(session_hex)
    berth_id_hex = ss_session.berth_id.hex()
    peer_counts = getattr(app.state, "peer_counts", {})
    peers = []
    for peer in small_sea.list_peers(session_hex):
        member_id_hex = peer["member_id"]
        name = peer.get("name")
        peers.append(
            {
                "member_id": member_id_hex,
                "name": name,
                "label": name or f"Teammate {member_id_hex[:8]}...",
                "signal_count": peer_counts.get((berth_id_hex, member_id_hex), 0),
            }
        )
    return {"peers": peers}


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
            new_count = small_sea._bump_signal(session_hex)
        except Exception as exc:
            new_count = None
            if _logger:
                _logger.warning(f"_bump_signal failed: {exc}")
        # Pulse the local berth event so other sessions on this berth
        # (e.g. a second browser tab) are also notified.
        try:
            ss_session = small_sea._lookup_session(session_hex)
            if (
                new_count is not None
                and ss_session.team_name == "NoteToSelf"
                and session_hex in getattr(app.state, "watched_sessions", {})
            ):
                app.state.watched_sessions[session_hex]["ignore_self_signal_count"] = new_count
            _pulse_berth_event(app, ss_session.berth_id.hex())
        except Exception as exc:
            if _logger:
                _logger.warning(f"local berth pulse failed: {exc}")
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


@app.get("/bootstrap/cloud_file")
async def bootstrap_cloud_file(
    path: str,
    session_hex: str = Depends(_require_bootstrap_session),
):
    import base64

    ok, data, etag = app.state.backend.bootstrap_cloud_file(session_hex, path)
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
    return {"version": signals.get("version", 1), "berths": {
        k: v for k, v in signals.items() if k != "version"
    }, "etag": etag}


# ---- Notifications ----


class WatchNotificationsReq(pydantic.BaseModel):
    known: dict[str, int] = {}  # member_id_hex → last known count
    known_self_count: Optional[int] = None
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
    berth_id_hex = ss_session.berth_id.hex()

    def _check():
        updated = {}
        for member_id_hex, known_count in req.known.items():
            current = app.state.peer_counts.get((berth_id_hex, member_id_hex), 0)
            if current > known_count:
                updated[member_id_hex] = current
        result = {"updated": updated}
        if req.known_self_count is not None:
            self_counts = getattr(app.state, "self_signal_counts", {})
            current_self = self_counts.get(berth_id_hex, 0)
            if current_self > req.known_self_count:
                result["self_updated_count"] = current_self
        return result

    # Return immediately if we already know about newer data.
    result = _check()
    if result.get("updated") or "self_updated_count" in result:
        return result

    # Grab the current event before sleeping — the watcher may replace it
    # while we wait, but we hold the reference so set() still wakes us.
    if not hasattr(app.state, "peer_signal_events"):
        app.state.peer_signal_events = {}
    event = app.state.peer_signal_events.setdefault(berth_id_hex, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=req.timeout)
    except asyncio.TimeoutError:
        return {"updated": {}}

    return _check()


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
