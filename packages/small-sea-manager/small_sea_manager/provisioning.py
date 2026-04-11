# Participant/user/team/app provisioning for small-sea-manager.
#
# Handles creating participants, initializing per-user databases, and managing
# teams/apps via direct SQLite and filesystem operations. No network I/O.
# Called by TeamManager (manager.py) for all local DB reads and writes.
#
# The SQLAlchemy models here are duplicated from the hub — the SQLite DB
# schema is the shared contract between the two packages.

import base64
import hashlib
import json
import os
import pathlib
import secrets
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import Column, LargeBinary, String, create_engine, text
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()

import shutil
import subprocess

import cod_sync.protocol as CodSync
from cuttlefish import (
    generate_bootstrap_keypair,
    generate_bootstrap_signing_keypair,
    open_welcome_bundle,
    seal_welcome_bundle,
    sign_welcome_bundle,
    verify_welcome_bundle_signature,
)
from cuttlefish.group import (
    GroupMessage,
    _advance_chain_key,
    _derive_message_key,
    create_sender_key,
    group_decrypt,
)
from small_sea_note_to_self.db import (
    attached_note_to_self_connection,
    device_local_db_path,
    initialize_bootstrap_local_state,
    initialize_shared_db,
    note_to_self_sync_db_path,
)
from small_sea_note_to_self.bootstrap import (
    JoinRequestArtifact,
    SignedWelcomeBundle,
    WelcomeBundle,
    deserialize_signed_welcome_bundle_plaintext,
    deserialize_join_request_artifact,
    deserialize_welcome_bundle_plaintext,
    join_request_auth_string,
    serialize_join_request_artifact,
    serialize_signed_welcome_bundle_plaintext,
    serialize_welcome_bundle_plaintext,
    welcome_bundle_confirmation_string,
    welcome_bundle_aad,
)
from small_sea_note_to_self.ids import uuid7
from small_sea_note_to_self.sender_keys import (
    deserialize_distribution_message,
    deserialize_sender_key_record,
    distribution_message_from_record,
    load_team_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
    save_team_sender_key,
    serialize_sender_key_record,
    serialize_distribution_message,
)
from wrasse_trust.identity import (
    CertType,
    KeyCertificate,
    issue_device_link_cert,
    issue_membership_cert,
    parse_cert_type,
    trusted_device_keys_by_member as resolve_trusted_device_keys_by_member,
    trusted_device_keys_for_member as resolve_trusted_device_keys_for_member,
    verify_device_link_cert,
    verify_membership_cert,
)
from wrasse_trust.keys import ParticipantKey, ProtectionLevel, generate_key_pair


def _serialize_cert(cert: KeyCertificate) -> dict:
    return {
        "cert_id": cert.cert_id.hex(),
        "cert_type": cert.cert_type.value,
        "team_id": cert.team_id.hex() if cert.team_id is not None else None,
        "subject_key_id": cert.subject_key_id.hex(),
        "subject_public_key": cert.subject_public_key.hex(),
        "issuer_key_id": cert.issuer_key_id.hex(),
        "issuer_participant_id": cert.issuer_participant_id.hex(),
        "issued_at_iso": cert.issued_at_iso,
        "claims": cert.claims,
        "signature": cert.signature.hex(),
    }


def _deserialize_cert(data: dict) -> KeyCertificate:
    return KeyCertificate(
        cert_id=bytes.fromhex(data["cert_id"]),
        cert_type=parse_cert_type(data["cert_type"]),
        team_id=bytes.fromhex(data["team_id"]) if data.get("team_id") else None,
        subject_key_id=bytes.fromhex(data["subject_key_id"]),
        subject_public_key=bytes.fromhex(data["subject_public_key"]),
        issuer_key_id=bytes.fromhex(data["issuer_key_id"]),
        issuer_participant_id=bytes.fromhex(data["issuer_participant_id"]),
        issued_at_iso=data["issued_at_iso"],
        claims=data["claims"],
        signature=bytes.fromhex(data["signature"]),
    )


def _fake_enclave_dir(root_dir, participant_hex) -> pathlib.Path:
    return pathlib.Path(root_dir) / "Participants" / participant_hex / "FakeEnclave"


def _bootstrap_state_dir(root_dir) -> pathlib.Path:
    return pathlib.Path(root_dir) / ".small-sea-manager"


def _bootstrap_fake_enclave_dir(root_dir) -> pathlib.Path:
    return _bootstrap_state_dir(root_dir) / "FakeEnclave"


def _bootstrap_state_path(root_dir) -> pathlib.Path:
    return _bootstrap_state_dir(root_dir) / "pending_identity_join.json"


def _team_device_key_path(root_dir, participant_hex, team_id: bytes, device_id: bytes) -> pathlib.Path:
    return _fake_enclave_dir(root_dir, participant_hex) / (
        f"team-device-{team_id.hex()}-{device_id.hex()}.key"
    )


def _note_to_self_device_encryption_key_path(root_dir, participant_hex, device_id: bytes) -> pathlib.Path:
    return _fake_enclave_dir(root_dir, participant_hex) / f"device-{device_id.hex()}-enc.key"


def _note_to_self_device_signing_key_path(root_dir, participant_hex, device_id: bytes) -> pathlib.Path:
    return _fake_enclave_dir(root_dir, participant_hex) / f"device-{device_id.hex()}-sign.key"


def _bootstrap_pending_device_encryption_key_path(root_dir, device_id: bytes) -> pathlib.Path:
    return _bootstrap_fake_enclave_dir(root_dir) / f"pending-device-{device_id.hex()}-enc.key"


def _bootstrap_pending_device_signing_key_path(root_dir, device_id: bytes) -> pathlib.Path:
    return _bootstrap_fake_enclave_dir(root_dir) / f"pending-device-{device_id.hex()}-sign.key"


def _identity_bootstrap_status_path(root_dir, participant_hex: str) -> pathlib.Path:
    return (
        pathlib.Path(root_dir)
        / "Participants"
        / participant_hex
        / "NoteToSelf"
        / "Local"
        / "identity_bootstrap_status.json"
    )


def _current_device_row(conn):
    row = conn.execute(
        """
        SELECT
            ud.id,
            ud.bootstrap_encryption_key,
            ud.signing_key,
            ndks.encryption_private_key_ref,
            ndks.signing_private_key_ref
        FROM user_device ud
        JOIN local.note_to_self_device_key_secret ndks
          ON ndks.device_id = ud.id
        ORDER BY ud.id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise ValueError("No local device registered in user_device")
    return row


def _write_local_secret(path: pathlib.Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _read_local_secret(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def _participant_key_from_public(public_key: bytes) -> ParticipantKey:
    key_id = hashlib.sha256(public_key).digest()[:16]
    return ParticipantKey(
        key_id=key_id,
        public_key=public_key,
        protection_level=ProtectionLevel.DAILY,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
    )


def _persist_pending_join_state(root_dir, state: dict) -> None:
    path = _bootstrap_state_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True, indent=2))


def _load_pending_join_state(root_dir) -> dict:
    path = _bootstrap_state_path(root_dir)
    if not path.exists():
        raise ValueError("No pending identity-join state found")
    return json.loads(path.read_text())


def _clear_pending_join_state(root_dir) -> None:
    path = _bootstrap_state_path(root_dir)
    if path.exists():
        path.unlink()


def _mark_identity_bootstrap_untrusted(root_dir, participant_hex: str, *, reason: str) -> None:
    path = _identity_bootstrap_status_path(root_dir, participant_hex)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "identity_bootstrap_untrusted",
                "reason": reason,
            },
            sort_keys=True,
            indent=2,
        )
    )


def _clear_identity_bootstrap_untrusted(root_dir, participant_hex: str) -> None:
    path = _identity_bootstrap_status_path(root_dir, participant_hex)
    if path.exists():
        path.unlink()


def assert_identity_bootstrap_trusted(root_dir, participant_hex: str) -> None:
    path = _identity_bootstrap_status_path(root_dir, participant_hex)
    if path.exists():
        status = json.loads(path.read_text())
        raise ValueError(
            "Installation is blocked because identity bootstrap did not verify cleanly: "
            f"{status.get('reason', 'unknown reason')}"
        )


def _single_note_to_self_remote_descriptor(root_dir, participant_hex: str) -> dict:
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        rows = conn.execute(
            """
            SELECT id, protocol, url, client_id, path_metadata
            FROM cloud_storage
            ORDER BY rowid
            """
        ).fetchall()
    if not rows:
        raise ValueError("No NoteToSelf remote configured for this participant")
    if len(rows) != 1:
        raise ValueError("Expected exactly one NoteToSelf remote configuration")
    row = rows[0]
    return {
        "storage_id_hex": row[0].hex(),
        "protocol": row[1],
        "url": row[2],
        "client_id": row[3],
        "path_metadata": row[4],
    }


def _push_note_to_self_to_local_remote(root_dir, participant_hex: str, remote_descriptor: dict) -> None:
    protocol = remote_descriptor["protocol"]
    if protocol != "localfolder":
        raise NotImplementedError(
            "Identity bootstrap currently supports only localfolder NoteToSelf remotes"
        )
    remote_path = remote_descriptor["url"]
    repo_dir = pathlib.Path(root_dir) / "Participants" / participant_hex / "NoteToSelf" / "Sync"
    cod = CodSync.CodSync("identity-bootstrap", repo_dir=repo_dir)
    cod.remote = CodSync.LocalFolderRemote(remote_path)
    if cod.remote.path is None:
        raise ValueError(f"Invalid localfolder remote path: {remote_path}")
    cod.push_to_remote(["main"])


def _remote_from_descriptor(remote_descriptor: dict):
    protocol = remote_descriptor["protocol"]
    if protocol == "localfolder":
        remote = CodSync.LocalFolderRemote(remote_descriptor["url"])
        if remote.path is None:
            raise ValueError(f"Invalid localfolder remote path: {remote_descriptor['url']}")
        return remote
    raise NotImplementedError(
        f"Unsupported NoteToSelf bootstrap remote protocol: {protocol}"
    )


# ---- SQLAlchemy models for per-user core.db ----


class UserDevice(Base):
    __tablename__ = "user_device"

    id = Column(LargeBinary, primary_key=True)
    bootstrap_encryption_key = Column(LargeBinary, nullable=False)
    signing_key = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<UserDevice(id='{self.id.hex()}')>"


class Nickname(Base):
    __tablename__ = "nickname"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<Nickname(id='{self.id.hex()}')>"


class Team(Base):
    __tablename__ = "team"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)
    self_in_team = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<Team(id='{self.id.hex()}')>"


class App(Base):
    __tablename__ = "app"

    id = Column(LargeBinary, primary_key=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<App(id='{self.id.hex()}')>"


class TeamAppBerth(Base):
    __tablename__ = "team_app_berth"

    id = Column(LargeBinary, primary_key=True)
    team_id = Column(LargeBinary, nullable=False)
    app_id = Column(LargeBinary, nullable=False)

    def __repr__(self):
        return f"<TeamAppBerth(id='{self.id.hex()}')>"


class NotificationService(Base):
    __tablename__ = "notification_service"

    id = Column(LargeBinary, primary_key=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    access_key = Column(String, nullable=True)   # Gotify app token; ntfy auth token
    access_token = Column(String, nullable=True)  # Gotify client token

    def __repr__(self):
        return f"<NotificationService(id='{self.id.hex()}')>"


# ---- SQLAlchemy models for per-team core.db ----


class Invitation(Base):
    __tablename__ = "invitation"

    id = Column(LargeBinary, primary_key=True)
    nonce = Column(LargeBinary, nullable=False)
    status = Column(String, nullable=False, default="pending")
    invitee_label = Column(String)
    role = Column(String, nullable=False, default="admin")
    created_at = Column(String, nullable=False)
    accepted_at = Column(String)
    accepted_by = Column(LargeBinary)
    acceptor_protocol = Column(String)
    acceptor_url = Column(String)

    def __repr__(self):
        return f"<Invitation(id='{self.id.hex()}', status='{self.status}')>"


class Peer(Base):
    __tablename__ = "peer"

    id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    display_name = Column(String, nullable=True)
    protocol = Column(String, nullable=False)
    url = Column(String, nullable=False)
    bucket = Column(String, nullable=True)

    def __repr__(self):
        return f"<Peer(id='{self.id.hex()}')>"


# ---- Constants ----

USER_SCHEMA_VERSION = 54


# ---- Provisioning functions ----


def create_new_participant(root_dir, nickname, device=None):
    """Create a new participant: directory layout, user DB, git repo."""
    root_dir = pathlib.Path(root_dir)
    ident = uuid7()
    ident_dir = root_dir / "Participants" / ident.hex()

    try:
        os.makedirs(ident_dir / "NoteToSelf" / "Sync", exist_ok=False)
        os.makedirs(ident_dir / "NoteToSelf" / "Local", exist_ok=False)
        os.makedirs(ident_dir / "FakeEnclave", exist_ok=False)
    except Exception as exn:
        print(f"makedirs failed :( {ident_dir}")

    if device is None:
        device = "42"

    _initialize_user_db(root_dir, ident, nickname, device)
    return ident.hex()


def _initialize_user_db(root_dir, ident, nickname, device):
    root_dir = pathlib.Path(root_dir)
    shared_db_path = note_to_self_sync_db_path(root_dir, ident.hex())
    local_db_path = device_local_db_path(root_dir, ident.hex())
    initialize_shared_db(shared_db_path)
    device_id = uuid7()
    encryption_private_key_bytes, encryption_public_key_bytes = generate_bootstrap_keypair()
    signing_private_key_bytes, signing_public_key_bytes = generate_bootstrap_signing_keypair()
    try:
        with attached_note_to_self_connection(root_dir, ident.hex()) as conn:
            conn.execute(
                "INSERT INTO nickname (id, name) VALUES (?, ?)",
                (uuid7(), nickname),
            )
            conn.execute(
                """
                INSERT INTO user_device (id, bootstrap_encryption_key, signing_key)
                VALUES (?, ?, ?)
                """,
                (device_id, encryption_public_key_bytes, signing_public_key_bytes),
            )
            team_id = uuid7()
            app_id = uuid7()
            conn.execute(
                "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
                (team_id, "NoteToSelf", b"0"),
            )
            conn.execute(
                "INSERT INTO app (id, name) VALUES (?, ?)",
                (app_id, "SmallSeaCollectiveCore"),
            )
            conn.execute(
                "INSERT INTO team_app_berth (id, team_id, app_id) VALUES (?, ?, ?)",
                (uuid7(), team_id, app_id),
            )
            encryption_key_path = _note_to_self_device_encryption_key_path(
                root_dir, ident.hex(), device_id
            )
            signing_key_path = _note_to_self_device_signing_key_path(
                root_dir, ident.hex(), device_id
            )
            conn.execute(
                """
                INSERT INTO local.note_to_self_device_key_secret (
                    device_id, encryption_private_key_ref, signing_private_key_ref
                ) VALUES (?, ?, ?)
                """,
                (device_id, str(encryption_key_path), str(signing_key_path)),
            )
            conn.commit()

    except sqlite3.Error as e:
        print("SQLite error occurred:", e)

    _write_local_secret(
        _note_to_self_device_encryption_key_path(root_dir, ident.hex(), device_id),
        encryption_private_key_bytes,
    )
    _write_local_secret(
        _note_to_self_device_signing_key_path(root_dir, ident.hex(), device_id),
        signing_private_key_bytes,
    )

    repo_dir = root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync"
    CodSync.gitCmd(["init", "-b", "main", str(repo_dir)])
    CodSync.gitCmd(["-C", str(repo_dir), "add", "core.db"])
    CodSync.gitCmd(
        ["-C", str(repo_dir), "commit", "-m", f"Welcome to Small Sea Collective"]
    )


def create_identity_join_request(root_dir):
    """Create a persisted public join request artifact for a blank installation."""
    root_dir = pathlib.Path(root_dir)
    device_id = uuid7()
    encryption_private_key_bytes, encryption_public_key_bytes = generate_bootstrap_keypair()
    signing_private_key_bytes, signing_public_key_bytes = generate_bootstrap_signing_keypair()
    pending_encryption_key_path = _bootstrap_pending_device_encryption_key_path(root_dir, device_id)
    pending_signing_key_path = _bootstrap_pending_device_signing_key_path(root_dir, device_id)
    _write_local_secret(pending_encryption_key_path, encryption_private_key_bytes)
    _write_local_secret(pending_signing_key_path, signing_private_key_bytes)

    artifact = JoinRequestArtifact(
        version=1,
        device_id_hex=device_id.hex(),
        device_encryption_public_key_hex=encryption_public_key_bytes.hex(),
        device_signing_public_key_hex=signing_public_key_bytes.hex(),
    )
    auth_string = join_request_auth_string(artifact)
    _persist_pending_join_state(
        root_dir,
        {
            "device_id_hex": device_id.hex(),
            "device_encryption_public_key_hex": encryption_public_key_bytes.hex(),
            "device_signing_public_key_hex": signing_public_key_bytes.hex(),
            "encryption_private_key_ref": str(pending_encryption_key_path),
            "signing_private_key_ref": str(pending_signing_key_path),
            "join_request_artifact": serialize_join_request_artifact(artifact),
            "auth_string": auth_string,
        },
    )
    return {
        "join_request_artifact": serialize_join_request_artifact(artifact),
        "auth_string": auth_string,
    }


def authorize_identity_join(
    root_dir,
    participant_hex,
    join_request_artifact_b64,
    *,
    remote_descriptor: dict | None = None,
    expires_in_seconds: int = 600,
):
    """Admit a new device into shared NoteToSelf and return a sealed welcome bundle.

    When ``remote_descriptor`` is provided, this function stays local-only and
    returns whether NoteToSelf needs to be published separately by the caller.
    When absent, it falls back to the older localfolder-only publish path.
    """
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds must be positive")

    root_dir = pathlib.Path(root_dir)
    artifact = deserialize_join_request_artifact(join_request_artifact_b64)
    auth_string = join_request_auth_string(artifact)
    device_id = bytes.fromhex(artifact.device_id_hex)
    encryption_public_key = bytes.fromhex(artifact.device_encryption_public_key_hex)
    signing_public_key = bytes.fromhex(artifact.device_signing_public_key_hex)
    inserted_user_device = False

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        authorizing_device = _current_device_row(conn)
        existing = conn.execute(
            "SELECT bootstrap_encryption_key, signing_key FROM user_device WHERE id = ?",
            (device_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO user_device (id, bootstrap_encryption_key, signing_key)
                VALUES (?, ?, ?)
                """,
                (device_id, encryption_public_key, signing_public_key),
            )
            conn.commit()
            inserted_user_device = True
        elif existing[0] != encryption_public_key or existing[1] != signing_public_key:
            raise ValueError("A device with that ID is already registered with different keys")
        authorizing_device_id = authorizing_device[0]
        authorizing_signing_private_key = _read_local_secret(pathlib.Path(authorizing_device[4]))

    if remote_descriptor is None:
        remote_descriptor = _single_note_to_self_remote_descriptor(root_dir, participant_hex)
    if inserted_user_device:
        repo_dir = root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync"
        CodSync.gitCmd(["-C", str(repo_dir), "add", "core.db"])
        CodSync.gitCmd(
            ["-C", str(repo_dir), "commit", "-m", f"Admit device {artifact.device_id_hex[:8]}"]
        )
        if remote_descriptor.get("protocol") == "localfolder":
            _push_note_to_self_to_local_remote(root_dir, participant_hex, remote_descriptor)

    now = datetime.now(timezone.utc)
    expires = now.timestamp() + expires_in_seconds
    bundle = WelcomeBundle(
        version=1,
        participant_hex=participant_hex,
        joining_device_id_hex=artifact.device_id_hex,
        joining_device_public_key_hex=artifact.device_encryption_public_key_hex,
        identity_label=get_nickname(root_dir, participant_hex),
        remote_descriptor=remote_descriptor,
        issued_at=now.isoformat(),
        expires_at=datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        authorizing_device_label=get_nickname(root_dir, participant_hex),
    )
    bundle_plaintext = serialize_welcome_bundle_plaintext(bundle)
    signature = sign_welcome_bundle(authorizing_signing_private_key, bundle_plaintext)
    signed_bundle = SignedWelcomeBundle(
        version=1,
        bundle=bundle,
        authorizing_device_id_hex=authorizing_device_id.hex(),
        signature_hex=signature.hex(),
    )
    aad = welcome_bundle_aad(
        joining_device_id_hex=bundle.joining_device_id_hex,
        version=bundle.version,
    )
    sealed = seal_welcome_bundle(
        encryption_public_key,
        serialize_signed_welcome_bundle_plaintext(signed_bundle),
        associated_data=aad,
    )
    return {
        "welcome_bundle": base64.b64encode(sealed).decode("ascii"),
        "auth_string": auth_string,
        "second_confirmation_string": welcome_bundle_confirmation_string(
            artifact,
            bundle,
            signature,
        ),
        "needs_publish": inserted_user_device and remote_descriptor.get("protocol") != "localfolder",
    }


def prepare_identity_bootstrap(root_dir, welcome_bundle_b64):
    """Perform the local-only prepare step for identity bootstrap."""
    root_dir = pathlib.Path(root_dir)
    state = _load_pending_join_state(root_dir)
    pending_artifact = deserialize_join_request_artifact(state["join_request_artifact"])
    pending_encryption_private_key_bytes = _read_local_secret(
        pathlib.Path(state["encryption_private_key_ref"])
    )
    pending_signing_private_key_path = pathlib.Path(state["signing_private_key_ref"])

    sealed_bundle = base64.b64decode(welcome_bundle_b64.encode("ascii"))
    aad = welcome_bundle_aad(
        joining_device_id_hex=pending_artifact.device_id_hex,
        version=1,
    )
    plaintext = open_welcome_bundle(
        pending_encryption_private_key_bytes,
        sealed_bundle,
        associated_data=aad,
    )
    signed_bundle = deserialize_signed_welcome_bundle_plaintext(plaintext)
    bundle = signed_bundle.bundle
    if bundle.joining_device_id_hex != pending_artifact.device_id_hex:
        raise ValueError("Welcome bundle device_id does not match pending join request")
    if bundle.joining_device_public_key_hex != pending_artifact.device_encryption_public_key_hex:
        raise ValueError("Welcome bundle public key does not match pending join request")

    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(bundle.expires_at)
    if expires_at <= now:
        raise ValueError("Welcome bundle has expired")

    participant_dir = root_dir / "Participants" / bundle.participant_hex
    if participant_dir.exists():
        shared_db = note_to_self_sync_db_path(root_dir, bundle.participant_hex)
        if shared_db.exists():
            raise ValueError(f"Participant {bundle.participant_hex} already exists locally")

    initialize_bootstrap_local_state(root_dir, bundle.participant_hex)
    (participant_dir / "FakeEnclave").mkdir(parents=True, exist_ok=True)

    final_encryption_key_path = _note_to_self_device_encryption_key_path(
        root_dir,
        bundle.participant_hex,
        bytes.fromhex(bundle.joining_device_id_hex),
    )
    final_signing_key_path = _note_to_self_device_signing_key_path(
        root_dir,
        bundle.participant_hex,
        bytes.fromhex(bundle.joining_device_id_hex),
    )
    _write_local_secret(final_encryption_key_path, pending_encryption_private_key_bytes)
    _write_local_secret(final_signing_key_path, _read_local_secret(pending_signing_private_key_path))
    local_db_path = device_local_db_path(root_dir, bundle.participant_hex)
    with sqlite3.connect(local_db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO note_to_self_device_key_secret (
                device_id, encryption_private_key_ref, signing_private_key_ref
            ) VALUES (?, ?, ?)
            """,
            (
                bytes.fromhex(bundle.joining_device_id_hex),
                str(final_encryption_key_path),
                str(final_signing_key_path),
            ),
        )
        conn.commit()

    sync_dir = participant_dir / "NoteToSelf" / "Sync"
    if not (sync_dir / ".git").exists():
        CodSync.gitCmd(["init", "-b", "main", str(sync_dir)])

    return {
        "participant_hex": bundle.participant_hex,
        "participant_dir": str(participant_dir),
        "sync_dir": str(sync_dir),
        "bundle": bundle,
        "signed_bundle": signed_bundle,
        "pending_artifact": pending_artifact,
        "pending_state": state,
    }


def finalize_identity_bootstrap(root_dir, prepared: dict):
    """Verify fetched NoteToSelf state and finalize bootstrap cleanup."""
    root_dir = pathlib.Path(root_dir)
    bundle = prepared["bundle"]
    signed_bundle = prepared["signed_bundle"]
    pending_artifact = prepared["pending_artifact"]
    state = prepared["pending_state"]
    bundle_plaintext = serialize_welcome_bundle_plaintext(bundle)
    signature = bytes.fromhex(signed_bundle.signature_hex)
    with sqlite3.connect(note_to_self_sync_db_path(root_dir, bundle.participant_hex)) as conn:
        signer_row = conn.execute(
            "SELECT signing_key FROM user_device WHERE id = ?",
            (bytes.fromhex(signed_bundle.authorizing_device_id_hex),),
        ).fetchone()
    if signer_row is None or not verify_welcome_bundle_signature(
        signer_row[0],
        bundle_plaintext,
        signature,
    ):
        _mark_identity_bootstrap_untrusted(
            root_dir,
            bundle.participant_hex,
            reason="Welcome bundle signature verification failed",
        )
        raise ValueError("Welcome bundle signature verification failed")
    _clear_identity_bootstrap_untrusted(root_dir, bundle.participant_hex)

    pending_encryption_key_path = pathlib.Path(state["encryption_private_key_ref"])
    pending_signing_private_key_path = pathlib.Path(state["signing_private_key_ref"])
    if pending_encryption_key_path.exists():
        pending_encryption_key_path.unlink()
    if pending_signing_private_key_path.exists():
        pending_signing_private_key_path.unlink()
    _clear_pending_join_state(root_dir)
    return {
        "participant_hex": bundle.participant_hex,
        "identity_label": bundle.identity_label,
        "joining_device_id_hex": bundle.joining_device_id_hex,
        "authorizing_device_label": bundle.authorizing_device_label,
        "second_confirmation_string": welcome_bundle_confirmation_string(
            pending_artifact,
            bundle,
            signature,
        ),
    }


def bootstrap_existing_identity(root_dir, welcome_bundle_b64):
    """Complete local-only identity bootstrap for localfolder remotes."""
    prepared = prepare_identity_bootstrap(root_dir, welcome_bundle_b64)
    bundle = prepared["bundle"]
    sync_dir = pathlib.Path(prepared["sync_dir"])
    cod = CodSync.CodSync("bootstrap-identity", repo_dir=sync_dir)
    cod.remote = _remote_from_descriptor(bundle.remote_descriptor)
    fetched_sha = cod.fetch_from_remote(["main"])
    if fetched_sha is None:
        raise RuntimeError("Failed to fetch NoteToSelf during identity bootstrap")
    CodSync.gitCmd(["-C", str(sync_dir), "checkout", "main"])
    return finalize_identity_bootstrap(root_dir, prepared)



def _migrate_user_db(conn, from_version):
    """Apply incremental migrations to bring a user DB up to USER_SCHEMA_VERSION."""
    if from_version < 44:
        for col in [
            "client_id",
            "client_secret",
            "refresh_token",
            "access_token",
            "token_expiry",
            "path_metadata",
        ]:
            conn.execute(text(f"ALTER TABLE cloud_storage ADD COLUMN {col} TEXT"))
    if from_version < 45:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS notification_service ("
                "id BLOB PRIMARY KEY, "
                "protocol TEXT NOT NULL, "
                "url TEXT NOT NULL)"
            )
        )
    if from_version < 46:
        pass  # team DB schema updated (app, team_app_berth, berth_role); NoteToSelf schema unchanged
    if from_version < 47:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_signing_key ("
                "id BLOB PRIMARY KEY, "
                "team_id BLOB NOT NULL, "
                "public_key BLOB NOT NULL, "
                "private_key BLOB NOT NULL, "
                "created_at TEXT NOT NULL, "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )
    if from_version < 48:
        conn.execute(text("ALTER TABLE notification_service ADD COLUMN access_key TEXT"))
        conn.execute(text("ALTER TABLE notification_service ADD COLUMN access_token TEXT"))
    if from_version < 49:
        pass  # peer.bucket added to team DB schema; NoteToSelf schema unchanged
    if from_version < 50:
        pass  # peer.display_name added to team DB schema; NoteToSelf schema unchanged
    if from_version < 51:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_sender_key ("
                "team_id BLOB PRIMARY KEY, "
                "group_id BLOB NOT NULL, "
                "sender_participant_id BLOB NOT NULL, "
                "chain_id BLOB NOT NULL, "
                "chain_key BLOB NOT NULL, "
                "iteration INTEGER NOT NULL, "
                "signing_public_key BLOB NOT NULL, "
                "signing_private_key BLOB, "
                "skipped_message_keys TEXT NOT NULL DEFAULT '{}', "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )
    if from_version < 52:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_identity ("
                "team_id BLOB PRIMARY KEY, "
                "member_id BLOB NOT NULL, "
                "public_key BLOB NOT NULL, "
                "created_at TEXT NOT NULL, "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS wrapped_team_identity_key ("
                "team_id BLOB NOT NULL, "
                "device_id BLOB NOT NULL, "
                "wrapped_private_key BLOB NOT NULL, "
                "wrapper_version TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "revoked_at TEXT, "
                "PRIMARY KEY (team_id, device_id), "
                "FOREIGN KEY (team_id) REFERENCES team(id), "
                "FOREIGN KEY (device_id) REFERENCES user_device(id))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_device_key ("
                "team_id BLOB NOT NULL, "
                "device_id BLOB NOT NULL, "
                "public_key BLOB NOT NULL, "
                "private_key_ref TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "revoked_at TEXT, "
                "PRIMARY KEY (team_id, device_id), "
                "FOREIGN KEY (team_id) REFERENCES team(id), "
                "FOREIGN KEY (device_id) REFERENCES user_device(id))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS peer_sender_key ("
                "team_id BLOB NOT NULL, "
                "group_id BLOB NOT NULL, "
                "sender_participant_id BLOB NOT NULL, "
                "chain_id BLOB NOT NULL, "
                "chain_key BLOB NOT NULL, "
                "iteration INTEGER NOT NULL, "
                "signing_public_key BLOB NOT NULL, "
                "signing_private_key BLOB, "
                "skipped_message_keys TEXT NOT NULL DEFAULT '{}', "
                "PRIMARY KEY (team_id, sender_participant_id), "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )


def _migrate_team_db(conn, from_version):
    """Apply incremental migrations to bring a team DB up to USER_SCHEMA_VERSION."""
    if from_version < 49:
        conn.execute(text("ALTER TABLE peer ADD COLUMN bucket TEXT"))
    if from_version < 50:
        conn.execute(text("ALTER TABLE peer ADD COLUMN display_name TEXT"))
    if from_version < 52:
        conn.execute(text("ALTER TABLE member RENAME COLUMN public_key TO device_public_key"))
        conn.execute(text("ALTER TABLE member ADD COLUMN identity_public_key BLOB"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS key_certificate ("
                "cert_id BLOB PRIMARY KEY, "
                "cert_type TEXT NOT NULL, "
                "subject_key_id BLOB NOT NULL, "
                "subject_public_key BLOB NOT NULL, "
                "issuer_key_id BLOB NOT NULL, "
                "issuer_member_id BLOB NOT NULL, "
                "issued_at TEXT NOT NULL, "
                "claims TEXT NOT NULL, "
                "signature BLOB NOT NULL, "
                "FOREIGN KEY (issuer_member_id) REFERENCES member(id) ON DELETE CASCADE)"
            )
        )


def ensure_team_db_schema(db_path):
    """Upgrade an existing team DB in place if needed."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            user_version = conn.execute(text("PRAGMA user_version")).scalar()
            if user_version == USER_SCHEMA_VERSION:
                return
            if (0 != user_version) and (user_version < USER_SCHEMA_VERSION):
                _migrate_team_db(conn, user_version)
                conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
                return
            if user_version > USER_SCHEMA_VERSION:
                raise NotImplementedError("TODO: DB FROM THE FUTURE!")
    finally:
        engine.dispose()


def migrate_participant_team_dbs(root_dir, participant_hex):
    """Ensure all existing team DBs for a participant are on the current schema."""
    root_dir = pathlib.Path(root_dir)
    for team in list_teams(root_dir, participant_hex):
        team_name = team["name"]
        if team_name == "NoteToSelf":
            continue
        team_db_path = (
            root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
        )
        if team_db_path.exists():
            ensure_team_db_schema(team_db_path)


def _initialize_core_note_to_self_schema(conn):
    raise NotImplementedError(
        "NoteToSelf shared-schema initialization now lives in small_sea_note_to_self.db"
    )


def make_device_link_invitation(session):
    # make keypair
    pass


def _init_team_db(db_path):
    """Initialize a team core.db with the team schema. Returns the engine."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        schema_path = pathlib.Path(__file__).parent / "sql" / "core_other_team.sql"
        with open(schema_path, "r") as f:
            schema_script = f.read()
        for statement in schema_script.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.execute(text(f"PRAGMA user_version = {USER_SCHEMA_VERSION}"))
    return engine


def _install_sqlite_merge_driver(team_sync_dir):
    """Install the splice-sqlite-merge git merge driver for core.db.

    Writes .gitattributes (tracked) and configures the merge driver
    command in .git/config (local only).
    """
    team_sync_dir = pathlib.Path(team_sync_dir)

    # .gitattributes — tracked by git, cloned automatically
    gitattributes = team_sync_dir / ".gitattributes"
    gitattributes.write_text("core.db merge=splice-sqlite\n")

    # Find the splice-sqlite-merge executable
    merge_bin = shutil.which("splice-sqlite-merge")
    if merge_bin is None:
        # Fallback: try to find it via the Python that's running us
        merge_bin = "splice-sqlite-merge"

    driver_cmd = f"{merge_bin} %O %A %B %L %P"
    CodSync.gitCmd(
        [
            "-C",
            str(team_sync_dir),
            "config",
            "merge.splice-sqlite.driver",
            driver_cmd,
        ]
    )


def _initialize_team_sender_key_state(user_db_path, team_id, member_id):
    sender_key, distribution = create_sender_key(team_id, member_id)
    save_team_sender_key(user_db_path, team_id, sender_key)
    save_peer_sender_key(
        user_db_path,
        team_id,
        receiver_record_from_distribution(distribution),
    )
    return distribution


def _store_team_certificate(conn, cert: KeyCertificate, issuer_member_id: bytes) -> None:
    conn.execute(
        text(
            "INSERT INTO key_certificate ("
            "cert_id, cert_type, subject_key_id, subject_public_key, "
            "issuer_key_id, issuer_member_id, issued_at, claims, signature"
            ") VALUES ("
            ":cert_id, :cert_type, :subject_key_id, :subject_public_key, "
            ":issuer_key_id, :issuer_member_id, :issued_at, :claims, :signature)"
        ),
        {
            "cert_id": cert.cert_id,
            "cert_type": cert.cert_type,
            "subject_key_id": cert.subject_key_id,
            "subject_public_key": cert.subject_public_key,
            "issuer_key_id": cert.issuer_key_id,
            "issuer_member_id": issuer_member_id,
            "issued_at": cert.issued_at_iso,
            "claims": json.dumps(cert.claims, sort_keys=True),
            "signature": cert.signature,
        },
    )


def _load_team_certificates(conn, team_id: bytes) -> list[KeyCertificate]:
    rows = conn.execute(
        text(
            "SELECT cert_id, cert_type, subject_key_id, subject_public_key, "
            "issuer_key_id, issuer_member_id, issued_at, claims, signature "
            "FROM key_certificate ORDER BY issued_at ASC"
        )
    ).fetchall()
    certs = []
    for row in rows:
        certs.append(
            KeyCertificate(
                cert_id=row[0],
                cert_type=parse_cert_type(row[1]),
                team_id=team_id,
                subject_key_id=row[2],
                subject_public_key=row[3],
                issuer_key_id=row[4],
                issuer_participant_id=row[5],
                issued_at_iso=row[6],
                claims=json.loads(row[7]),
                signature=row[8],
            )
        )
    return certs


def _team_row(root_dir, participant_hex, team_name):
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            "SELECT id, self_in_team FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Team '{team_name}' not found in NoteToSelf")
    return row


def get_trusted_device_keys_for_member_in_team_db(team_db_path, team_id, member_id):
    """Return trusted team-device public keys for one member from one team DB."""
    if isinstance(member_id, str):
        member_id = bytes.fromhex(member_id)
    engine = create_engine(f"sqlite:///{team_db_path}")
    try:
        with engine.begin() as conn:
            certs = _load_team_certificates(conn, team_id)
    finally:
        engine.dispose()
    return resolve_trusted_device_keys_for_member(certs, team_id, member_id)


def get_trusted_device_keys_for_member(root_dir, participant_hex, team_name, member_id):
    """Return trusted team-device public keys for one member from cert history."""
    if isinstance(member_id, str):
        member_id = bytes.fromhex(member_id)
    team_id, _self_in_team = _team_row(root_dir, participant_hex, team_name)
    team_db_path = (
        pathlib.Path(root_dir)
        / "Participants"
        / participant_hex
        / team_name
        / "Sync"
        / "core.db"
    )
    return get_trusted_device_keys_for_member_in_team_db(team_db_path, team_id, member_id)


def get_trusted_device_keys_by_member(root_dir, participant_hex, team_name):
    """Return trusted team-device public keys for every member from cert history."""
    team_id, _self_in_team = _team_row(root_dir, participant_hex, team_name)
    team_db_path = (
        pathlib.Path(root_dir)
        / "Participants"
        / participant_hex
        / team_name
        / "Sync"
        / "core.db"
    )
    engine = create_engine(f"sqlite:///{team_db_path}")
    try:
        with engine.begin() as conn:
            certs = _load_team_certificates(conn, team_id)
    finally:
        engine.dispose()
    return resolve_trusted_device_keys_by_member(certs, team_id)


def issue_device_link_for_member(root_dir, participant_hex, team_name, linked_device_public_key):
    """Issue and store a device_link cert for an externally generated public key."""
    if isinstance(linked_device_public_key, str):
        linked_device_public_key = bytes.fromhex(linked_device_public_key)

    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex
    team_id, member_id = _team_row(root_dir, participant_hex, team_name)
    issuer_private_key, issuer_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )

    current_trusted_keys = get_trusted_device_keys_for_member(
        root_dir, participant_hex, team_name, member_id
    )
    if issuer_public_key not in current_trusted_keys:
        raise ValueError("Current team device key is not trusted for this member")

    issuer_key = _participant_key_from_public(issuer_public_key)
    subject_key = _participant_key_from_public(linked_device_public_key)
    cert = issue_device_link_cert(
        subject_key=subject_key,
        issuer_key=issuer_key,
        issuer_private_key=issuer_private_key,
        team_id=team_id,
        member_id=member_id,
    )
    if not verify_device_link_cert(
        cert,
        issuer_public_key=issuer_public_key,
        team_id=team_id,
        member_id=member_id,
        subject_public_key=linked_device_public_key,
    ):
        raise ValueError("Failed to issue a valid device_link cert")

    team_sync_dir = participant_dir / team_name / "Sync"
    team_db_path = team_sync_dir / "core.db"
    engine = create_engine(f"sqlite:///{team_db_path}")
    try:
        with engine.begin() as conn:
            _store_team_certificate(conn, cert, issuer_member_id=member_id)
    finally:
        engine.dispose()

    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", "Linked additional team device"])
    return cert


def _generate_initial_team_device_key(
    root_dir,
    participant_hex: str,
    team_id: bytes,
):
    """Create the local current team-device key for this team."""
    now = datetime.now(timezone.utc).isoformat()
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        device_row = _current_device_row(conn)
        device_id = device_row[0]

    device_key, device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    device_key_path = _team_device_key_path(root_dir, participant_hex, team_id, device_id)
    _write_local_secret(device_key_path, device_private_key)

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        conn.execute(
            """
            INSERT INTO team_device_key (
                team_id, device_id, public_key, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (team_id, device_id, device_key.public_key, now),
        )
        conn.execute(
            """
            INSERT INTO local.team_device_key_secret (
                team_id, device_id, private_key_ref
            ) VALUES (?, ?, ?)
            """,
            (team_id, device_id, str(device_key_path)),
        )
        conn.commit()

    return {
        "device_id": device_id,
        "device_key": device_key,
        "device_private_key": device_private_key,
        "device_key_path": device_key_path,
    }


def _deserialize_group_message(payload: bytes) -> GroupMessage:
    data = json.loads(payload.decode("utf-8"))
    return GroupMessage(
        sender_participant_id=bytes.fromhex(data["sender_participant_id"]),
        sender_chain_id=bytes.fromhex(data["sender_chain_id"]),
        iteration=int(data["iteration"]),
        iv=bytes.fromhex(data["iv"]),
        ciphertext=bytes.fromhex(data["ciphertext"]),
        signature=bytes.fromhex(data["signature"]),
    )


def _message_key_for(message: GroupMessage, sender_key) -> bytes:
    target_iteration = message.iteration

    if target_iteration < sender_key.iteration:
        message_key = sender_key.skipped_message_keys.get(target_iteration)
        if message_key is None:
            raise ValueError(
                f"No skipped key for iteration {target_iteration} "
                f"(current iteration: {sender_key.iteration})"
            )
        return message_key

    if target_iteration == sender_key.iteration:
        return _derive_message_key(sender_key.chain_key)

    chain_key = sender_key.chain_key
    for _ in range(sender_key.iteration, target_iteration):
        chain_key = _advance_chain_key(chain_key)
    return _derive_message_key(chain_key)


def decrypt_invitation_bootstrap_payload(
    inviter_sender_key, payload: bytes
) -> tuple[object, bytes]:
    """Decrypt invitation bootstrap bytes when they were published in team mode.

    Some bootstrap artifacts may still be plaintext. In that case, return them
    unchanged so the invitation flow can consume either representation.
    """
    try:
        message = _deserialize_group_message(payload)
    except Exception:
        return inviter_sender_key, payload

    replay_message_key = _message_key_for(message, inviter_sender_key)
    next_sender_key, plaintext = group_decrypt(message, inviter_sender_key)
    replayable_keys = dict(next_sender_key.skipped_message_keys)
    replayable_keys[message.iteration] = replay_message_key
    next_sender_key = next_sender_key.__class__(
        group_id=next_sender_key.group_id,
        sender_participant_id=next_sender_key.sender_participant_id,
        chain_id=next_sender_key.chain_id,
        chain_key=next_sender_key.chain_key,
        iteration=next_sender_key.iteration,
        signing_public_key=next_sender_key.signing_public_key,
        signing_private_key=next_sender_key.signing_private_key,
        skipped_message_keys=replayable_keys,
    )
    return next_sender_key, plaintext


def get_current_team_device_key(root_dir, participant_hex, team_name):
    """Return the current device team key as (private_key_bytes, public_key_bytes)."""
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            """
            SELECT tdks.private_key_ref, tdk.public_key
            FROM team_device_key tdk
            JOIN team t ON tdk.team_id = t.id
            JOIN local.team_device_key_secret tdks
              ON tdks.team_id = tdk.team_id AND tdks.device_id = tdk.device_id
            WHERE t.name = ?
              AND tdk.revoked_at IS NULL
            ORDER BY tdk.created_at DESC
            LIMIT 1
            """,
            (team_name,),
        ).fetchone()
    if row is None:
        raise ValueError(f"No current device key found for team '{team_name}'")
    return _read_local_secret(pathlib.Path(row[0])), row[1]


def create_team(root_dir, participant_hex, team_name):
    """Create a new team for an existing participant.

    Adds team + team_app_berth rows to the user's NoteToSelf/Sync/core.db,
    creates the team directory with its own core.db (member table),
    and initializes a git repo for the team sync directory.

    Returns {"team_id_hex": ..., "member_id_hex": ...}.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    team_id = uuid7()
    member_id = uuid7()

    # --- Update the user's NoteToSelf core.db ---
    # Only a lightweight membership pointer goes here; structural team data
    # (App, TeamAppBerth, BerthRole) lives in the team's own DB.
    user_db_path = note_to_self_sync_db_path(root_dir, participant_hex)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, member_id),
        )
        conn.commit()

    # --- Generate local current device key ---
    team_keys = _generate_initial_team_device_key(
        root_dir, participant_hex, team_id
    )
    membership_cert = issue_membership_cert(
        subject_key=team_keys["device_key"],
        issuer_key=team_keys["device_key"],
        issuer_private_key=team_keys["device_private_key"],
        team_id=team_id,
        issuer_member_id=member_id,
        admitted_member_id=member_id,
    )
    _initialize_team_sender_key_state(
        device_local_db_path(root_dir, participant_hex), team_id, member_id
    )

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    # Populate the team DB: creator member, app, berth, and creator's role.
    app_id = uuid7()
    berth_id = uuid7()
    with team_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO member (id, device_public_key) "
                "VALUES (:id, :device_public_key)"
            ),
            {
                "id": member_id,
                "device_public_key": team_keys["device_key"].public_key,
            },
        )
        _store_team_certificate(conn, membership_cert, issuer_member_id=member_id)
        conn.execute(
            text("INSERT INTO app (id, name) VALUES (:id, :name)"),
            {"id": app_id, "name": "SmallSeaCollectiveCore"},
        )
        conn.execute(
            text("INSERT INTO team_app_berth (id, app_id) VALUES (:id, :app_id)"),
            {"id": berth_id, "app_id": app_id},
        )
        conn.execute(
            text(
                "INSERT INTO berth_role (id, member_id, berth_id, role) "
                "VALUES (:id, :mid, :bid, :role)"
            ),
            {"id": uuid7(), "mid": member_id, "bid": berth_id, "role": "read-write"},
        )

    # --- Git init ---
    CodSync.gitCmd(["init", "-b", "main", str(team_sync_dir)])
    _install_sqlite_merge_driver(team_sync_dir)
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"New team: {team_name}"])

    return {
        "team_id_hex": team_id.hex(),
        "member_id_hex": member_id.hex(),
        "berth_id_hex": berth_id.hex(),
    }


def create_invitation(
    root_dir, participant_hex, team_name, inviter_cloud, invitee_label=None, role="admin"
):
    """Create an invitation token for a team.

    inviter_cloud: dict with keys protocol and url (endpoint only — no credentials).
    Returns a base64-encoded JSON token string.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        team_row = conn.execute(
            "SELECT id, self_in_team FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    if team_row is None:
        raise ValueError(f"Team '{team_name}' not found in NoteToSelf")
    team_id = team_row[0]
    inviter_member_id = team_row[1]
    inviter_display_name = get_nickname(root_dir, participant_hex) or None

    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    inviter_sender_key = load_team_sender_key(
        device_local_db_path(root_dir, participant_hex), team_id
    )
    if inviter_sender_key is None:
        raise ValueError(f"No sender key found for team '{team_name}'")

    # Look up the berth ID from the team DB (to derive the bucket name).
    # Berth structural data lives in the team DB, not NoteToSelf.
    with team_engine.begin() as conn:
        berth_row = conn.execute(
            text("SELECT id FROM team_app_berth LIMIT 1")
        ).fetchone()
    if berth_row is None:
        raise ValueError(f"No berth found in team DB for '{team_name}'")
    berth_id_hex = berth_row[0].hex()
    if inviter_cloud["protocol"] == "dropbox":
        inviter_bucket = f"ss-{inviter_member_id.hex()[:16]}"
    else:
        inviter_bucket = f"ss-{berth_id_hex[:16]}"

    # Create invitation row
    inv_id = uuid7()
    nonce = secrets.token_bytes(16)
    now = datetime.now(timezone.utc).isoformat()

    with team_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO invitation (id, nonce, status, invitee_label, role, created_at) "
                "VALUES (:id, :nonce, 'pending', :label, :role, :created_at)"
            ),
            {"id": inv_id, "nonce": nonce, "label": invitee_label, "role": role, "created_at": now},
        )

    # Build token — credentials are never included; bucket is publicly readable.
    token_data = {
        "invitation_id": inv_id.hex(),
        "nonce": nonce.hex(),
        "team_id": team_id.hex(),
        "team_name": team_name,
        "inviter_member_id": inviter_member_id.hex(),
        "inviter_display_name": inviter_display_name,
        "inviter_cloud": {"protocol": inviter_cloud["protocol"], "url": inviter_cloud["url"]},
        "inviter_bucket": inviter_bucket,
        "inviter_sender_key": serialize_sender_key_record(inviter_sender_key),
    }
    token_json = json.dumps(token_data)
    token_b64 = base64.b64encode(token_json.encode()).decode()

    # Git commit the updated DB
    team_sync_dir = participant_dir / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Created invitation"])

    return token_b64


def accept_invitation(
    root_dir,
    acceptor_participant_hex,
    token_b64,
    inviter_remote,
    acceptor_remote=None,
    acceptor_member_id=None,
):
    """Accept a team invitation token (acceptor side).

    Clones the team repo from the inviter's cloud, adds self as member,
    and returns an acceptance response for the inviter. The caller is
    responsible for pushing to the acceptor's own cloud after this returns
    (typically via a Hub team session).

    inviter_remote: CodSyncRemote for reading the inviter's (public) bucket.
    acceptor_remote: ignored (deprecated; push is now the caller's responsibility).
    acceptor_member_id: pre-generated member ID bytes (optional). When None a
        new UUID is generated. Pass a pre-generated ID when the acceptor's
        bucket must be derived before this call (e.g. Dropbox folder-prefix).
    Returns a base64-encoded acceptance response JSON string.
    """
    root_dir = pathlib.Path(root_dir)

    # Decode token
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)
    team_name = token["team_name"]
    team_id = bytes.fromhex(token["team_id"])
    inviter_member_id = bytes.fromhex(token["inviter_member_id"])
    inviter_cloud = token["inviter_cloud"]  # protocol + url only, no credentials
    inviter_bucket = token["inviter_bucket"]
    inviter_display_name = token.get("inviter_display_name") or None
    inviter_sender_key = deserialize_distribution_message(token["inviter_sender_key"])
    invitation_id = bytes.fromhex(token["invitation_id"])
    nonce = bytes.fromhex(token["nonce"])

    # Read acceptor's own cloud config (URL only; credentials stay in Hub)
    acceptor_cloud_full = get_cloud_storage(root_dir, acceptor_participant_hex)
    acceptor_cloud = {"protocol": acceptor_cloud_full["protocol"], "url": acceptor_cloud_full["url"]}

    # Use pre-generated member ID if provided (required when acceptor_remote must
    # be constructed before this call, e.g. Dropbox folder-prefix naming).
    if acceptor_member_id is None:
        acceptor_member_id = uuid7()

    acceptor_dir = root_dir / "Participants" / acceptor_participant_hex

    # --- Create acceptor's team directory ---
    team_sync_dir = acceptor_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    # --- Clone the team repo from inviter's cloud ---
    # Use git init + fetch_from_remote + checkout rather than clone_from_remote,
    # so this works when the workspace lives inside an existing git repo.

    CodSync.gitCmd(["init", "-b", "main", str(team_sync_dir)])

    saved_cwd = os.getcwd()
    os.chdir(team_sync_dir)
    try:
        cod = CodSync.CodSync("inviter")
        cod.remote = inviter_remote
        result = cod.fetch_from_remote(["main"])
        if result is None:
            inviter_url = (
                f"{inviter_cloud['protocol']}://{inviter_cloud['url']}/{inviter_bucket}"
            )
            raise RuntimeError(
                f"Failed to fetch team repo from inviter's cloud (code {result}; {inviter_url})"
            )
        CodSync.gitCmd(["checkout", "main"])
    finally:
        os.chdir(saved_cwd)

    # --- Record the inviter as a peer in the cloned DB ---
    team_db_path = team_sync_dir / "core.db"
    ensure_team_db_schema(team_db_path)
    team_engine = create_engine(f"sqlite:///{team_db_path}")

    with team_engine.begin() as conn:
        # Store inviter's cloud location as a peer (URL only, no credentials)
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, display_name, protocol, url, bucket) "
                "VALUES (:id, :member_id, :display_name, :protocol, :url, :bucket)"
            ),
            {
                "id": uuid7(),
                "member_id": inviter_member_id,
                "display_name": inviter_display_name,
                "protocol": inviter_cloud["protocol"],
                "url": inviter_cloud["url"],
                "bucket": inviter_bucket,
            },
        )

    team_engine.dispose()

    # --- Install sqlite merge driver ---
    _install_sqlite_merge_driver(team_sync_dir)

    # --- Add team membership pointer to acceptor's NoteToSelf ---
    # Only a lightweight Team reference goes in NoteToSelf; structural data
    # (App, TeamAppBerth, BerthRole) lives in the team DB, which was cloned above.
    with attached_note_to_self_connection(root_dir, acceptor_participant_hex) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, acceptor_member_id),
        )
        conn.commit()

    # --- Generate local current device key ---
    team_keys = _generate_initial_team_device_key(
        root_dir, acceptor_participant_hex, team_id
    )
    save_peer_sender_key(
        device_local_db_path(root_dir, acceptor_participant_hex),
        team_id,
        receiver_record_from_distribution(inviter_sender_key),
    )
    acceptor_sender_key = _initialize_team_sender_key_state(
        device_local_db_path(root_dir, acceptor_participant_hex), team_id, acceptor_member_id
    )

    # --- Git commit the DB changes ---
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db", ".gitattributes"])
    CodSync.gitCmd(
        ["-C", str(team_sync_dir), "commit", "-m", f"Joined team: {team_name}"]
    )

    # Derive acceptor's bucket name (protocol-aware to avoid folder collisions)
    team_db_path = team_sync_dir / "core.db"
    team_engine = create_engine(f"sqlite:///{team_db_path}")
    if acceptor_cloud["protocol"] == "dropbox":
        acceptor_bucket = f"ss-{acceptor_member_id.hex()[:16]}"
    else:
        with team_engine.begin() as conn:
            berth_row = conn.execute(
                text("SELECT id FROM team_app_berth LIMIT 1")
            ).fetchone()
        acceptor_bucket = f"ss-{berth_row[0].hex()[:16]}"

    # --- Build and return acceptance response (no credentials) ---
    acceptance_data = {
        "invitation_id": invitation_id.hex(),
        "nonce": nonce.hex(),
        "team_id": team_id.hex(),
        "acceptor_member_id": acceptor_member_id.hex(),
        "acceptor_device_public_key": team_keys["device_key"].public_key.hex(),
        "acceptor_cloud": acceptor_cloud,
        "acceptor_bucket": acceptor_bucket,
        "acceptor_sender_key": serialize_distribution_message(acceptor_sender_key),
    }
    acceptance_json = json.dumps(acceptance_data)
    acceptance_b64 = base64.b64encode(acceptance_json.encode()).decode()

    return acceptance_b64


def complete_invitation_acceptance(
    root_dir, participant_hex, team_name, acceptance_b64
):
    """Complete an invitation acceptance (inviter side).

    Decodes the acceptance response, validates it against the invitation row,
    and adds the acceptor as a member + peer in the inviter's team DB.
    """
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    # Decode acceptance response
    acceptance_json = base64.b64decode(acceptance_b64).decode()
    acceptance = json.loads(acceptance_json)
    invitation_id = bytes.fromhex(acceptance["invitation_id"])
    nonce = bytes.fromhex(acceptance["nonce"])
    team_id = bytes.fromhex(acceptance["team_id"])
    acceptor_member_id = bytes.fromhex(acceptance["acceptor_member_id"])
    acceptor_device_public_key = bytes.fromhex(acceptance["acceptor_device_public_key"])
    acceptor_cloud = acceptance["acceptor_cloud"]
    acceptor_bucket = acceptance["acceptor_bucket"]
    acceptor_sender_key = deserialize_distribution_message(acceptance["acceptor_sender_key"])

    # Find and validate the invitation in the inviter's team DB
    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    ensure_team_db_schema(team_db_path)
    engine = create_engine(f"sqlite:///{team_db_path}")
    inviter_private_key, inviter_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    inviter_device_key = _participant_key_from_public(inviter_public_key)
    acceptor_device_key = _participant_key_from_public(acceptor_device_public_key)

    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    user_engine = create_engine(f"sqlite:///{user_db_path}")
    try:
        with user_engine.begin() as conn:
            inviter_team_row = conn.execute(
                text("SELECT id, self_in_team FROM team WHERE name = :team_name"),
                {"team_name": team_name},
            ).fetchone()
        if inviter_team_row is None:
            raise ValueError(f"Team '{team_name}' not found in NoteToSelf")
        if inviter_team_row[0] != team_id:
            raise ValueError("Acceptance team_id does not match local team")
        inviter_member_id = inviter_team_row[1]
    finally:
        user_engine.dispose()

    membership_cert = issue_membership_cert(
        subject_key=acceptor_device_key,
        issuer_key=inviter_device_key,
        issuer_private_key=inviter_private_key,
        team_id=team_id,
        issuer_member_id=inviter_member_id,
        admitted_member_id=acceptor_member_id,
    )
    if not verify_membership_cert(
        membership_cert,
        issuer_public_key=inviter_public_key,
        team_id=team_id,
        issuer_member_id=inviter_member_id,
        admitted_member_id=acceptor_member_id,
        subject_public_key=acceptor_device_public_key,
    ):
        raise ValueError("Failed to issue a valid membership cert for the acceptor")

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT nonce, status, invitee_label FROM invitation WHERE id = :id"),
            {"id": invitation_id},
        ).fetchone()

        if row is None:
            engine.dispose()
            raise ValueError("Invitation not found")

        if row[1] != "pending":
            engine.dispose()
            raise ValueError(f"Invitation is not pending (status: {row[1]})")
        if row[0] != nonce:
            engine.dispose()
            raise ValueError("Nonce mismatch")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            text(
                "UPDATE invitation SET status='accepted', accepted_at=:now, "
                "accepted_by=:member_id, acceptor_protocol=:protocol, "
                "acceptor_url=:url "
                "WHERE id = :id"
            ),
            {
                "id": invitation_id,
                "now": now,
                "member_id": acceptor_member_id,
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
            },
        )

        # Add acceptor as member + peer in inviter's team DB (URL only, no credentials)
        conn.execute(
            text(
                "INSERT INTO member (id, device_public_key) "
                "VALUES (:id, :device_public_key)"
            ),
            {
                "id": acceptor_member_id,
                "device_public_key": acceptor_device_public_key,
            },
        )
        _store_team_certificate(conn, membership_cert, issuer_member_id=inviter_member_id)
        conn.execute(
            text(
                "INSERT INTO peer (id, member_id, display_name, protocol, url, bucket) "
                "VALUES (:id, :member_id, :display_name, :protocol, :url, :bucket)"
            ),
            {
                "id": uuid7(),
                "member_id": acceptor_member_id,
                "display_name": row[2],
                "protocol": acceptor_cloud["protocol"],
                "url": acceptor_cloud["url"],
                "bucket": acceptor_bucket,
            },
        )

        # Grant the acceptor read-write on all berths (default).
        # The inviter (admin) can change this later.
        berth_row = conn.execute(
            text("SELECT id FROM team_app_berth LIMIT 1")
        ).fetchone()
        if berth_row is not None:
            conn.execute(
                text(
                    "INSERT INTO berth_role (id, member_id, berth_id, role) "
                    "VALUES (:id, :mid, :bid, :role)"
                ),
                {
                    "id": uuid7(),
                    "mid": acceptor_member_id,
                    "bid": berth_row[0],
                    "role": "read-write",
                },
            )

    # Dispose engine to release file locks before git operations
    engine.dispose()

    team_sync_dir = participant_dir / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", f"Accepted invitation"])

    save_peer_sender_key(
        device_local_db_path(root_dir, participant_hex),
        team_id,
        receiver_record_from_distribution(acceptor_sender_key),
    )


def add_notification_service(
    root_dir, participant_hex, protocol, url,
    access_key=None, access_token=None,
):
    """Register a notification service in a participant's NoteToSelf DB.

    protocol: "ntfy" or "gotify"
      ntfy:   url = ntfy server base URL; access_key = auth token if server requires it
      gotify: url = Gotify server base URL; access_key = app token (publish);
              access_token = client token (poll/subscribe)

    Returns the notification service ID hex.
    """
    known = {"ntfy", "gotify"}
    if protocol not in known:
        raise ValueError(f"Unknown notification protocol: {protocol}")

    root_dir = pathlib.Path(root_dir)
    ns_id = uuid7()
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        conn.execute(
            "INSERT INTO notification_service (id, protocol, url) VALUES (?, ?, ?)",
            (ns_id, protocol, url),
        )
        conn.execute(
            """
            INSERT INTO local.notification_service_credential (
                notification_service_id, access_key, access_token
            ) VALUES (?, ?, ?)
            """,
            (ns_id, access_key, access_token),
        )
        conn.commit()
    return ns_id.hex()


def set_notification_service(
    root_dir, participant_hex, protocol, url,
    access_key=None, access_token=None,
):
    """Upsert a notification service in a participant's NoteToSelf DB.

    Replaces any existing row with the same protocol before inserting, so this
    is safe to call multiple times (e.g. to update the URL).

    Returns the new notification service ID hex.
    """
    known = {"ntfy", "gotify"}
    if protocol not in known:
        raise ValueError(f"Unknown notification protocol: {protocol}")

    root_dir = pathlib.Path(root_dir)
    ns_id = uuid7()
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        old_ids = conn.execute(
            "SELECT id FROM notification_service WHERE protocol = ?",
            (protocol,),
        ).fetchall()
        for old_id, in old_ids:
            conn.execute(
                "DELETE FROM local.notification_service_credential WHERE notification_service_id = ?",
                (old_id,),
            )
        conn.execute(
            "DELETE FROM notification_service WHERE protocol = ?",
            (protocol,),
        )
        conn.execute(
            "INSERT INTO notification_service (id, protocol, url) VALUES (?, ?, ?)",
            (ns_id, protocol, url),
        )
        conn.execute(
            """
            INSERT INTO local.notification_service_credential (
                notification_service_id, access_key, access_token
            ) VALUES (?, ?, ?)
            """,
            (ns_id, access_key, access_token),
        )
        conn.commit()
    return ns_id.hex()


def get_cloud_storage(root_dir, participant_hex):
    """Return the first cloud storage config from NoteToSelf DB as a dict.

    Raises ValueError if no cloud storage is configured.
    """
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            """
            SELECT cs.protocol, cs.url, csc.access_key, csc.secret_key
            FROM cloud_storage cs
            LEFT JOIN local.cloud_storage_credential csc
              ON csc.cloud_storage_id = cs.id
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError("No cloud storage configured for this participant")
    return {"protocol": row[0], "url": row[1], "access_key": row[2], "secret_key": row[3]}


def add_cloud_storage(
    root_dir,
    participant_hex,
    protocol,
    url,
    access_key=None,
    secret_key=None,
    client_id=None,
    client_secret=None,
    refresh_token=None,
    access_token=None,
    token_expiry=None,
):
    """Add a cloud storage configuration to a participant's NoteToSelf DB."""
    root_dir = pathlib.Path(root_dir)
    storage_id = uuid7()
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        conn.execute(
            """
            INSERT INTO cloud_storage (id, protocol, url, client_id, path_metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (storage_id, protocol, url, client_id, None),
        )
        conn.execute(
            """
            INSERT INTO local.cloud_storage_credential (
                cloud_storage_id, access_key, secret_key, client_secret,
                refresh_token, access_token, token_expiry
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                storage_id,
                access_key,
                secret_key,
                client_secret,
                refresh_token,
                access_token,
                token_expiry,
            ),
        )
        conn.commit()


def list_cloud_storage(root_dir, participant_hex):
    """Return all cloud storage configs as a list of dicts (credentials masked)."""
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        rows = conn.execute(
            """
            SELECT cs.id, cs.protocol, cs.url, csc.access_key, cs.client_id
            FROM cloud_storage cs
            LEFT JOIN local.cloud_storage_credential csc
              ON csc.cloud_storage_id = cs.id
            ORDER BY rowid
            """
        ).fetchall()
    result = []
    for row in rows:
        storage_id = row[0].hex() if isinstance(row[0], bytes) else row[0]
        result.append({
            "id": storage_id,
            "protocol": row[1],
            "url": row[2],
            "access_key": row[3],
            "client_id": row[4],
        })
    return result


def remove_cloud_storage(root_dir, participant_hex, storage_id_hex):
    """Remove a cloud storage config by its hex ID."""
    root_dir = pathlib.Path(root_dir)
    storage_id = bytes.fromhex(storage_id_hex)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        conn.execute(
            "DELETE FROM local.cloud_storage_credential WHERE cloud_storage_id = ?",
            (storage_id,),
        )
        conn.execute(
            "DELETE FROM cloud_storage WHERE id = ?",
            (storage_id,),
        )
        conn.commit()


def revoke_invitation(root_dir, participant_hex, team_name, invitation_id_hex):
    """Set an invitation's status to 'revoked'. Raises ValueError if not pending."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    invitation_id = bytes.fromhex(invitation_id_hex)
    engine = create_engine(f"sqlite:///{team_db_path}")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM invitation WHERE id = :id"), {"id": invitation_id}
        ).fetchone()
        if row is None:
            raise ValueError("Invitation not found")
        if row[0] != "pending":
            raise ValueError(f"Invitation is not pending (status: {row[0]})")
        conn.execute(
            text("UPDATE invitation SET status = 'revoked' WHERE id = :id"),
            {"id": invitation_id},
        )
    engine.dispose()
    team_sync_dir = root_dir / "Participants" / participant_hex / team_name / "Sync"
    CodSync.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CodSync.gitCmd(["-C", str(team_sync_dir), "commit", "-m", "Revoked invitation"])


def get_nickname(root_dir, participant_hex):
    """Return the participant's first nickname, or empty string if none."""
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute("SELECT name FROM nickname LIMIT 1").fetchone()
    return row[0] if row else ""


def list_teams(root_dir, participant_hex):
    """List teams from NoteToSelf DB. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        rows = conn.execute("SELECT id, name, self_in_team FROM team").fetchall()
    return [
        {"id": row[0].hex(), "name": row[1], "self_in_team": row[2].hex()}
        for row in rows
    ]


def list_members(root_dir, participant_hex, team_name):
    """List members of a team with their berth roles. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        members = conn.execute(text("SELECT id FROM member")).fetchall()
        role_rows = conn.execute(
            text("SELECT member_id, berth_id, role FROM berth_role")
        ).fetchall()

    engine.dispose()

    roles_by_member = {}
    for r in role_rows:
        key = r[0].hex()
        roles_by_member.setdefault(key, []).append(
            {"berth_id": r[1].hex(), "role": r[2]}
        )

    return [
        {"id": row[0].hex(), "berth_roles": roles_by_member.get(row[0].hex(), [])}
        for row in members
    ]


def list_invitations(root_dir, participant_hex, team_name):
    """List invitations for a team. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    engine = create_engine(f"sqlite:///{team_db_path}")

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, status, invitee_label, role, created_at FROM invitation")
        ).fetchall()

    return [
        {
            "id": row[0].hex(),
            "status": row[1],
            "invitee_label": row[2],
            "role": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]
