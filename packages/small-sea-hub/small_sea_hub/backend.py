# Top Matter

import logging
import os
import pathlib
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple

import yaml

import plyer
from sqlalchemy import (Column, DateTime, Integer, LargeBinary, String,
                        create_engine, text)
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()

from botocore.exceptions import ClientError
from small_sea_hub.adapters import (SmallSeaDropboxAdapter,
                                    SmallSeaGDriveAdapter, SmallSeaGotifyAdapter,
                                    SmallSeaNtfyAdapter, SmallSeaS3Adapter,
                                    SmallSeaStorageAdapter)
from small_sea_hub.adapters.oauth import (is_token_expired,
                                          refresh_dropbox_token,
                                          refresh_google_token)
from small_sea_hub.crypto import (commit_encrypted_upload,
                                  decrypt_group_payload,
                                  prepare_encrypted_upload)
from small_sea_note_to_self.db import attached_note_to_self_connection
from small_sea_note_to_self.ids import uuid7
from wrasse_trust.transport import (
    MemberTransportAnnouncement,
    TransportEndpoint,
    key_certificate_from_team_db_record,
    select_effective_member_transport,
)


class SmallSeaBackendExn(Exception):
    pass


class SmallSeaNotFoundExn(SmallSeaBackendExn):
    pass


# ---- SQLAlchemy models ----
# Hub-local session table


class SmallSeaSession(Base):
    __tablename__ = "session"

    id = Column(LargeBinary, primary_key=True)
    token = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    duration_sec = Column(Integer)
    participant_id = Column(LargeBinary, nullable=False)
    team_id = Column(LargeBinary, nullable=False)
    team_name = Column(String, nullable=False)
    app_id = Column(LargeBinary, nullable=False)
    app_name = Column(String, nullable=False)
    berth_id = Column(LargeBinary, nullable=False)
    mode = Column(String, nullable=False)
    client = Column(String, nullable=False)

    def __repr__(self):
        return f"<Session(id='{self.id.hex()}', token='{self.token.hex()}')>"


class PendingSession(Base):
    __tablename__ = "pending_session"

    id = Column(LargeBinary, primary_key=True)
    participant_hex = Column(String, nullable=False)
    team_name = Column(String, nullable=False)
    app_name = Column(String, nullable=False)
    client_name = Column(String, nullable=False)
    mode = Column(String, nullable=False)
    pin = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)


class BootstrapSession(Base):
    __tablename__ = "bootstrap_session"

    id = Column(LargeBinary, primary_key=True)
    token = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    bucket = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)


# Per-user core.db models (duplicated in team manager — the DB is the contract)


class Nickname(Base):
    __tablename__ = "nickname"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)


class Team(Base):
    __tablename__ = "team"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)


class App(Base):
    __tablename__ = "app"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)


class TeamAppBerth(Base):
    __tablename__ = "team_app_berth"

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=True)  # absent in team DBs (table is team-scoped)
    app_id = Column(LargeBinary, nullable=False)


@dataclass
class CloudStorageRecord:
    id: bytes
    protocol: str
    url: str
    access_key: str | None
    secret_key: str | None
    client_id: str | None
    client_secret: str | None
    refresh_token: str | None
    access_token: str | None
    token_expiry: str | None
    path_metadata: str | None


@dataclass
class NotificationServiceRecord:
    id: bytes
    protocol: str
    url: str
    access_key: str | None
    access_token: str | None


class SmallSeaBackend:
    """
    Hub backend — session management, cloud storage, sync.

    Participant/user/team provisioning has moved to the
    small-sea-manager package (provisioning.py).
    """

    hub_schema_version: int = 50

    def __init__(self, root_dir, auto_approve_sessions: bool = False,
                 sandbox_mode: bool = False, log_level: str = "INFO"):
        self.root_dir = pathlib.Path(root_dir)
        self.auto_approve_sessions = auto_approve_sessions
        self.sandbox_mode = sandbox_mode
        os.makedirs(self.root_dir, exist_ok=True)
        self.path_local_db = self.root_dir / "small_sea_collective_local.db"
        os.makedirs(self.root_dir / "Logging", exist_ok=True)
        log_path = self.root_dir / "Logging" / "small_sea_hub.log"
        console_level = getattr(logging, log_level.upper(), logging.INFO)
        self.logger = setup_logging(log_file=log_path, console_level=console_level)
        self._initialize_small_sea_db()

    def _initialize_small_sea_db(self):
        try:
            conn = None
            conn = sqlite3.connect(self.path_local_db)
            cursor = conn.cursor()
            self._initialize_small_sea_schema(cursor)
            conn.commit()

        except sqlite3.Error as e:
            print(f"SQLite error occurred: '{e}'")

        finally:
            if conn is not None:
                conn.close()

    def _initialize_small_sea_schema(self, cursor):
        cursor.execute("PRAGMA user_version")
        user_version = cursor.fetchone()[0]

        if user_version == SmallSeaBackend.hub_schema_version:
            print("SmallSea local DB already initialized")
            return

        if user_version != 0 and user_version < SmallSeaBackend.hub_schema_version:
            if user_version <= 44:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pending_session (
                        id BLOB PRIMARY KEY,
                        participant_hex TEXT NOT NULL,
                        team_name TEXT NOT NULL,
                        app_name TEXT NOT NULL,
                        client_name TEXT NOT NULL,
                        pin TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                """)
                user_version = 45
                print("Hub DB migrated to v45.")

            if user_version == 45:
                cursor.execute(
                    "ALTER TABLE session ADD COLUMN team_name TEXT NOT NULL DEFAULT ''"
                )
                cursor.execute(
                    "ALTER TABLE session ADD COLUMN app_name TEXT NOT NULL DEFAULT ''"
                )
                user_version = 46
                print("Hub DB migrated to v46.")

            if user_version == 46:
                # team_signing_key table added to NoteToSelf schema;
                # Hub doesn't read it, just needs to accept the version bump.
                user_version = 47
                print("Hub DB migrated to v47.")

            if user_version == 47:
                cursor.execute(
                    "ALTER TABLE session ADD COLUMN mode TEXT NOT NULL DEFAULT 'passthrough'"
                )
                user_version = 48
                print("Hub DB migrated to v48.")

            if user_version == 48:
                cursor.execute(
                    "ALTER TABLE pending_session ADD COLUMN mode TEXT NOT NULL DEFAULT 'encrypted'"
                )
                user_version = 49
                print("Hub DB migrated to v49.")

            if user_version == 49:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bootstrap_session (
                        id BLOB PRIMARY KEY,
                        token BLOB NOT NULL,
                        protocol TEXT NOT NULL,
                        url TEXT NOT NULL,
                        bucket TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                """)
                user_version = 50
                print("Hub DB migrated to v50.")

            cursor.execute(
                f"PRAGMA user_version = {SmallSeaBackend.hub_schema_version}"
            )
            return

        if user_version > SmallSeaBackend.hub_schema_version:
            print("TODO: DB FROM THE FUTURE!")
            raise NotImplementedError()

        schema_path = pathlib.Path(__file__).parent
        schema_path = schema_path / "sql" / "hub_local_schema.sql"

        with open(schema_path, "r") as f:
            schema_script = f.read()
        cursor.executescript(schema_script)

        cursor.execute(f"PRAGMA user_version = {SmallSeaBackend.hub_schema_version}")
        print("Hub DB schema initialized successfully.")

    # ---- Session management ----

    def _find_participant(self, nickname):
        """Return list of (participant_dir, engine) for participants matching nickname.

        Accepts either a human-readable nickname ("Alice") or the participant's
        hex directory name. Matching by directory name is an exact match and
        skips the DB query, so callers that have the hex (e.g. TeamManager) do
        not need to know the human-readable name.
        """
        matching = []
        participants_dir = self.root_dir / "Participants"
        for d in participants_dir.iterdir():
            if not d.is_dir():
                continue
            # Direct match by participant directory name (hex ID).
            if d.name == nickname:
                note_to_self_db_path = d / "NoteToSelf" / "Sync" / "core.db"
                engine = create_engine(f"sqlite:///{note_to_self_db_path}")
                matching.append((d, engine))
                continue
            note_to_self_db_path = d / "NoteToSelf" / "Sync" / "core.db"
            engine = create_engine(f"sqlite:///{note_to_self_db_path}")
            with Session(engine) as sess:
                results = sess.query(Nickname).filter(Nickname.name == nickname).all()
                if results:
                    matching.append((d, engine))
        return matching

    def _resolve_berth(self, participant_dir, team_name, app_name):
        """Return (team_id, app_id, berth_id) as bytes.

        The team row is always read from the participant's NoteToSelf DB.
        For NoteToSelf, app and berth are also in that DB.
        For all other teams, app and berth are in the team DB at
        Participants/{hex}/{team_name}/Sync/core.db.

        Uses raw SQL for the app/berth lookup to stay compatible with both
        the NoteToSelf schema (team_app_berth has team_id) and the team DB
        schema (team_app_berth intentionally omits team_id).
        """
        note_to_self_db = str(participant_dir / "NoteToSelf" / "Sync" / "core.db")
        conn = sqlite3.connect(note_to_self_db)
        try:
            row = conn.execute(
                "SELECT id FROM team WHERE name = ?", (team_name,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise SmallSeaNotFoundExn(f"Team '{team_name}' not found")
        team_id = row[0]

        if team_name == "NoteToSelf":
            berth_db = note_to_self_db
        else:
            berth_db = str(participant_dir / team_name / "Sync" / "core.db")

        conn = sqlite3.connect(berth_db)
        try:
            app_row = conn.execute(
                "SELECT id FROM app WHERE name = ?", (app_name,)
            ).fetchone()
            if app_row is None:
                raise SmallSeaNotFoundExn(f"App '{app_name}' not found in '{team_name}'")
            app_id = app_row[0]

            berth_row = conn.execute(
                "SELECT id FROM team_app_berth WHERE app_id = ?", (app_id,)
            ).fetchone()
            if berth_row is None:
                raise SmallSeaNotFoundExn(
                    f"No berth for app '{app_name}' in team '{team_name}'"
                )
            berth_id = berth_row[0]
        finally:
            conn.close()

        return team_id, app_id, berth_id

    @staticmethod
    def _normalize_mode(mode: Optional[str]) -> str:
        if mode is None:
            return "encrypted"
        if mode not in ("encrypted", "passthrough"):
            raise SmallSeaBackendExn(f"Unknown session mode: {mode}")
        return mode

    @staticmethod
    def _mode_warning_marker(mode: str) -> str:
        return "[unsafe] " if mode == "passthrough" else ""

    def request_session(self, nickname, app, team, client, mode: Optional[str] = None):
        """Step 1 of session approval: generate PIN, write pending row, send OS notification.

        Returns (pending_id_hex, pin). For normal clients, pin should not be shown to
        the requester — it arrives via OS notification. For 'Smoke Tests', the caller
        may use the returned pin directly to call confirm_session.
        """
        matching = self._find_participant(nickname)
        if not matching:
            raise SmallSeaNotFoundExn()
        mode = self._normalize_mode(mode)

        participant_dir, engine = matching[0]
        participant_hex = participant_dir.absolute().name
        self._resolve_berth(participant_dir, team, app)  # validate existence

        pin = str(secrets.randbelow(1000)).zfill(3)

        if client != "Smoke Tests":
            self._send_os_notification(client, pin, team, app, mode)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=5)
        pending_id = uuid7()

        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            pending = PendingSession(
                id=pending_id,
                participant_hex=participant_hex,
                team_name=team,
                app_name=app,
                client_name=client,
                mode=mode,
                pin=pin,
                created_at=now.isoformat(),
                expires_at=expires_at.isoformat(),
            )
            sess.add(pending)
            sess.commit()

        return pending_id.hex(), pin

    def confirm_session(self, pending_id_hex, pin) -> bytes:
        """Step 2 of session approval: validate PIN and TTL, create real session.

        Returns the session token (bytes) on success.
        Raises SmallSeaBackendExn on invalid or expired PIN.
        """
        pending_id = bytes.fromhex(pending_id_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")

        with Session(engine_local) as sess:
            pending = (
                sess.query(PendingSession)
                .filter(PendingSession.id == pending_id)
                .first()
            )
            if pending is None:
                raise SmallSeaNotFoundExn("No pending session found")

            now = datetime.now(timezone.utc)
            expires_at = datetime.fromisoformat(pending.expires_at)
            if now > expires_at:
                sess.delete(pending)
                sess.commit()
                raise SmallSeaBackendExn("PIN expired")

            if pending.pin != pin:
                raise SmallSeaBackendExn("Invalid PIN")

            participant_dir = (
                self.root_dir / "Participants" / pending.participant_hex
            )
            team_id, app_id, berth_id = self._resolve_berth(
                participant_dir, pending.team_name, pending.app_name
            )

            token = secrets.token_bytes(32)
            ss_session = SmallSeaSession(
                id=uuid7(),
                token=token,
                duration_sec=None,
                participant_id=bytes.fromhex(pending.participant_hex),
                team_id=team_id,
                team_name=pending.team_name,
                app_id=app_id,
                app_name=pending.app_name,
                berth_id=berth_id,
                mode=pending.mode,
                client=pending.client_name,
            )
            sess.add(ss_session)
            sess.delete(pending)
            sess.commit()

        return token

    def _send_os_notification(
        self, client: str, pin: str, team: str, app: str, mode: str
    ) -> None:
        """Fire an OS notification carrying the session PIN.

        Tries plyer first (cross-platform), then osascript (macOS fallback).
        Logs a warning if both fail — the PIN will then only be visible in the
        Hub status UI and the sandbox dashboard (if running).

        The PIN must travel out-of-band from the requesting client. This method
        must never return or log the PIN in a way that the requesting process
        could intercept.
        """
        title = "Small Sea Access Request"
        marker = self._mode_warning_marker(mode)
        message = f'PIN: {pin} — {marker}"{client}" requesting access to {team} → {app}'
        try:
            plyer.notification.notify(
                title=title,
                message=message,
                app_name="Small Sea Hub",
                timeout=10,
            )
            return
        except Exception:
            pass
        # plyer failed (common on macOS when the process lacks notification
        # permission). Try osascript directly.
        try:
            import subprocess as _sp
            _msg = (
                f'PIN: {pin} — {marker}\\"{client}\\" requesting access to {team} → {app}'
            )
            _sp.run(
                ["osascript", "-e",
                 f'display notification "{_msg}" with title "{title}"'],
                check=True, timeout=3,
            )
            return
        except Exception:
            pass
        self.logger.warning(
            "OS notification failed — PIN for '%s' session request: %s", client, pin
        )

    def resend_notification(self, pending_id_hex: str) -> None:
        """Re-fire the OS notification for a pending session request.

        Safe to expose over unauthenticated HTTP because the PIN is never
        returned in the response — it only travels via the OS notification.
        The worst a caller can do is annoy the user with repeat notifications.

        Raises SmallSeaNotFoundExn if the pending session does not exist or
        has already expired.
        """
        pending_id = bytes.fromhex(pending_id_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            pending = (
                sess.query(PendingSession)
                .filter(PendingSession.id == pending_id)
                .first()
            )
            if pending is None:
                raise SmallSeaNotFoundExn("No pending session found")
            now = datetime.now(timezone.utc)
            if now > datetime.fromisoformat(pending.expires_at):
                sess.delete(pending)
                sess.commit()
                raise SmallSeaNotFoundExn("Pending session has expired")
            self._send_os_notification(
                pending.client_name,
                pending.pin,
                pending.team_name,
                pending.app_name,
                pending.mode,
            )

    def list_pending_sessions_safe(self) -> list[dict]:
        """Return pending sessions WITHOUT PINs, for the Hub status UI.

        Safe to expose over unauthenticated HTTP. The PIN field is intentionally
        absent — it must only travel via OS notification, never via HTTP response.
        Team and app names are also excluded: they are private to participants
        and must not be readable by any process that can reach localhost.
        """
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            rows = sess.query(PendingSession).all()
            return [
                {
                    "pending_id": r.id.hex(),
                    "client_name": r.client_name,
                    "mode": r.mode,
                    "mode_warning": self._mode_warning_marker(r.mode).strip(),
                    "expires_at": r.expires_at,
                }
                for r in rows
            ]

    def count_active_sessions(self) -> int:
        """Return the number of currently active (confirmed) sessions."""
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            return sess.query(SmallSeaSession).count()

    def list_pending_sessions(self) -> list[dict]:
        """Return all pending sessions with their PINs.

        Only for sandbox use. Do not expose in production — pins are secrets.
        """
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            rows = sess.query(PendingSession).all()
            return [
                {
                    "pending_id": r.id.hex(),
                    "participant_hex": r.participant_hex,
                    "team_name": r.team_name,
                    "app_name": r.app_name,
                    "client_name": r.client_name,
                    "mode": r.mode,
                    "mode_warning": self._mode_warning_marker(r.mode).strip(),
                    "pin": r.pin,
                    "expires_at": r.expires_at,
                }
                for r in rows
            ]

    def open_session(
        self, nickname, app, team, client, mode: Optional[str] = None
    ) -> bytes:
        """Smoke-test shortcut: request + auto-confirm in one call.

        Only for use with client='Smoke Tests'. Real clients use the
        request_session / confirm_session two-step flow via the HTTP API.
        """
        assert client == "Smoke Tests", "open_session is only for smoke tests"
        pending_id_hex, pin = self.request_session(nickname, app, team, client, mode=mode)
        return self.confirm_session(pending_id_hex, pin)

    def _lookup_session(self, session_hex):
        session_token = bytes.fromhex(session_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as session:
            ss_session = (
                session.query(SmallSeaSession)
                .filter(SmallSeaSession.token == session_token)
                .first()
            )
        if ss_session is None:
            raise SmallSeaNotFoundExn(f"Session not found: {session_hex[:8]}")
        ss_session.participant_path = (
            self.root_dir / "Participants" / ss_session.participant_id.hex()
        )
        return ss_session

    def create_bootstrap_session(
        self,
        *,
        protocol: str,
        url: str,
        bucket: str,
        expires_at_iso: str | None = None,
    ) -> bytes:
        if protocol != "s3":
            raise SmallSeaBackendExn(
                f"Unsupported bootstrap protocol: {protocol}"
            )

        now = datetime.now(timezone.utc)
        if expires_at_iso is None:
            expires_at = now + timedelta(minutes=5)
        else:
            expires_at = datetime.fromisoformat(expires_at_iso)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                raise SmallSeaBackendExn("Bootstrap session expiry must be in the future")

        token = secrets.token_bytes(32)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as sess:
            sess.add(
                BootstrapSession(
                    id=uuid7(),
                    token=token,
                    protocol=protocol,
                    url=url,
                    bucket=bucket,
                    created_at=now.isoformat(),
                    expires_at=expires_at.isoformat(),
                )
            )
            sess.commit()
        return token

    def _lookup_bootstrap_session(self, token_hex: str) -> BootstrapSession:
        session_token = bytes.fromhex(token_hex)
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as session:
            bootstrap = (
                session.query(BootstrapSession)
                .filter(BootstrapSession.token == session_token)
                .first()
            )
            if bootstrap is None:
                raise SmallSeaNotFoundExn(f"Bootstrap session not found: {token_hex[:8]}")
            now = datetime.now(timezone.utc)
            expires_at = datetime.fromisoformat(bootstrap.expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                session.delete(bootstrap)
                session.commit()
                raise SmallSeaBackendExn("Bootstrap session expired")
            session.expunge(bootstrap)
        return bootstrap

    def all_session_tokens(self) -> list[str]:
        """Return hex tokens for all confirmed sessions."""
        engine_local = create_engine(f"sqlite:///{self.path_local_db}")
        with Session(engine_local) as session:
            rows = session.query(SmallSeaSession.token).all()
        return [row.token.hex() for row in rows]

    # ---- Cloud storage ----

    def add_cloud_location(
        self,
        session,
        protocol,
        url,
        access_key=None,
        secret_key=None,
        client_id=None,
        client_secret=None,
        refresh_token=None,
    ):
        known_protocols = ["s3", "webdav", "gdrive", "dropbox"]
        if protocol not in known_protocols:
            raise SmallSeaBackendExn(f"Unknown protocol: {protocol}")

        return self._add_cloud_location(
            session,
            protocol,
            url,
            access_key=access_key,
            secret_key=secret_key,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

    def _add_cloud_location(
        self,
        session_hex,
        scheme,
        location,
        access_key=None,
        secret_key=None,
        client_id=None,
        client_secret=None,
        refresh_token=None,
    ):
        ss_session = self._lookup_session(session_hex)

        # TODO: Should we check permissions? Probably.
        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as conn:
            cloud_id = uuid7()
            conn.execute(
                """
                INSERT INTO cloud_storage (id, protocol, url, client_id, path_metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cloud_id, scheme, location, client_id, None),
            )
            conn.execute(
                """
                INSERT INTO local.cloud_storage_credential (
                    cloud_storage_id, access_key, secret_key, client_secret,
                    refresh_token, access_token, token_expiry
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cloud_id,
                    access_key,
                    secret_key,
                    client_secret,
                    refresh_token,
                    None,
                    None,
                ),
            )
            conn.commit()

    def _get_cloud_link(self, ss_session: SmallSeaSession):
        # TODO: Should we check permissions? Probably.
        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as conn:
            row = conn.execute(
                """
                SELECT
                    cs.id,
                    cs.protocol,
                    cs.url,
                    csc.access_key,
                    csc.secret_key,
                    cs.client_id,
                    csc.client_secret,
                    csc.refresh_token,
                    csc.access_token,
                    csc.token_expiry,
                    cs.path_metadata
                FROM cloud_storage cs
                LEFT JOIN local.cloud_storage_credential csc
                  ON csc.cloud_storage_id = cs.id
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise SmallSeaNotFoundExn(
                "No cloud storage configured for this participant. "
                "Add a cloud storage account in the Manager before syncing."
            )
        return CloudStorageRecord(*row)

    def _make_storage_adapter(self, ss_session: SmallSeaSession):
        cloud = self._get_cloud_link(ss_session)

        if cloud.protocol == "s3":
            return self._make_s3_adapter(ss_session, cloud)
        elif cloud.protocol == "gdrive":
            return self._make_gdrive_adapter(ss_session, cloud)
        elif cloud.protocol == "dropbox":
            return self._make_dropbox_adapter(ss_session, cloud)
        else:
            raise SmallSeaBackendExn(f"Unsupported protocol: {cloud.protocol}")

    def _refresh_token_if_needed(self, ss_session, cloud):
        """Refresh OAuth token if expired, persisting the new token to the DB."""
        if not is_token_expired(cloud.token_expiry):
            return cloud.access_token

        if cloud.protocol == "gdrive":
            access_token, expiry = refresh_google_token(
                cloud.client_id, cloud.client_secret, cloud.refresh_token
            )
        elif cloud.protocol == "dropbox":
            access_token, expiry = refresh_dropbox_token(
                cloud.client_id, cloud.client_secret, cloud.refresh_token
            )
        else:
            raise SmallSeaBackendExn(f"No token refresh for protocol: {cloud.protocol}")

        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as conn:
            conn.execute(
                """
                UPDATE local.cloud_storage_credential
                SET access_token = ?, token_expiry = ?
                WHERE cloud_storage_id = ?
                """,
                (access_token, expiry, cloud.id),
            )
            conn.commit()

        return access_token

    def _make_s3_adapter(self, ss_session, cloud):
        import boto3
        from botocore.config import Config as BotoConfig

        bucket_name = f"ss-{ss_session.berth_id.hex()[:16]}"

        s3_client = boto3.client(
            "s3",
            endpoint_url=cloud.url,
            aws_access_key_id=cloud.access_key,
            aws_secret_access_key=cloud.secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )

        return SmallSeaS3Adapter(s3_client, bucket_name)

    def _make_gdrive_adapter(self, ss_session, cloud):
        import json as _json

        access_token = self._refresh_token_if_needed(ss_session, cloud)
        path_metadata = None
        if cloud.path_metadata:
            path_metadata = _json.loads(cloud.path_metadata)
        return SmallSeaGDriveAdapter(access_token, path_metadata=path_metadata)

    def _make_dropbox_adapter(self, ss_session, cloud):
        access_token = self._refresh_token_if_needed(ss_session, cloud)
        # Use member_id as folder prefix to avoid collisions in a shared Dropbox account.
        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as conn:
            row = conn.execute(
                "SELECT self_in_team FROM team WHERE name = ?",
                (ss_session.team_name,),
            ).fetchone()
        # self_in_team is a valid 16-byte UUID for real teams; placeholder b"0" for NoteToSelf
        if row and len(row[0]) == 16:
            folder_prefix = f"ss-{row[0].hex()[:16]}"
        else:
            folder_prefix = f"ss-{ss_session.participant_id.hex()[:16]}"
        return SmallSeaDropboxAdapter(access_token, folder_prefix=folder_prefix)

    def ensure_cloud_ready(self, session_hex):
        """Create and publish the session's cloud bucket (S3 only)."""
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        if hasattr(adapter, "ensure_bucket_public"):
            adapter.ensure_bucket_public()

    def download_from_peer(self, session_hex, member_id_hex, path):
        """Download a file from a peer's public cloud bucket via the Hub proxy."""
        ss_session = self._lookup_session(session_hex)
        ok, data, etag = self._download_peer_file(session_hex, member_id_hex, path)
        if ok and ss_session.mode == "encrypted":
            data = decrypt_group_payload(ss_session, data)
        return ok, data, etag

    def list_peers(self, session_hex):
        """Return peer details visible to the current team session.

        Prefers the team DB's member.display_name when present. Falls back to
        accepted invitation labels for older team DBs.
        """
        ss_session = self._lookup_session(session_hex)

        if ss_session.team_name == "NoteToSelf":
            return []

        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as nts_conn:
            self_row = nts_conn.execute(
                "SELECT self_in_team FROM team WHERE name = ?",
                (ss_session.team_name,),
            ).fetchone()
        self_in_team = self_row[0] if self_row is not None else None

        team_db_path = str(ss_session.participant_path / ss_session.team_name / "Sync" / "core.db")
        conn = sqlite3.connect(team_db_path)
        try:
            rows = conn.execute(
                "SELECT id, display_name FROM member WHERE id != ? ORDER BY id",
                (self_in_team,),
            ).fetchall()
            name_rows = conn.execute(
                "SELECT accepted_by, invitee_label "
                "FROM invitation "
                "WHERE accepted_by IS NOT NULL AND invitee_label IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()

        names_by_member = {}
        for member_id, invitee_label in name_rows:
            if member_id is None or not invitee_label:
                continue
            names_by_member[member_id.hex()] = invitee_label

        result = []
        for row in rows:
            member_id_hex = row[0].hex()
            display_name = row[1]
            result.append(
                {
                    "member_id": member_id_hex,
                    "name": display_name or names_by_member.get(member_id_hex),
                }
            )
        return result

    def upload_to_cloud(self, session_hex, path, data, expected_etag=None):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        if ss_session.mode == "encrypted":
            next_sender_key, data = prepare_encrypted_upload(ss_session, data)
        else:
            next_sender_key = None
        if expected_etag is not None:
            result = adapter.upload_if_match(path, data, expected_etag)
        else:
            result = adapter.upload_overwrite(path, data)
        if result[0] and next_sender_key is not None:
            commit_encrypted_upload(ss_session, next_sender_key)
        return result

    def download_from_cloud(self, session_hex, path):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        ok, data, etag = adapter.download(path)
        if ok and ss_session.mode == "encrypted":
            data = decrypt_group_payload(ss_session, data)
        return ok, data, etag

    def upload_runtime_artifact(self, session_hex, path, data, expected_etag=None):
        """Upload a runtime-control artifact without group-layer encryption."""
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        if expected_etag is not None:
            result = adapter.upload_if_match(path, data, expected_etag)
        else:
            result = adapter.upload_overwrite(path, data)
        if result[0]:
            self._bump_signal(session_hex)
        return result

    def download_runtime_artifact_from_cloud(self, session_hex, path):
        """Download a raw runtime-control artifact from this session's own bucket."""
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        return adapter.download(path)

    def download_runtime_artifact_from_peer(self, session_hex, member_id_hex, path):
        """Download a raw runtime-control artifact from a peer bucket."""
        return self._download_peer_file(session_hex, member_id_hex, path)

    def get_local_signal(self, session_hex):
        """Return (signals_dict, etag) from this session's own signals.yaml."""
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        ok, data, etag = adapter.download(self._SIGNAL_PATH)
        if not ok:
            return None, None
        signals = yaml.safe_load(data.decode("utf-8")) or {}
        if not isinstance(signals, dict):
            signals = {}
        return signals, etag

    # ---- Signal file ----

    _SIGNAL_PATH = "signals.yaml"
    _SIGNAL_MAX_RETRIES = 5

    def _bump_signal(self, session_hex):
        """Atomically increment the berth counter in this session's signals.yaml.

        Uses a CAS retry loop. On network failure after all retries, logs a
        warning and returns — teammates will catch up on the next push.
        Over-counting (from concurrent device pushes) is acceptable.
        """
        ss_session = self._lookup_session(session_hex)
        berth_id_hex = ss_session.berth_id.hex()
        adapter = self._make_storage_adapter(ss_session)

        for attempt in range(self._SIGNAL_MAX_RETRIES):
            ok, data, etag = adapter.download(self._SIGNAL_PATH)
            if ok:
                signals = yaml.safe_load(data.decode("utf-8")) or {}
                if not isinstance(signals, dict):
                    signals = {}
            else:
                signals = {}
                etag = None

            signals.setdefault("version", 1)
            signals[berth_id_hex] = signals.get(berth_id_hex, 0) + 1

            payload = yaml.dump(signals, default_flow_style=False).encode("utf-8")
            if etag is not None:
                upload_ok, _, msg = adapter.upload_if_match(self._SIGNAL_PATH, payload, etag)
            else:
                upload_ok, _, msg = adapter.upload_overwrite(self._SIGNAL_PATH, payload)

            if upload_ok:
                self._ntfy_publish_signal(ss_session, signals[berth_id_hex])
                return signals[berth_id_hex]
            # CAS conflict — re-read and retry

        self.logger.warning(
            f"_bump_signal: gave up after {self._SIGNAL_MAX_RETRIES} retries "
            f"(session {session_hex[:8]})"
        )
        return None

    def _ntfy_publish_signal(self, ss_session, count):
        """Fire-and-forget ntfy publish after a successful signal bump."""
        try:
            adapter = self._make_notification_adapter(ss_session)
        except SmallSeaNotFoundExn:
            return  # No notification service configured
        except Exception as exc:
            self.logger.debug(f"ntfy: no adapter ({exc})")
            return
        try:
            import json
            msg = json.dumps({"event": "push", "count": count})
            ok, _, err = adapter.publish(msg)
            if not ok:
                self.logger.debug(f"ntfy publish failed: {err}")
        except Exception as exc:
            self.logger.debug(f"ntfy publish error: {exc}")

    def get_peer_signal(self, session_hex, member_id_hex):
        """Return (signals_dict, etag) from a peer's public signals.yaml.

        Uses the same anonymous S3 read path as download_from_peer.
        Returns (None, None) if the file does not exist yet.
        """
        ok, data, etag = self._download_peer_file(
            session_hex, member_id_hex, self._SIGNAL_PATH
        )
        if not ok:
            return None, None
        signals = yaml.safe_load(data.decode("utf-8")) or {}
        if not isinstance(signals, dict):
            signals = {}
        return signals, etag

    def proxy_cloud_file(self, session_hex, protocol, url, bucket, path):
        """Download a file from an arbitrary cloud location using session credentials.

        Requires a NoteToSelf session. Used during invitation acceptance so Bob's
        Manager can clone Alice's team repo before any peer relationship exists.

        For S3: anonymous read (public bucket).
        For Dropbox: use the session's own Dropbox credentials to read from the
            shared app folder using `bucket` as the folder prefix.
        """
        ss_session = self._lookup_session(session_hex)
        if ss_session.team_name != "NoteToSelf":
            raise SmallSeaBackendExn("proxy_cloud_file requires a NoteToSelf session")

        if protocol == "s3":
            import boto3
            from botocore import UNSIGNED
            from botocore.config import Config as BotoConfig

            s3_client = boto3.client(
                "s3",
                endpoint_url=url,
                config=BotoConfig(signature_version=UNSIGNED),
                region_name="us-east-1",
            )
            try:
                response = s3_client.get_object(Bucket=bucket, Key=path)
                data_bytes = response["Body"].read()
                etag = response["ETag"].strip('"')
                return True, data_bytes, etag
            except ClientError as e:
                code = e.response["Error"]["Code"]
                return False, None, f"Proxy download failed: {code}"

        elif protocol == "dropbox":
            cloud = self._get_cloud_link(ss_session)
            access_token = self._refresh_token_if_needed(ss_session, cloud)
            adapter = SmallSeaDropboxAdapter(access_token, folder_prefix=bucket)
            return adapter.download(path)

        else:
            raise SmallSeaBackendExn(f"Unsupported proxy protocol: {protocol}")

    def bootstrap_cloud_file(self, token_hex: str, path: str):
        """Download a bootstrap file from a descriptor-scoped cloud location."""
        bootstrap = self._lookup_bootstrap_session(token_hex)
        if bootstrap.protocol != "s3":
            raise SmallSeaBackendExn(
                f"Unsupported bootstrap protocol: {bootstrap.protocol}"
            )

        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config as BotoConfig

        s3_client = boto3.client(
            "s3",
            endpoint_url=bootstrap.url,
            config=BotoConfig(signature_version=UNSIGNED),
            region_name="us-east-1",
        )
        try:
            response = s3_client.get_object(Bucket=bootstrap.bucket, Key=path)
            data_bytes = response["Body"].read()
            etag = response["ETag"].strip('"')
            return True, data_bytes, etag
        except ClientError as e:
            code = e.response["Error"]["Code"]
            return False, None, f"Bootstrap download failed: {code}"

    def _download_peer_file(self, session_hex, member_id_hex, path):
        """Core of download_from_peer, factored out for reuse."""
        ss_session = self._lookup_session(session_hex)

        if ss_session.team_name == "NoteToSelf":
            team_db_path = str(ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db")
        else:
            team_db_path = str(ss_session.participant_path / ss_session.team_name / "Sync" / "core.db")

        member_id = bytes.fromhex(member_id_hex)
        conn = sqlite3.connect(team_db_path)
        try:
            transport = self._effective_peer_transport(conn, ss_session.team_id, member_id)
        finally:
            conn.close()

        if transport is None:
            raise SmallSeaNotFoundExn(f"No peer found for member {member_id_hex}")

        protocol = transport.protocol
        url = transport.url
        bucket = transport.bucket

        if protocol == "s3":
            import boto3
            from botocore import UNSIGNED
            from botocore.config import Config as BotoConfig

            bucket_name = bucket or f"ss-{ss_session.berth_id.hex()[:16]}"
            s3_client = boto3.client(
                "s3",
                endpoint_url=url,
                config=BotoConfig(signature_version=UNSIGNED),
                region_name="us-east-1",
            )
            try:
                response = s3_client.get_object(Bucket=bucket_name, Key=path)
                data_bytes = response["Body"].read()
                etag = response["ETag"].strip('"')
                return True, data_bytes, etag
            except ClientError as e:
                code = e.response["Error"]["Code"]
                return False, None, f"Peer download failed: {code}"

        elif protocol == "dropbox":
            # Use own Dropbox credentials to access the shared account,
            # with the peer's folder prefix (stored in peer.bucket).
            cloud = self._get_cloud_link(ss_session)
            access_token = self._refresh_token_if_needed(ss_session, cloud)
            folder_prefix = bucket or ""
            adapter = SmallSeaDropboxAdapter(access_token, folder_prefix=folder_prefix)
            return adapter.download(path)

        else:
            raise SmallSeaBackendExn(f"Unsupported peer protocol: {protocol}")

    def _table_exists(self, conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _load_team_certificates(self, conn, team_id: bytes):
        if not self._table_exists(conn, "key_certificate"):
            return []
        rows = conn.execute(
            """
            SELECT cert_id, cert_type, subject_key_id, subject_public_key,
                   issuer_key_id, issuer_member_id, issued_at, claims, signature
            FROM key_certificate ORDER BY issued_at ASC
            """
        ).fetchall()
        return [
            key_certificate_from_team_db_record(
                team_id=team_id,
                cert_id=row[0],
                cert_type=row[1],
                subject_key_id=row[2],
                subject_public_key=row[3],
                issuer_key_id=row[4],
                issuer_member_id=row[5],
                issued_at=row[6],
                claims_json=row[7],
                signature=row[8],
            )
            for row in rows
        ]

    def _load_member_transport_announcements(self, conn):
        if not self._table_exists(conn, "member_transport_announcement"):
            return []
        rows = conn.execute(
            """
            SELECT announcement_id, member_id, protocol, url, bucket, announced_at,
                   signer_key_id, signature
            FROM member_transport_announcement
            ORDER BY announcement_id DESC
            """
        ).fetchall()
        return [
            MemberTransportAnnouncement(
                announcement_id=row[0],
                member_id=row[1],
                protocol=row[2],
                url=row[3],
                bucket=row[4],
                announced_at=row[5],
                signer_key_id=row[6],
                signature=row[7],
            )
            for row in rows
        ]

    def _device_public_keys_by_key_id(self, conn) -> dict[bytes, bytes]:
        if not self._table_exists(conn, "team_device"):
            return {}
        rows = conn.execute("SELECT device_key_id, public_key FROM team_device").fetchall()
        return {row[0]: row[1] for row in rows}

    def _legacy_transport_for_member(self, conn, member_id: bytes) -> TransportEndpoint | None:
        if not self._table_exists(conn, "team_device"):
            return None
        row = conn.execute(
            """
            SELECT protocol, url, bucket
            FROM team_device
            WHERE member_id = ?
              AND url IS NOT NULL
            ORDER BY created_at ASC, device_key_id ASC
            LIMIT 1
            """,
            (member_id,),
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return TransportEndpoint(protocol=row[0], url=row[1], bucket=row[2] or "")

    def _effective_peer_transport(self, conn, team_id: bytes, member_id: bytes) -> TransportEndpoint | None:
        selection = select_effective_member_transport(
            member_id=member_id,
            announcements=self._load_member_transport_announcements(conn),
            certs=self._load_team_certificates(conn, team_id),
            team_id=team_id,
            device_public_keys_by_key_id=self._device_public_keys_by_key_id(conn),
            legacy_fallback=self._legacy_transport_for_member(conn, member_id),
        )
        return selection.transport

    # ---- Notifications ----

    def _get_notification_service(self, ss_session):
        with attached_note_to_self_connection(
            self.root_dir, ss_session.participant_id.hex()
        ) as conn:
            row = conn.execute(
                """
                SELECT
                    ns.id,
                    ns.protocol,
                    ns.url,
                    nsc.access_key,
                    nsc.access_token
                FROM notification_service ns
                LEFT JOIN local.notification_service_credential nsc
                  ON nsc.notification_service_id = ns.id
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise SmallSeaNotFoundExn("No notification service configured")
        return NotificationServiceRecord(*row)

    def _make_notification_adapter(self, ss_session):
        import hashlib

        ns = self._get_notification_service(ss_session)
        if ns.protocol == "ntfy":
            topic = "ss-" + ss_session.berth_id.hex()
            return SmallSeaNtfyAdapter(ns.url, topic)
        elif ns.protocol == "gotify":
            return SmallSeaGotifyAdapter(
                ns.url, ns.access_key, client_token=ns.access_token
            )
        else:
            raise SmallSeaBackendExn(f"Unsupported notification protocol: {ns.protocol}")

    def send_notification(self, session_hex, message, title=None):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_notification_adapter(ss_session)
        return adapter.publish(message, title)

    def poll_notifications(self, session_hex, since=None, timeout=30):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_notification_adapter(ss_session)
        if since is None:
            since = "all"
        return adapter.poll(since, timeout)


def setup_logging(
    log_file="app.log",
    console_level=logging.INFO,
    file_level=logging.DEBUG,
    max_bytes=5 * 1024 * 1024,
    backup_count=3,
):
    # Create a root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Capture everything, handlers will filter

    # Clear existing handlers (important for e.g. pytest or re-imports)
    if logger.hasHandlers():
        logger.handlers.clear()

    # Formatters
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    # Attach handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
