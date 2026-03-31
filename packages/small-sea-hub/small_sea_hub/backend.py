# Top Matter

import logging
import os
import pathlib
import secrets
import sqlite3
import sys
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
from small_sea_manager.provisioning import uuid7


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
    station_id = Column(LargeBinary, nullable=False)
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
    pin = Column(String, nullable=False)
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


class TeamAppStation(Base):
    __tablename__ = "team_app_station"

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=True)  # absent in team DBs (table is team-scoped)
    app_id = Column(LargeBinary, nullable=False)


class CloudStorage(Base):
    __tablename__ = "cloud_storage"

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    # Credential storage will likely change (e.g. to a keyring or vault reference)
    access_key = Column(String, nullable=True)
    secret_key = Column(String, nullable=True)
    # OAuth fields for Google Drive / Dropbox
    client_id = Column(String, nullable=True)
    client_secret = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    token_expiry = Column(String, nullable=True)
    # JSON dict mapping path → provider-specific metadata (e.g. Google Drive file IDs)
    path_metadata = Column(String, nullable=True)

    def __repr__(self):
        return f"<CloudStorage(id='{self.id.hex()}')>"


class NotificationService(Base):
    __tablename__ = "notification_service"

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String, nullable=True)   # Gotify app token; ntfy auth token
    access_token = Column(String, nullable=True)  # Gotify client token

    def __repr__(self):
        return f"<NotificationService(id='{self.id.hex()}')>"


class SmallSeaBackend:
    """
    Hub backend — session management, cloud storage, sync.

    Participant/user/team provisioning has moved to the
    small-sea-manager package (provisioning.py).
    """

    hub_schema_version: int = 47

    def __init__(self, root_dir, auto_approve_sessions: bool = False):
        self.root_dir = pathlib.Path(root_dir)
        self.auto_approve_sessions = auto_approve_sessions
        os.makedirs(self.root_dir, exist_ok=True)
        self.path_local_db = self.root_dir / "small_sea_collective_local.db"
        os.makedirs(self.root_dir / "Logging", exist_ok=True)
        log_path = self.root_dir / "Logging" / "small_sea_hub.log"
        self.logger = setup_logging(log_file=log_path)
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

    def _resolve_station(self, participant_dir, team_name, app_name):
        """Return (team_id, app_id, station_id) as bytes.

        The team row is always read from the participant's NoteToSelf DB.
        For NoteToSelf, app and station are also in that DB.
        For all other teams, app and station are in the team DB at
        Participants/{hex}/{team_name}/Sync/core.db.

        Uses raw SQL for the app/station lookup to stay compatible with both
        the NoteToSelf schema (team_app_station has team_id) and the team DB
        schema (team_app_station intentionally omits team_id).
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
            station_db = note_to_self_db
        else:
            station_db = str(participant_dir / team_name / "Sync" / "core.db")

        conn = sqlite3.connect(station_db)
        try:
            app_row = conn.execute(
                "SELECT id FROM app WHERE name = ?", (app_name,)
            ).fetchone()
            if app_row is None:
                raise SmallSeaNotFoundExn(f"App '{app_name}' not found in '{team_name}'")
            app_id = app_row[0]

            station_row = conn.execute(
                "SELECT id FROM team_app_station WHERE app_id = ?", (app_id,)
            ).fetchone()
            if station_row is None:
                raise SmallSeaNotFoundExn(
                    f"No station for app '{app_name}' in team '{team_name}'"
                )
            station_id = station_row[0]
        finally:
            conn.close()

        return team_id, app_id, station_id

    def request_session(self, nickname, app, team, client):
        """Step 1 of session approval: generate PIN, write pending row, send OS notification.

        Returns (pending_id_hex, pin). For normal clients, pin should not be shown to
        the requester — it arrives via OS notification. For 'Smoke Tests', the caller
        may use the returned pin directly to call confirm_session.
        """
        matching = self._find_participant(nickname)
        if not matching:
            raise SmallSeaNotFoundExn()

        participant_dir, engine = matching[0]
        participant_hex = participant_dir.absolute().name
        self._resolve_station(participant_dir, team, app)  # validate existence

        pin = str(secrets.randbelow(10000)).zfill(4)

        if client != "Smoke Tests":
            try:
                plyer.notification.notify(
                    title="Small Sea Access Request",
                    message=f'PIN: {pin} — "{client}" requesting access to {team} → {app}',
                    app_name="Small Sea Hub",
                    timeout=10,
                )
            except Exception:
                pass  # notification failure must not abort the session request

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
            team_id, app_id, station_id = self._resolve_station(
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
                station_id=station_id,
                client=pending.client_name,
            )
            sess.add(ss_session)
            sess.delete(pending)
            sess.commit()

        return token

    def open_session(self, nickname, app, team, client) -> bytes:
        """Smoke-test shortcut: request + auto-confirm in one call.

        Only for use with client='Smoke Tests'. Real clients use the
        request_session / confirm_session two-step flow via the HTTP API.
        """
        assert client == "Smoke Tests", "open_session is only for smoke tests"
        pending_id_hex, pin = self.request_session(nickname, app, team, client)
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
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            cloud = CloudStorage(
                id=uuid7(),
                protocol=scheme,
                url=location,
                access_key=access_key,
                secret_key=secret_key,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            session.add_all([cloud])
            session.commit()

    def _get_cloud_link(self, ss_session: SmallSeaSession):
        # TODO: Should we check permissions? Probably.
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            results = session.query(CloudStorage).all()
            if len(results) != 1:
                print(f"TODO: Other cases {len(results)}")
                raise NotImplementedError()
            cloud = results[0]
        return cloud

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

        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            session.execute(
                text(
                    "UPDATE cloud_storage SET access_token = :token, token_expiry = :expiry WHERE id = :id"
                ),
                {"token": access_token, "expiry": expiry, "id": cloud.id},
            )
            session.commit()

        return access_token

    def _make_s3_adapter(self, ss_session, cloud):
        import boto3
        from botocore.config import Config as BotoConfig

        bucket_name = f"ss-{ss_session.station_id.hex()[:16]}"

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
        nts_db = str(ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db")
        conn = sqlite3.connect(nts_db)
        try:
            row = conn.execute(
                "SELECT self_in_team FROM team WHERE name = ?", (ss_session.team_name,)
            ).fetchone()
        finally:
            conn.close()
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
        return self._download_peer_file(session_hex, member_id_hex, path)

    def upload_to_cloud(self, session_hex, path, data, expected_etag=None):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        if expected_etag is not None:
            return adapter.upload_if_match(path, data, expected_etag)
        return adapter.upload_overwrite(path, data)

    def download_from_cloud(self, session_hex, path):
        ss_session = self._lookup_session(session_hex)
        adapter = self._make_storage_adapter(ss_session)
        return adapter.download(path)

    # ---- Signal file ----

    _SIGNAL_PATH = "signals.yaml"
    _SIGNAL_MAX_RETRIES = 5

    def _bump_signal(self, session_hex):
        """Atomically increment the station counter in this session's signals.yaml.

        Uses a CAS retry loop. On network failure after all retries, logs a
        warning and returns — teammates will catch up on the next push.
        Over-counting (from concurrent device pushes) is acceptable.
        """
        ss_session = self._lookup_session(session_hex)
        station_id_hex = ss_session.station_id.hex()
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
            signals[station_id_hex] = signals.get(station_id_hex, 0) + 1

            payload = yaml.dump(signals, default_flow_style=False).encode("utf-8")
            if etag is not None:
                upload_ok, _, msg = adapter.upload_if_match(self._SIGNAL_PATH, payload, etag)
            else:
                upload_ok, _, msg = adapter.upload_overwrite(self._SIGNAL_PATH, payload)

            if upload_ok:
                return
            # CAS conflict — re-read and retry

        self.logger.warning(
            f"_bump_signal: gave up after {self._SIGNAL_MAX_RETRIES} retries "
            f"(session {session_hex[:8]})"
        )

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
            row = conn.execute(
                "SELECT protocol, url, bucket FROM peer WHERE member_id = ?", (member_id,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise SmallSeaNotFoundExn(f"No peer found for member {member_id_hex}")

        protocol, url, bucket = row

        if protocol == "s3":
            import boto3
            from botocore import UNSIGNED
            from botocore.config import Config as BotoConfig

            bucket_name = bucket or f"ss-{ss_session.station_id.hex()[:16]}"
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

    # ---- Notifications ----

    def _get_notification_service(self, ss_session):
        core_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
        engine_core = create_engine(f"sqlite:///{core_path}")
        with Session(engine_core) as session:
            results = session.query(NotificationService).all()
            if len(results) == 0:
                raise SmallSeaNotFoundExn("No notification service configured")
            return results[0]

    def _make_notification_adapter(self, ss_session):
        import hashlib

        ns = self._get_notification_service(ss_session)
        if ns.protocol == "ntfy":
            # Derive topic from team+app names so all participants on the same
            # station share the same ntfy topic automatically.
            station_key = f"{ss_session.team_name}/{ss_session.app_name}"
            topic = "ss-" + hashlib.sha256(station_key.encode()).hexdigest()[:16]
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
