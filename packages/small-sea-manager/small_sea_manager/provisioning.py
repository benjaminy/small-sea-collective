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
from dataclasses import replace
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import Column, LargeBinary, String, create_engine, event, text
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()

import shutil


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


def _sqlite_engine(db_path) -> object:
    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine

import cod_sync.protocol as CodSync
from cod_sync.repo import Repo as _Repo
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
from cuttlefish.prekeys import (
    IdentityKeyPair,
    OneTimePrekey,
    PrekeyBundle,
    SignedPrekey,
    build_prekey_bundle,
    generate_identity_key_pair,
    generate_one_time_prekeys,
    generate_signed_prekey,
)
from cuttlefish.ratchet import (
    EncryptedMessage,
    RatchetState,
    decrypt as ratchet_decrypt,
    encrypt as ratchet_encrypt,
    initialize_as_receiver,
    initialize_as_sender,
)
from cuttlefish.x3dh import X3DHInitialMessage, x3dh_receive, x3dh_send
from small_sea_note_to_self.db import (
    attached_note_to_self_connection,
    device_local_db_path,
    get_note_to_self_adopted_count,
    initialize_bootstrap_local_state,
    initialize_shared_db,
    note_to_self_sync_db_path,
    set_note_to_self_adopted_count,
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
    load_all_peer_sender_keys,
    load_peer_sender_key,
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
from wrasse_trust.keys import (
    ParticipantKey,
    ProtectionLevel,
    generate_key_pair,
    key_id_from_public,
)
from wrasse_trust.transport import (
    EffectiveTransportSelection,
    MemberTransportAnnouncement,
    TransportEndpoint,
    canonical_member_transport_announcement_bytes,
    key_certificate_from_team_db_record,
    select_effective_member_transport,
)


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
    key_id = key_id_from_public(public_key)
    return ParticipantKey(
        key_id=key_id,
        public_key=public_key,
        protection_level=ProtectionLevel.DAILY,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
    )


def _linked_team_bootstrap_session_row(root_dir, participant_hex: str, bootstrap_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT bootstrap_id, team_id, device_id, team_device_public_key,
                   team_device_private_key, x3dh_identity_dh_public_key,
                   x3dh_identity_dh_private_key, x3dh_identity_signing_public_key,
                   x3dh_identity_signing_private_key, signed_prekey_id,
                   signed_prekey_public_key, signed_prekey_private_key,
                   one_time_prekey_id, one_time_prekey_public_key,
                   one_time_prekey_private_key, ratchet_state_json, finalized_at,
                   response_payload_json, created_at
            FROM linked_team_bootstrap_session
            WHERE bootstrap_id = ?
            """,
            (bootstrap_id,),
        ).fetchone()


def _open_linked_team_bootstrap_session_for_team(root_dir, participant_hex: str, team_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT bootstrap_id, team_id, device_id, team_device_public_key,
                   team_device_private_key, x3dh_identity_dh_public_key,
                   x3dh_identity_dh_private_key, x3dh_identity_signing_public_key,
                   x3dh_identity_signing_private_key, signed_prekey_id,
                   signed_prekey_public_key, signed_prekey_private_key,
                   one_time_prekey_id, one_time_prekey_public_key,
                   one_time_prekey_private_key, ratchet_state_json, finalized_at,
                   response_payload_json, created_at
            FROM linked_team_bootstrap_session
            WHERE team_id = ? AND finalized_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (team_id,),
        ).fetchone()


def _store_linked_team_bootstrap_session(
    root_dir,
    participant_hex: str,
    *,
    bootstrap_id: bytes,
    team_id: bytes,
    device_id: bytes,
    team_device_public_key: bytes,
    team_device_private_key: bytes | None,
    x3dh_identity: IdentityKeyPair,
    signed_prekey: SignedPrekey,
    signed_prekey_private_key: bytes,
    one_time_prekey: OneTimePrekey | None,
    one_time_prekey_private_key: bytes | None,
    ratchet_state: RatchetState | None = None,
    finalized_at: str | None = None,
    response_payload_json: str | None = None,
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO linked_team_bootstrap_session (
                bootstrap_id,
                team_id,
                device_id,
                team_device_public_key,
                team_device_private_key,
                x3dh_identity_dh_public_key,
                x3dh_identity_dh_private_key,
                x3dh_identity_signing_public_key,
                x3dh_identity_signing_private_key,
                signed_prekey_id,
                signed_prekey_public_key,
                signed_prekey_private_key,
                one_time_prekey_id,
                one_time_prekey_public_key,
                one_time_prekey_private_key,
                ratchet_state_json,
                finalized_at,
                response_payload_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bootstrap_id,
                team_id,
                device_id,
                team_device_public_key,
                team_device_private_key,
                x3dh_identity.dh_public_key,
                x3dh_identity.dh_private_key,
                x3dh_identity.signing_public_key,
                x3dh_identity.signing_private_key,
                signed_prekey.prekey_id,
                signed_prekey.public_key,
                signed_prekey_private_key,
                one_time_prekey.prekey_id if one_time_prekey is not None else None,
                one_time_prekey.public_key if one_time_prekey is not None else None,
                one_time_prekey_private_key,
                _serialize_ratchet_state(ratchet_state) if ratchet_state is not None else None,
                finalized_at,
                response_payload_json,
                _now_iso(),
            ),
        )
        conn.commit()


def _update_linked_team_bootstrap_session(
    root_dir,
    participant_hex: str,
    bootstrap_id: bytes,
    *,
    ratchet_state: RatchetState | None = None,
    one_time_prekey_private_key: bytes | None | object = ...,
    finalized_at: str | None | object = ...,
    response_payload_json: str | None | object = ...,
) -> None:
    assignments = []
    values: list[object] = []
    if ratchet_state is not None:
        assignments.append("ratchet_state_json = ?")
        values.append(_serialize_ratchet_state(ratchet_state))
    if one_time_prekey_private_key is not ...:
        assignments.append("one_time_prekey_private_key = ?")
        values.append(one_time_prekey_private_key)
    if finalized_at is not ...:
        assignments.append("finalized_at = ?")
        values.append(finalized_at)
    if response_payload_json is not ...:
        assignments.append("response_payload_json = ?")
        values.append(response_payload_json)
    if not assignments:
        return
    values.append(bootstrap_id)
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            f"UPDATE linked_team_bootstrap_session SET {', '.join(assignments)} "
            "WHERE bootstrap_id = ?",
            values,
        )
        conn.commit()


def _store_pending_linked_team_bootstrap(
    root_dir,
    participant_hex: str,
    *,
    bootstrap_id: bytes,
    team_id: bytes,
    peer_device_id: bytes,
    peer_team_device_public_key: bytes,
    bootstrap_bundle: str | None = None,
) -> None:
    # Intentionally retained after finalize for create-side store-and-replay.
    # Cleanup of completed breadcrumbs is deferred for now.
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pending_linked_team_bootstrap (
                bootstrap_id, team_id, peer_device_id, peer_team_device_public_key,
                bootstrap_bundle, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                bootstrap_id,
                team_id,
                peer_device_id,
                peer_team_device_public_key,
                bootstrap_bundle,
                _now_iso(),
            ),
        )
        conn.commit()


def _load_pending_linked_team_bootstrap(root_dir, participant_hex: str, bootstrap_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT bootstrap_id, team_id, peer_device_id, peer_team_device_public_key,
                   bootstrap_bundle, created_at
            FROM pending_linked_team_bootstrap
            WHERE bootstrap_id = ?
            """,
            (bootstrap_id,),
        ).fetchone()


def _clear_pending_linked_team_bootstrap(root_dir, participant_hex: str, bootstrap_id: bytes) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            "DELETE FROM pending_linked_team_bootstrap WHERE bootstrap_id = ?",
            (bootstrap_id,),
        )
        conn.commit()


def _distribution_matches_record(distribution, record) -> bool:
    return (
        record.group_id == distribution.group_id
        and record.sender_device_key_id == distribution.sender_device_key_id
        and record.chain_id == distribution.sender_chain_id
        and record.iteration == distribution.iteration
        and record.chain_key == distribution.chain_key
        and record.signing_public_key == distribution.signing_public_key
    )


def _pending_bootstrap_matches_request(
    pending,
    *,
    team_id: bytes,
    peer_device_id: bytes,
    peer_team_device_public_key: bytes,
) -> bool:
    return (
        pending["team_id"] == team_id
        and pending["peer_device_id"] == peer_device_id
        and pending["peer_team_device_public_key"] == peer_team_device_public_key
    )


REDISTRIBUTION_ONE_TIME_PREKEY_COUNT = 5


def _redistribution_prekey_state_row(root_dir, participant_hex: str, team_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT team_id,
                   identity_dh_public_key,
                   identity_dh_private_key,
                   identity_signing_public_key,
                   identity_signing_private_key,
                   signed_prekey_id,
                   signed_prekey_public_key,
                   signed_prekey_private_key,
                   signed_prekey_signature,
                   published_at,
                   updated_at
            FROM redistribution_prekey_state
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()


def _available_redistribution_one_time_prekeys(root_dir, participant_hex: str, team_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT prekey_id, public_key, private_key
            FROM redistribution_one_time_prekey
            WHERE team_id = ? AND private_key IS NOT NULL AND consumed_at IS NULL
            ORDER BY prekey_id
            """,
            (team_id,),
        ).fetchall()


def _store_redistribution_prekey_state(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    identity: IdentityKeyPair,
    signed_prekey: SignedPrekey,
    signed_prekey_private_key: bytes,
    published_at: str | None = None,
) -> None:
    now = _now_iso()
    published = published_at or now
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO redistribution_prekey_state (
                team_id,
                identity_dh_public_key,
                identity_dh_private_key,
                identity_signing_public_key,
                identity_signing_private_key,
                signed_prekey_id,
                signed_prekey_public_key,
                signed_prekey_private_key,
                signed_prekey_signature,
                published_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                identity.dh_public_key,
                identity.dh_private_key,
                identity.signing_public_key,
                identity.signing_private_key,
                signed_prekey.prekey_id,
                signed_prekey.public_key,
                signed_prekey_private_key,
                signed_prekey.signature,
                published,
                now,
            ),
        )
        conn.commit()


def _replace_redistribution_one_time_prekeys(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    one_time_prekeys: list[tuple[OneTimePrekey, bytes]],
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            "DELETE FROM redistribution_one_time_prekey WHERE team_id = ?",
            (team_id,),
        )
        conn.executemany(
            """
            INSERT INTO redistribution_one_time_prekey (
                team_id, prekey_id, public_key, private_key, consumed_at
            ) VALUES (?, ?, ?, ?, NULL)
            """,
            [
                (
                    team_id,
                    one_time_prekey.prekey_id,
                    one_time_prekey.public_key,
                    one_time_prekey_private_key,
                )
                for one_time_prekey, one_time_prekey_private_key in one_time_prekeys
            ],
        )
        conn.commit()


def _consume_redistribution_one_time_prekey(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    prekey_id: bytes,
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            UPDATE redistribution_one_time_prekey
            SET private_key = NULL, consumed_at = ?
            WHERE team_id = ? AND prekey_id = ?
            """,
            (_now_iso(), team_id, prekey_id),
        )
        conn.commit()


def _redistribution_one_time_prekey_private(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    prekey_id: bytes,
) -> bytes | None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        row = conn.execute(
            """
            SELECT private_key
            FROM redistribution_one_time_prekey
            WHERE team_id = ? AND prekey_id = ?
            """,
            (team_id, prekey_id),
        ).fetchone()
    if row is None:
        return None
    return row[0]


def _ensure_redistribution_prekey_material(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    refresh: bool = False,
) -> tuple[IdentityKeyPair, SignedPrekey, bytes, list[tuple[OneTimePrekey, bytes]]]:
    state_row = _redistribution_prekey_state_row(root_dir, participant_hex, team_id)
    if state_row is None:
        identity = generate_identity_key_pair()
        signed_prekey, signed_prekey_private_key = generate_signed_prekey(
            identity.signing_private_key
        )
        one_time_prekeys = generate_one_time_prekeys(REDISTRIBUTION_ONE_TIME_PREKEY_COUNT)
        _store_redistribution_prekey_state(
            root_dir,
            participant_hex,
            team_id=team_id,
            identity=identity,
            signed_prekey=signed_prekey,
            signed_prekey_private_key=signed_prekey_private_key,
        )
        _replace_redistribution_one_time_prekeys(
            root_dir,
            participant_hex,
            team_id=team_id,
            one_time_prekeys=one_time_prekeys,
        )
        return identity, signed_prekey, signed_prekey_private_key, one_time_prekeys

    identity = IdentityKeyPair(
        dh_public_key=state_row["identity_dh_public_key"],
        dh_private_key=state_row["identity_dh_private_key"],
        signing_public_key=state_row["identity_signing_public_key"],
        signing_private_key=state_row["identity_signing_private_key"],
    )
    if refresh:
        signed_prekey, signed_prekey_private_key = generate_signed_prekey(
            identity.signing_private_key
        )
        one_time_prekeys = generate_one_time_prekeys(REDISTRIBUTION_ONE_TIME_PREKEY_COUNT)
        _store_redistribution_prekey_state(
            root_dir,
            participant_hex,
            team_id=team_id,
            identity=identity,
            signed_prekey=signed_prekey,
            signed_prekey_private_key=signed_prekey_private_key,
        )
        _replace_redistribution_one_time_prekeys(
            root_dir,
            participant_hex,
            team_id=team_id,
            one_time_prekeys=one_time_prekeys,
        )
        return identity, signed_prekey, signed_prekey_private_key, one_time_prekeys

    signed_prekey = SignedPrekey(
        prekey_id=state_row["signed_prekey_id"],
        public_key=state_row["signed_prekey_public_key"],
        signature=state_row["signed_prekey_signature"],
    )
    one_time_prekeys = [
        (
            OneTimePrekey(
                prekey_id=row["prekey_id"],
                public_key=row["public_key"],
            ),
            row["private_key"],
        )
        for row in _available_redistribution_one_time_prekeys(root_dir, participant_hex, team_id)
    ]
    return identity, signed_prekey, state_row["signed_prekey_private_key"], one_time_prekeys


def _ensure_device_prekey_bundle_table(conn) -> None:
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS device_prekey_bundle ("
            "device_key_id BLOB PRIMARY KEY, "
            "prekey_bundle_json TEXT NOT NULL, "
            "published_at TEXT NOT NULL, "
            "FOREIGN KEY (device_key_id) REFERENCES team_device(device_key_id) ON DELETE CASCADE)"
        )
    )


def _upsert_device_prekey_bundle(
    conn,
    *,
    device_key_id: bytes,
    prekey_bundle: PrekeyBundle,
    published_at: str,
) -> None:
    _ensure_device_prekey_bundle_table(conn)
    conn.execute(
        text(
            "INSERT OR REPLACE INTO device_prekey_bundle "
            "(device_key_id, prekey_bundle_json, published_at) "
            "VALUES (:device_key_id, :prekey_bundle_json, :published_at)"
        ),
        {
            "device_key_id": device_key_id,
            "prekey_bundle_json": json.dumps(
                _serialize_prekey_bundle(prekey_bundle),
                sort_keys=True,
            ),
            "published_at": published_at,
        },
    )


def _load_device_prekey_bundle(conn, device_key_id: bytes) -> PrekeyBundle | None:
    _ensure_device_prekey_bundle_table(conn)
    row = conn.execute(
        text(
            "SELECT prekey_bundle_json FROM device_prekey_bundle WHERE device_key_id = :device_key_id"
        ),
        {"device_key_id": device_key_id},
    ).fetchone()
    if row is None:
        return None
    return _deserialize_prekey_bundle(json.loads(row[0]))


def _bucket_name_for_protocol(protocol: str, member_id: bytes, berth_id: bytes) -> str:
    if protocol == "dropbox":
        return f"ss-{member_id.hex()[:16]}"
    return f"ss-{berth_id.hex()[:16]}"


_DEFAULT_ADMISSION_QUORUM = 1
_DEFAULT_PROPOSAL_EXPIRY_SECONDS = 7 * 24 * 60 * 60


def _sha256_bytes(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def _team_head_commit(team_sync_dir: pathlib.Path) -> str:
    head = _Repo(team_sync_dir / ".git", team_sync_dir).head()
    if head is None:
        raise ValueError("Team repo has no HEAD commit")
    return head


def _team_setting(conn, key: str, default: str) -> str:
    row = conn.execute(
        text("SELECT value FROM team_setting WHERE key = :key"),
        {"key": key},
    ).fetchone()
    return row[0] if row is not None else default


def set_team_admission_policy(
    root_dir,
    participant_hex,
    team_name,
    *,
    quorum: int | None = None,
    proposal_expiry_seconds: int | None = None,
) -> None:
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            if quorum is not None:
                if quorum < 1:
                    raise ValueError("admission quorum must be at least 1")
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO team_setting (key, value) VALUES "
                        "('admission_quorum', :value)"
                    ),
                    {"value": str(quorum)},
                )
            if proposal_expiry_seconds is not None:
                if proposal_expiry_seconds <= 0:
                    raise ValueError("proposal expiry must be positive")
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO team_setting (key, value) VALUES "
                        "('proposal_expiry_seconds', :value)"
                    ),
                    {"value": str(proposal_expiry_seconds)},
                )
    finally:
        engine.dispose()


def _team_admission_policy(conn) -> tuple[int, int]:
    quorum = int(_team_setting(conn, "admission_quorum", str(_DEFAULT_ADMISSION_QUORUM)))
    expiry = int(
        _team_setting(
            conn,
            "proposal_expiry_seconds",
            str(_DEFAULT_PROPOSAL_EXPIRY_SECONDS),
        )
    )
    return quorum, expiry


def _proposal_expiry(now: datetime, expiry_seconds: int) -> str:
    return datetime.fromtimestamp(now.timestamp() + expiry_seconds, timezone.utc).isoformat()


def _proposal_transcript_payload(
    *,
    proposal_id: bytes,
    nonce: bytes,
    team_id: bytes,
    invitee_member_id: bytes,
    invitee_device_public_key: bytes,
    invitee_bootstrap_key: bytes,
) -> dict[str, str]:
    return {
        "proposal_id": proposal_id.hex(),
        "nonce": nonce.hex(),
        "team_id": team_id.hex(),
        "invitee_member_id": invitee_member_id.hex(),
        "invitee_device_public_key": invitee_device_public_key.hex(),
        "invitee_bootstrap_key": invitee_bootstrap_key.hex(),
    }


def _proposal_transcript_digest(payload: dict[str, str]) -> bytes:
    return _sha256_bytes(_json_bytes(payload))


def _approval_payload(*, proposal_id: bytes, transcript_digest: bytes, admin_member_id: bytes) -> bytes:
    return _json_bytes(
        {
            "proposal_id": proposal_id.hex(),
            "transcript_digest": transcript_digest.hex(),
            "admin_member_id": admin_member_id.hex(),
        }
    )


def _finalization_payload(*, proposal_id: bytes, transcript_digest: bytes, invitee_member_id: bytes) -> bytes:
    return _json_bytes(
        {
            "proposal_id": proposal_id.hex(),
            "transcript_digest": transcript_digest.hex(),
            "invitee_member_id": invitee_member_id.hex(),
        }
    )


def _role_to_core_berth_role(role: str) -> str:
    if role == "admin":
        return "read-write"
    if role in {"contributor", "observer"}:
        return "read-only"
    raise ValueError(f"Unknown invitation role: {role}")


def _governance_snapshot(conn) -> dict[str, object]:
    member_ids = [row[0].hex() for row in conn.execute(text("SELECT id FROM member ORDER BY id")).fetchall()]
    admin_ids = [
        row[0].hex()
        for row in conn.execute(
            text(
                "SELECT br.member_id "
                "FROM berth_role br "
                "JOIN team_app_berth tab ON tab.id = br.berth_id "
                "JOIN app a ON a.id = tab.app_id "
                "WHERE a.name = 'SmallSeaCollectiveCore' AND br.role = 'read-write' "
                "ORDER BY br.member_id"
            )
        ).fetchall()
    ]
    member_devices: dict[str, list[str]] = {member_id_hex: [] for member_id_hex in member_ids}
    for member_id, device_key_id in conn.execute(
        text("SELECT member_id, device_key_id FROM team_device ORDER BY member_id, device_key_id")
    ).fetchall():
        member_devices.setdefault(member_id.hex(), []).append(device_key_id.hex())
    return {
        "admins": admin_ids,
        "members": member_ids,
        "member_devices": member_devices,
    }


def _governance_digest(snapshot: dict[str, object]) -> bytes:
    return _sha256_bytes(_json_bytes(snapshot))


def _load_admission_proposal_row(conn, proposal_id: bytes):
    row = conn.execute(
        text(
            "SELECT proposal_id, nonce, team_id, inviter_member_id, invitee_member_id, "
            "invitee_label, role, anchor_commit, governance_digest, governance_snapshot_json, "
            "state, created_at, expires_at, acceptance_recorded_at, invitee_device_public_key, "
            "invitee_bootstrap_key, acceptance_signature, transcript_digest, transcript_json, "
            "finalized_at, finalization_signature, invalid_reason "
            "FROM admission_proposal WHERE proposal_id = :proposal_id"
        ),
        {"proposal_id": proposal_id},
    ).fetchone()
    if row is None:
        raise ValueError("Admission proposal not found")
    return row


def _load_governance_snapshot_json(proposal_row) -> dict[str, object]:
    return json.loads(proposal_row[9])


def _proposal_is_still_valid(conn, proposal_row) -> tuple[bool, str | None]:
    state = proposal_row[10]
    if state == "finalized":
        return False, "Proposal is already finalized"
    if state in {"invalidated", "expired"}:
        return False, f"Proposal is already {state}"
    now_iso = _now_iso()
    if proposal_row[12] < now_iso:
        conn.execute(
            text(
                "UPDATE admission_proposal SET state = 'expired', invalid_reason = 'expired' "
                "WHERE proposal_id = :proposal_id"
            ),
            {"proposal_id": proposal_row[0]},
        )
        return False, "Proposal has expired"
    current_snapshot = _governance_snapshot(conn)
    if _governance_digest(current_snapshot) != proposal_row[8]:
        conn.execute(
            text(
                "UPDATE admission_proposal SET state = 'invalidated', "
                "invalid_reason = 'governance_drift' WHERE proposal_id = :proposal_id"
            ),
            {"proposal_id": proposal_row[0]},
        )
        return False, "Proposal invalidated by governance drift"
    return True, None


def _proposal_quorum(conn) -> int:
    quorum, _expiry = _team_admission_policy(conn)
    return quorum


def _approval_count(conn, proposal_id: bytes, transcript_digest: bytes) -> int:
    # Quorum counting trusts rows that made it into the shared DB. We do not
    # re-verify signatures here on every count because this branch still relies
    # on the sync/write-acceptance model rather than an isolated authority layer.
    row = conn.execute(
        text(
            "SELECT COUNT(DISTINCT admin_member_id) "
            "FROM admin_approval "
            "WHERE proposal_id = :proposal_id AND transcript_digest = :transcript_digest"
        ),
        {"proposal_id": proposal_id, "transcript_digest": transcript_digest},
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _proposal_has_quorum(conn, proposal_row) -> bool:
    transcript_digest = proposal_row[17]
    if transcript_digest is None:
        return False
    return _approval_count(conn, proposal_row[0], transcript_digest) >= _proposal_quorum(conn)


def _proposal_admin_device_is_valid(snapshot: dict[str, object], admin_member_id: bytes, approver_device_key_id: bytes) -> bool:
    admin_member_id_hex = admin_member_id.hex()
    if admin_member_id_hex not in snapshot.get("admins", []):
        return False
    member_devices = snapshot.get("member_devices", {})
    if not isinstance(member_devices, dict):
        return False
    return approver_device_key_id.hex() in member_devices.get(admin_member_id_hex, [])


def _insert_admin_approval(
    conn,
    *,
    proposal_id: bytes,
    admin_member_id: bytes,
    approver_device_key_id: bytes,
    signature: bytes,
    transcript_digest: bytes,
) -> None:
    conn.execute(
        text(
            "INSERT OR REPLACE INTO admin_approval "
            "(approval_id, proposal_id, admin_member_id, approver_device_key_id, "
            "transcript_digest, signature, created_at) "
            "VALUES (:approval_id, :proposal_id, :admin_member_id, :approver_device_key_id, "
            ":transcript_digest, :signature, :created_at)"
        ),
        {
            "approval_id": uuid7(),
            "proposal_id": proposal_id,
            "admin_member_id": admin_member_id,
            "approver_device_key_id": approver_device_key_id,
            "transcript_digest": transcript_digest,
            "signature": signature,
            "created_at": _now_iso(),
        },
    )


def _upsert_member_row(
    conn,
    member_id: bytes,
    *,
    display_name: str | None = None,
    identity_public_key: bytes | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT OR IGNORE INTO member (id, display_name, identity_public_key) "
            "VALUES (:id, :display_name, :identity_public_key)"
        ),
        {
            "id": member_id,
            "display_name": display_name,
            "identity_public_key": identity_public_key,
        },
    )
    updates = []
    params: dict[str, object] = {"id": member_id}
    if display_name is not None:
        updates.append("display_name = :display_name")
        params["display_name"] = display_name
    if identity_public_key is not None:
        updates.append("identity_public_key = :identity_public_key")
        params["identity_public_key"] = identity_public_key
    if updates:
        conn.execute(
            text(f"UPDATE member SET {', '.join(updates)} WHERE id = :id"),
            params,
        )


def _upsert_team_device_row(
    conn,
    member_id: bytes,
    public_key: bytes,
    *,
    protocol: str | None = None,
    url: str | None = None,
    bucket: str | None = None,
    created_at: str | None = None,
) -> bytes:
    device_key_id = key_id_from_public(public_key)
    if created_at is None:
        created_at = _now_iso()
    conn.execute(
        text(
            "INSERT OR IGNORE INTO team_device "
            "(device_key_id, member_id, public_key, protocol, url, bucket, created_at) "
            "VALUES (:device_key_id, :member_id, :public_key, :protocol, :url, :bucket, :created_at)"
        ),
        {
            "device_key_id": device_key_id,
            "member_id": member_id,
            "public_key": public_key,
            "protocol": protocol,
            "url": url,
            "bucket": bucket,
            "created_at": created_at,
        },
    )
    conn.execute(
        text(
            "UPDATE team_device "
            "SET member_id = :member_id, public_key = :public_key, "
            "protocol = COALESCE(:protocol, protocol), "
            "url = COALESCE(:url, url), "
            "bucket = COALESCE(:bucket, bucket) "
            "WHERE device_key_id = :device_key_id"
        ),
        {
            "device_key_id": device_key_id,
            "member_id": member_id,
            "public_key": public_key,
            "protocol": protocol,
            "url": url,
            "bucket": bucket,
        },
    )
    return device_key_id


def _publish_local_device_prekey_bundle(
    root_dir,
    participant_hex: str,
    team_name: str,
    *,
    commit_message: str | None = None,
    refresh: bool = False,
    conn=None,
    team_id: bytes | None = None,
    team_device_public_key: bytes | None = None,
) -> dict:
    if team_id is None:
        team_id, _member_id = _team_row(root_dir, participant_hex, team_name)
    if team_device_public_key is None:
        _private_key, team_device_public_key = get_current_team_device_key(
            root_dir, participant_hex, team_name
        )
    device_key_id = key_id_from_public(team_device_public_key)
    identity, signed_prekey, _signed_prekey_private_key, one_time_prekeys = (
        _ensure_redistribution_prekey_material(
            root_dir,
            participant_hex,
            team_id=team_id,
            refresh=refresh,
        )
    )
    bundle = build_prekey_bundle(
        participant_id=device_key_id,
        identity=identity,
        signed_prekey=signed_prekey,
        one_time_prekeys=[prekey for prekey, _private in one_time_prekeys],
    )
    published_at = _now_iso()

    if conn is not None:
        _upsert_device_prekey_bundle(
            conn,
            device_key_id=device_key_id,
            prekey_bundle=bundle,
            published_at=published_at,
        )
    else:
        team_sync_dir = pathlib.Path(root_dir) / "Participants" / participant_hex / team_name / "Sync"
        team_db_path = team_sync_dir / "core.db"
        ensure_team_db_schema(team_db_path)
        engine = _sqlite_engine(team_db_path)
        try:
            with engine.begin() as local_conn:
                _upsert_device_prekey_bundle(
                    local_conn,
                    device_key_id=device_key_id,
                    prekey_bundle=bundle,
                    published_at=published_at,
                )
        finally:
            engine.dispose()
        if commit_message is not None:
            repo = _Repo(team_sync_dir / ".git", team_sync_dir)
            repo.stage(["core.db"])
            repo.commit(commit_message)

    return {
        "device_key_id": device_key_id,
        "prekey_bundle": bundle,
        "published_at": published_at,
    }


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _tokenize(payload: dict) -> str:
    return base64.b64encode(_json_bytes(payload)).decode("ascii")


def _untokenize(token: str) -> dict:
    return json.loads(base64.b64decode(token.encode("ascii")).decode("utf-8"))


def _json_dumps_sorted(value) -> str:
    return json.dumps(value, sort_keys=True)


def _sign_bytes(private_key: bytes, payload: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(payload)


def _verify_signature(public_key: bytes, payload: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        return True
    except Exception:
        return False


def _serialize_signed_prekey(prekey: SignedPrekey) -> dict:
    return {
        "prekey_id": prekey.prekey_id.hex(),
        "public_key": prekey.public_key.hex(),
        "signature": prekey.signature.hex(),
    }


def _deserialize_signed_prekey(data: dict) -> SignedPrekey:
    return SignedPrekey(
        prekey_id=bytes.fromhex(data["prekey_id"]),
        public_key=bytes.fromhex(data["public_key"]),
        signature=bytes.fromhex(data["signature"]),
    )


def _serialize_one_time_prekey(prekey: OneTimePrekey) -> dict:
    return {
        "prekey_id": prekey.prekey_id.hex(),
        "public_key": prekey.public_key.hex(),
    }


def _deserialize_one_time_prekey(data: dict) -> OneTimePrekey:
    return OneTimePrekey(
        prekey_id=bytes.fromhex(data["prekey_id"]),
        public_key=bytes.fromhex(data["public_key"]),
    )


def _serialize_prekey_bundle(bundle: PrekeyBundle) -> dict:
    return {
        "participant_id": bundle.participant_id.hex(),
        "identity_dh_public_key": bundle.identity_dh_public_key.hex(),
        "identity_signing_public_key": bundle.identity_signing_public_key.hex(),
        "signed_prekey": _serialize_signed_prekey(bundle.signed_prekey),
        "one_time_prekeys": [
            _serialize_one_time_prekey(prekey) for prekey in bundle.one_time_prekeys
        ],
    }


def _deserialize_prekey_bundle(data: dict) -> PrekeyBundle:
    return PrekeyBundle(
        participant_id=bytes.fromhex(data["participant_id"]),
        identity_dh_public_key=bytes.fromhex(data["identity_dh_public_key"]),
        identity_signing_public_key=bytes.fromhex(data["identity_signing_public_key"]),
        signed_prekey=_deserialize_signed_prekey(data["signed_prekey"]),
        one_time_prekeys=[
            _deserialize_one_time_prekey(prekey)
            for prekey in data.get("one_time_prekeys", [])
        ],
    )


def _serialize_x3dh_initial_message(message: X3DHInitialMessage) -> dict:
    return {
        "sender_identity_dh_public_key": message.sender_identity_dh_public_key.hex(),
        "ephemeral_public_key": message.ephemeral_public_key.hex(),
        "used_one_time_prekey_id": (
            message.used_one_time_prekey_id.hex()
            if message.used_one_time_prekey_id is not None
            else None
        ),
    }


def _deserialize_x3dh_initial_message(data: dict) -> X3DHInitialMessage:
    return X3DHInitialMessage(
        sender_identity_dh_public_key=bytes.fromhex(data["sender_identity_dh_public_key"]),
        ephemeral_public_key=bytes.fromhex(data["ephemeral_public_key"]),
        used_one_time_prekey_id=(
            bytes.fromhex(data["used_one_time_prekey_id"])
            if data.get("used_one_time_prekey_id")
            else None
        ),
    )


def _serialize_encrypted_message(message: EncryptedMessage) -> dict:
    return {
        "ratchet_public_key": message.ratchet_public_key.hex(),
        "message_index": message.message_index,
        "previous_chain_length": message.previous_chain_length,
        "ciphertext": message.ciphertext.hex(),
        "iv": message.iv.hex(),
    }


def _deserialize_encrypted_message(data: dict) -> EncryptedMessage:
    return EncryptedMessage(
        ratchet_public_key=bytes.fromhex(data["ratchet_public_key"]),
        message_index=int(data["message_index"]),
        previous_chain_length=int(data["previous_chain_length"]),
        ciphertext=bytes.fromhex(data["ciphertext"]),
        iv=bytes.fromhex(data["iv"]),
    )


def _serialize_ratchet_state(state: RatchetState) -> str:
    return json.dumps(
        {
            "dh_public_key": state.dh_public_key.hex(),
            "dh_private_key": state.dh_private_key.hex(),
            "dh_remote_public_key": (
                state.dh_remote_public_key.hex()
                if state.dh_remote_public_key is not None
                else None
            ),
            "root_key": state.root_key.hex(),
            "sending_chain_key": (
                state.sending_chain_key.hex()
                if state.sending_chain_key is not None
                else None
            ),
            "receiving_chain_key": (
                state.receiving_chain_key.hex()
                if state.receiving_chain_key is not None
                else None
            ),
            "sending_message_index": state.sending_message_index,
            "receiving_message_index": state.receiving_message_index,
            "previous_sending_chain_length": state.previous_sending_chain_length,
            "skipped_keys": [
                {
                    "ratchet_public_key": ratchet_public_key.hex(),
                    "message_index": message_index,
                    "message_key": message_key.hex(),
                }
                for (ratchet_public_key, message_index), message_key in state.skipped_keys.items()
            ],
        },
        sort_keys=True,
    )


def _deserialize_ratchet_state(raw_value: str | None) -> RatchetState | None:
    if not raw_value:
        return None
    data = json.loads(raw_value)
    return RatchetState(
        dh_public_key=bytes.fromhex(data["dh_public_key"]),
        dh_private_key=bytes.fromhex(data["dh_private_key"]),
        dh_remote_public_key=(
            bytes.fromhex(data["dh_remote_public_key"])
            if data.get("dh_remote_public_key")
            else None
        ),
        root_key=bytes.fromhex(data["root_key"]),
        sending_chain_key=(
            bytes.fromhex(data["sending_chain_key"])
            if data.get("sending_chain_key")
            else None
        ),
        receiving_chain_key=(
            bytes.fromhex(data["receiving_chain_key"])
            if data.get("receiving_chain_key")
            else None
        ),
        sending_message_index=int(data["sending_message_index"]),
        receiving_message_index=int(data["receiving_message_index"]),
        previous_sending_chain_length=int(data["previous_sending_chain_length"]),
        skipped_keys={
            (
                bytes.fromhex(item["ratchet_public_key"]),
                int(item["message_index"]),
            ): bytes.fromhex(item["message_key"])
            for item in data.get("skipped_keys", [])
        },
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
    acceptor_device_key_id = Column(LargeBinary)
    acceptor_protocol = Column(String)
    acceptor_url = Column(String)

    def __repr__(self):
        return f"<Invitation(id='{self.id.hex()}', status='{self.status}')>"


class TeamDevice(Base):
    __tablename__ = "team_device"

    device_key_id = Column(LargeBinary, primary_key=True)
    member_id = Column(LargeBinary, nullable=False)
    public_key = Column(LargeBinary, nullable=False)
    protocol = Column(String, nullable=True)
    url = Column(String, nullable=True)
    bucket = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    def __repr__(self):
        return f"<TeamDevice(device_key_id='{self.device_key_id.hex()}')>"


# ---- Constants ----

USER_SCHEMA_VERSION = 58


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
            conn.execute(
                "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
                (team_id, "NoteToSelf", b"0"),
            )
            _ensure_participant_app_registration(
                conn,
                team_id,
                "SmallSeaCollectiveCore",
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

    os.makedirs(
        root_dir / "Participants" / ident.hex() / "NoteToSelf" / "SmallSeaCollectiveCore",
        exist_ok=False,
    )

    repo_dir = root_dir / "Participants" / ident.hex() / "NoteToSelf" / "Sync"
    nts_repo = _Repo.init(repo_dir / ".git").with_work_tree(repo_dir)
    nts_repo.stage(["core.db"])
    nts_repo.commit("Welcome to Small Sea Collective")


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
        nts_repo = _Repo(repo_dir / ".git", repo_dir)
        nts_repo.stage(["core.db"])
        nts_repo.commit(f"Admit device {artifact.device_id_hex[:8]}")
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
    repo = _Repo(sync_dir / ".git", sync_dir)
    if not repo.git_dir.exists():
        _Repo.init(repo.git_dir)

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
    _Repo(sync_dir / ".git", sync_dir).checkout_branch("main", start_point=fetched_sha)
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
                "sender_device_key_id BLOB NOT NULL, "
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
                "sender_device_key_id BLOB NOT NULL, "
                "chain_id BLOB NOT NULL, "
                "chain_key BLOB NOT NULL, "
                "iteration INTEGER NOT NULL, "
                "signing_public_key BLOB NOT NULL, "
                "signing_private_key BLOB, "
                "skipped_message_keys TEXT NOT NULL DEFAULT '{}', "
                "PRIMARY KEY (team_id, sender_device_key_id), "
                "FOREIGN KEY (team_id) REFERENCES team(id))"
            )
        )
    if from_version < 55:
        _rename_sender_key_column_if_present(conn, "team_sender_key")
        _rename_sender_key_column_if_present(conn, "peer_sender_key")


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
    if from_version < 56:
        _migrate_team_db_to_member_and_team_device(conn)
    if from_version < 57:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS member_transport_announcement ("
                "announcement_id BLOB PRIMARY KEY, "
                "member_id BLOB NOT NULL, "
                "protocol TEXT NOT NULL, "
                "url TEXT NOT NULL, "
                "bucket TEXT NOT NULL, "
                "announced_at TEXT NOT NULL, "
                "signer_key_id BLOB NOT NULL, "
                "signature BLOB NOT NULL, "
                "FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE)"
            )
        )
    if from_version < 58:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS team_setting ("
                "key TEXT PRIMARY KEY, "
                "value TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS admission_proposal ("
                "proposal_id BLOB PRIMARY KEY, "
                "nonce BLOB NOT NULL, "
                "team_id BLOB NOT NULL, "
                "inviter_member_id BLOB NOT NULL, "
                "invitee_member_id BLOB NOT NULL, "
                "invitee_label TEXT, "
                "role TEXT NOT NULL DEFAULT 'admin', "
                "anchor_commit TEXT NOT NULL, "
                "governance_digest BLOB NOT NULL, "
                "governance_snapshot_json TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "expires_at TEXT NOT NULL, "
                "acceptance_recorded_at TEXT, "
                "invitee_device_public_key BLOB, "
                "invitee_bootstrap_key BLOB, "
                "acceptance_signature BLOB, "
                "transcript_digest BLOB, "
                "transcript_json TEXT, "
                "finalized_at TEXT, "
                "finalization_signature BLOB, "
                "invalid_reason TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS admin_approval ("
                "approval_id BLOB PRIMARY KEY, "
                "proposal_id BLOB NOT NULL, "
                "admin_member_id BLOB NOT NULL, "
                "approver_device_key_id BLOB NOT NULL, "
                "transcript_digest BLOB NOT NULL, "
                "signature BLOB NOT NULL, "
                "created_at TEXT NOT NULL, "
                "UNIQUE (proposal_id, approver_device_key_id), "
                "FOREIGN KEY (proposal_id) REFERENCES admission_proposal(proposal_id) "
                "ON DELETE CASCADE)"
            )
        )


def _table_columns(conn, table_name: str) -> list[str]:
    return [row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": table_name},
    ).fetchone()
    return row is not None


def _migrate_team_db_to_member_and_team_device(conn) -> None:
    member_columns = set(_table_columns(conn, "member"))
    if "display_name" not in member_columns:
        conn.execute(text("ALTER TABLE member ADD COLUMN display_name TEXT"))
    if "identity_public_key" not in member_columns:
        conn.execute(text("ALTER TABLE member ADD COLUMN identity_public_key BLOB"))

    peer_rows = []
    if _table_exists(conn, "peer"):
        peer_rows = conn.execute(
            text(
                "SELECT member_id, display_name, protocol, url, bucket "
                "FROM peer ORDER BY member_id, id"
            )
        ).fetchall()
        seen_member_ids: set[bytes] = set()
        for row in peer_rows:
            member_id = row[0]
            if member_id in seen_member_ids:
                raise ValueError(
                    f"Cannot migrate team DB with multiple peer rows for member {member_id.hex()}"
                )
            seen_member_ids.add(member_id)
            conn.execute(
                text(
                    "UPDATE member SET display_name = :display_name "
                    "WHERE id = :member_id AND display_name IS NULL"
                ),
                {"member_id": member_id, "display_name": row[1]},
            )

    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS team_device ("
            "device_key_id BLOB PRIMARY KEY, "
            "member_id BLOB NOT NULL, "
            "public_key BLOB NOT NULL, "
            "protocol TEXT, "
            "url TEXT, "
            "bucket TEXT, "
            "created_at TEXT NOT NULL, "
            "FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE)"
        )
    )

    peer_by_member = {row[0]: row for row in peer_rows}
    cert_rows = conn.execute(
        text(
            "SELECT cert_type, subject_public_key, issued_at, claims "
            "FROM key_certificate ORDER BY issued_at ASC"
        )
    ).fetchall()
    devices_by_key: dict[bytes, dict[str, object]] = {}
    for cert_type, subject_public_key, issued_at, claims_json in cert_rows:
        if cert_type not in {"membership", "device_link"}:
            continue
        claims = json.loads(claims_json)
        member_id_hex = claims.get("member_id")
        if not isinstance(member_id_hex, str):
            continue
        try:
            member_id = bytes.fromhex(member_id_hex)
        except ValueError:
            continue
        device_key_id = key_id_from_public(subject_public_key)
        if device_key_id not in devices_by_key:
            peer_row = peer_by_member.get(member_id)
            devices_by_key[device_key_id] = {
                "member_id": member_id,
                "public_key": subject_public_key,
                "protocol": peer_row[2] if peer_row else None,
                "url": peer_row[3] if peer_row else None,
                "bucket": peer_row[4] if peer_row else None,
                "created_at": issued_at or _now_iso(),
            }

    if "device_public_key" in member_columns:
        legacy_member_rows = conn.execute(
            text("SELECT id, device_public_key FROM member WHERE device_public_key IS NOT NULL")
        ).fetchall()
        for member_id, public_key in legacy_member_rows:
            device_key_id = key_id_from_public(public_key)
            devices_by_key.setdefault(
                device_key_id,
                {
                    "member_id": member_id,
                    "public_key": public_key,
                    "protocol": peer_by_member.get(member_id)[2] if member_id in peer_by_member else None,
                    "url": peer_by_member.get(member_id)[3] if member_id in peer_by_member else None,
                    "bucket": peer_by_member.get(member_id)[4] if member_id in peer_by_member else None,
                    "created_at": _now_iso(),
                },
            )

    for row in devices_by_key.values():
        conn.execute(
            text(
                "INSERT OR REPLACE INTO team_device "
                "(device_key_id, member_id, public_key, protocol, url, bucket, created_at) "
                "VALUES (:device_key_id, :member_id, :public_key, :protocol, :url, :bucket, :created_at)"
            ),
            {
                "device_key_id": key_id_from_public(row["public_key"]),
                "member_id": row["member_id"],
                "public_key": row["public_key"],
                "protocol": row["protocol"],
                "url": row["url"],
                "bucket": row["bucket"],
                "created_at": row["created_at"],
            },
        )

    if not _table_exists(conn, "device_prekey_bundle"):
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS device_prekey_bundle ("
                "device_key_id BLOB PRIMARY KEY, "
                "prekey_bundle_json TEXT NOT NULL, "
                "published_at TEXT NOT NULL, "
                "FOREIGN KEY (device_key_id) REFERENCES team_device(device_key_id) ON DELETE CASCADE)"
            )
        )

    invitation_columns = set(_table_columns(conn, "invitation"))
    if "acceptor_device_key_id" not in invitation_columns:
        conn.execute(text("ALTER TABLE invitation ADD COLUMN acceptor_device_key_id BLOB"))


def _rename_sender_key_column_if_present(conn, table_name: str) -> None:
    columns = {
        row[1]
        for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    }
    if "sender_participant_id" not in columns or "sender_device_key_id" in columns:
        return
    conn.execute(
        text(
            f"ALTER TABLE {table_name} "
            "RENAME COLUMN sender_participant_id TO sender_device_key_id"
        )
    )


def ensure_team_db_schema(db_path):
    """Upgrade an existing team DB in place if needed."""
    engine = _sqlite_engine(db_path)
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


def prepare_linked_device_team_join(root_dir, participant_hex, team_name):
    """Prepare a same-member encrypted team bootstrap request on the joining device."""
    root_dir = pathlib.Path(root_dir)
    team_id, _member_id = _team_row(root_dir, participant_hex, team_name)
    bootstrap_id = uuid7()

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        device_row = _current_device_row(conn)
        device_id = device_row[0]
        note_to_self_signing_private_key = _read_local_secret(pathlib.Path(device_row[4]))

    existing_sender_key = load_team_sender_key(device_local_db_path(root_dir, participant_hex), team_id)
    if existing_sender_key is not None:
        raise ValueError(f"Device already has an active sender key for team '{team_name}'")
    existing_session = _open_linked_team_bootstrap_session_for_team(
        root_dir,
        participant_hex,
        team_id,
    )
    if existing_session is not None:
        raise ValueError(
            f"Linked-team bootstrap already in progress for team '{team_name}'"
        )

    team_device_key, team_device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    x3dh_identity = generate_identity_key_pair()
    signed_prekey, signed_prekey_private_key = generate_signed_prekey(
        x3dh_identity.signing_private_key
    )
    one_time_prekey, one_time_prekey_private_key = generate_one_time_prekeys(1)[0]
    prekey_bundle = build_prekey_bundle(
        participant_id=device_id,
        identity=x3dh_identity,
        signed_prekey=signed_prekey,
        one_time_prekeys=[one_time_prekey],
    )

    _store_linked_team_bootstrap_session(
        root_dir,
        participant_hex,
        bootstrap_id=bootstrap_id,
        team_id=team_id,
        device_id=device_id,
        team_device_public_key=team_device_key.public_key,
        team_device_private_key=team_device_private_key,
        x3dh_identity=x3dh_identity,
        signed_prekey=signed_prekey,
        signed_prekey_private_key=signed_prekey_private_key,
        one_time_prekey=one_time_prekey,
        one_time_prekey_private_key=one_time_prekey_private_key,
    )

    request_body = {
        "bootstrap_id": bootstrap_id.hex(),
        "team_id": team_id.hex(),
        "device_id": device_id.hex(),
        "team_device_public_key": team_device_key.public_key.hex(),
        "x3dh_prekey_bundle": _serialize_prekey_bundle(prekey_bundle),
    }
    request_bytes = _json_bytes(request_body)
    request_body["note_to_self_signature"] = _sign_bytes(
        note_to_self_signing_private_key,
        request_bytes,
    ).hex()
    request_body["team_device_signature"] = _sign_bytes(
        team_device_private_key,
        request_bytes,
    ).hex()

    return {
        "bootstrap_id_hex": bootstrap_id.hex(),
        "join_request_bundle": _tokenize(request_body),
    }


def create_linked_device_bootstrap(root_dir, participant_hex, team_name, join_request_bundle):
    """Authorize a same-member bootstrap and return the encrypted bootstrap payload."""
    root_dir = pathlib.Path(root_dir)
    request = _untokenize(join_request_bundle)
    request_body = {
        "bootstrap_id": request["bootstrap_id"],
        "team_id": request["team_id"],
        "device_id": request["device_id"],
        "team_device_public_key": request["team_device_public_key"],
        "x3dh_prekey_bundle": request["x3dh_prekey_bundle"],
    }
    request_bytes = _json_bytes(request_body)
    bootstrap_id = bytes.fromhex(request["bootstrap_id"])
    requested_team_id = bytes.fromhex(request["team_id"])
    device_id = bytes.fromhex(request["device_id"])
    proposed_team_device_public_key = bytes.fromhex(request["team_device_public_key"])

    team_id, member_id = _team_row(root_dir, participant_hex, team_name)
    if requested_team_id != team_id:
        raise ValueError("Join request team_id does not match local team")

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        note_to_self_row = conn.execute(
            "SELECT signing_key FROM user_device WHERE id = ?",
            (device_id,),
        ).fetchone()
    if note_to_self_row is None:
        raise ValueError("Join request device is not known in shared NoteToSelf state")
    if not _verify_signature(
        note_to_self_row[0],
        request_bytes,
        bytes.fromhex(request["note_to_self_signature"]),
    ):
        raise ValueError("Join request NoteToSelf signature is invalid")
    if not _verify_signature(
        proposed_team_device_public_key,
        request_bytes,
        bytes.fromhex(request["team_device_signature"]),
    ):
        raise ValueError("Join request Team X signature is invalid")

    pending = _load_pending_linked_team_bootstrap(root_dir, participant_hex, bootstrap_id)
    if pending is not None:
        if not _pending_bootstrap_matches_request(
            pending,
            team_id=team_id,
            peer_device_id=device_id,
            peer_team_device_public_key=proposed_team_device_public_key,
        ):
            raise ValueError("Existing pending bootstrap does not match the replayed join request")
        if pending["bootstrap_bundle"] is None:
            raise ValueError(
                "Pending bootstrap exists without a stored bundle; manual cleanup is required"
            )
        return {
            "bootstrap_id_hex": bootstrap_id.hex(),
            "bootstrap_bundle": pending["bootstrap_bundle"],
        }

    cert = issue_device_link_for_member(
        root_dir,
        participant_hex,
        team_name,
        proposed_team_device_public_key,
    )

    prekey_bundle = _deserialize_prekey_bundle(request["x3dh_prekey_bundle"])
    sender_identity = generate_identity_key_pair()
    x3dh_result = x3dh_send(sender_identity, prekey_bundle)
    ratchet_state = initialize_as_sender(
        x3dh_result.shared_secret,
        x3dh_result.signed_prekey_public,
    )

    sender_key_record = load_team_sender_key(device_local_db_path(root_dir, participant_hex), team_id)
    if sender_key_record is None:
        raise ValueError(f"No local sender key found for team '{team_name}'")
    sender_distribution = distribution_message_from_record(sender_key_record)
    peer_sender_records = load_all_peer_sender_keys(
        device_local_db_path(root_dir, participant_hex),
        team_id,
    )
    peer_sender_distributions = [
        # Peer receiver records and local sender records share the same current
        # chain-position fields needed by SenderKeyDistributionMessage, so this
        # reuse is valid for bootstrap handoff.
        serialize_distribution_message(distribution_message_from_record(peer_record))
        for peer_record in peer_sender_records
    ]
    peer_sender_skipped_keys = {
        peer_record.sender_device_key_id.hex(): {
            str(iteration): key.hex()
            for iteration, key in peer_record.skipped_message_keys.items()
        }
        for peer_record in peer_sender_records
        if peer_record.skipped_message_keys
    }
    plaintext = _json_bytes(
        {
            "own_sender_distribution": serialize_distribution_message(sender_distribution),
            "peer_sender_distributions": peer_sender_distributions,
            "peer_sender_skipped_keys": peer_sender_skipped_keys,
        }
    )
    associated_data = _json_bytes(
        {"bootstrap_id": bootstrap_id.hex(), "team_id": team_id.hex()}
    )
    ratchet_state, encrypted_message = ratchet_encrypt(
        ratchet_state,
        plaintext,
        associated_data=associated_data,
    )

    _authorizer_private_key, authorizer_public_key = get_current_team_device_key(
        root_dir,
        participant_hex,
        team_name,
    )
    response_body = {
        "bootstrap_id": bootstrap_id.hex(),
        "team_id": team_id.hex(),
        "authorizing_team_device_public_key": authorizer_public_key.hex(),
        "active_sender_device_key_id": sender_distribution.sender_device_key_id.hex(),
        "x3dh_initial_message": _serialize_x3dh_initial_message(x3dh_result.initial_message),
        "ratchet_message": _serialize_encrypted_message(encrypted_message),
        "device_link_cert": _serialize_cert(cert),
    }
    response_bytes = _json_bytes(response_body)
    response_body["team_device_signature"] = _sign_bytes(
        _authorizer_private_key,
        response_bytes,
    ).hex()
    bootstrap_bundle = _tokenize(response_body)
    _store_pending_linked_team_bootstrap(
        root_dir,
        participant_hex,
        bootstrap_id=bootstrap_id,
        team_id=team_id,
        peer_device_id=device_id,
        peer_team_device_public_key=proposed_team_device_public_key,
        bootstrap_bundle=bootstrap_bundle,
    )
    return {
        "bootstrap_id_hex": bootstrap_id.hex(),
        "bootstrap_bundle": bootstrap_bundle,
    }


def finalize_linked_device_bootstrap(root_dir, participant_hex, team_name, bootstrap_bundle):
    """Finish a same-member bootstrap on the joining device."""
    root_dir = pathlib.Path(root_dir)
    response = _untokenize(bootstrap_bundle)
    response_body = {
        "bootstrap_id": response["bootstrap_id"],
        "team_id": response["team_id"],
        "authorizing_team_device_public_key": response["authorizing_team_device_public_key"],
        "active_sender_device_key_id": response["active_sender_device_key_id"],
        "x3dh_initial_message": response["x3dh_initial_message"],
        "ratchet_message": response["ratchet_message"],
        "device_link_cert": response["device_link_cert"],
    }
    response_bytes = _json_bytes(response_body)
    bootstrap_id = bytes.fromhex(response["bootstrap_id"])
    response_team_id = bytes.fromhex(response["team_id"])
    authorizing_team_device_public_key = bytes.fromhex(
        response["authorizing_team_device_public_key"]
    )

    team_id, member_id = _team_row(root_dir, participant_hex, team_name)
    if response_team_id != team_id:
        raise ValueError("Bootstrap bundle team_id does not match local team")

    trusted_keys = get_trusted_device_keys_for_member(
        root_dir, participant_hex, team_name, member_id
    )
    if authorizing_team_device_public_key not in trusted_keys:
        raise ValueError("Bootstrap bundle signer is not trusted for this member")
    if not _verify_signature(
        authorizing_team_device_public_key,
        response_bytes,
        bytes.fromhex(response["team_device_signature"]),
    ):
        raise ValueError("Bootstrap bundle signature is invalid")

    session_row = _linked_team_bootstrap_session_row(root_dir, participant_hex, bootstrap_id)
    if session_row is None:
        raise ValueError("No pending linked-team bootstrap session found")
    cert = _deserialize_cert(response["device_link_cert"])
    if not verify_device_link_cert(
        cert,
        issuer_public_key=authorizing_team_device_public_key,
        team_id=team_id,
        member_id=member_id,
        subject_public_key=session_row["team_device_public_key"],
    ):
        raise ValueError("Bootstrap bundle device_link cert is invalid")
    if session_row["finalized_at"] is not None and session_row["response_payload_json"]:
        response_payload = json.loads(session_row["response_payload_json"])
        return response_payload

    identity = IdentityKeyPair(
        dh_public_key=session_row["x3dh_identity_dh_public_key"],
        dh_private_key=session_row["x3dh_identity_dh_private_key"],
        signing_public_key=session_row["x3dh_identity_signing_public_key"],
        signing_private_key=session_row["x3dh_identity_signing_private_key"],
    )
    initial_message = _deserialize_x3dh_initial_message(response["x3dh_initial_message"])
    otp_private_key = session_row["one_time_prekey_private_key"]
    used_otp_id = initial_message.used_one_time_prekey_id
    if used_otp_id is None:
        otp_private_key = None
    elif session_row["one_time_prekey_id"] != used_otp_id:
        raise ValueError("Bootstrap bundle consumed an unexpected one-time prekey")

    shared_secret = x3dh_receive(
        identity,
        session_row["signed_prekey_private_key"],
        otp_private_key,
        initial_message,
    )
    ratchet_state = _deserialize_ratchet_state(session_row["ratchet_state_json"])
    if ratchet_state is None:
        ratchet_state = initialize_as_receiver(
            shared_secret,
            (
                session_row["signed_prekey_public_key"],
                session_row["signed_prekey_private_key"],
            ),
        )

    associated_data = _json_bytes(
        {"bootstrap_id": bootstrap_id.hex(), "team_id": team_id.hex()}
    )
    ratchet_state, plaintext = ratchet_decrypt(
        ratchet_state,
        _deserialize_encrypted_message(response["ratchet_message"]),
        associated_data=associated_data,
    )
    decrypted_payload = json.loads(plaintext.decode("utf-8"))
    authorizer_distribution = deserialize_distribution_message(
        decrypted_payload["own_sender_distribution"]
    )
    save_peer_sender_key(
        device_local_db_path(root_dir, participant_hex),
        team_id,
        receiver_record_from_distribution(authorizer_distribution),
    )
    peer_sender_skipped_keys = decrypted_payload.get("peer_sender_skipped_keys", {})
    for peer_distribution_data in decrypted_payload["peer_sender_distributions"]:
        peer_distribution = deserialize_distribution_message(peer_distribution_data)
        peer_record = receiver_record_from_distribution(peer_distribution)
        raw_skipped_keys = peer_sender_skipped_keys.get(
            peer_distribution.sender_device_key_id.hex(),
            {},
        )
        if raw_skipped_keys:
            peer_record = replace(
                peer_record,
                skipped_message_keys={
                    int(iteration): bytes.fromhex(key_hex)
                    for iteration, key_hex in raw_skipped_keys.items()
                },
            )
        save_peer_sender_key(
            device_local_db_path(root_dir, participant_hex),
            team_id,
            peer_record,
        )

    team_device_private_key = session_row["team_device_private_key"]
    if team_device_private_key is None:
        raise ValueError("Bootstrap session is missing the local Team X private key")

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        current_device = _current_device_row(conn)
        device_id = current_device[0]
        existing = conn.execute(
            "SELECT public_key FROM team_device_key WHERE team_id = ? AND device_id = ?",
            (team_id, device_id),
        ).fetchone()
        if existing is None:
            key_path = _team_device_key_path(root_dir, participant_hex, team_id, device_id)
            _write_local_secret(key_path, team_device_private_key)
            conn.execute(
                """
                INSERT INTO team_device_key (team_id, device_id, public_key, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (team_id, device_id, session_row["team_device_public_key"], _now_iso()),
            )
            conn.execute(
                """
                INSERT INTO local.team_device_key_secret (team_id, device_id, private_key_ref)
                VALUES (?, ?, ?)
                """,
                (team_id, device_id, str(key_path)),
            )
            conn.commit()

    existing_sender_record = load_team_sender_key(
        device_local_db_path(root_dir, participant_hex),
        team_id,
    )
    expected_sender_device_key_id = key_id_from_public(session_row["team_device_public_key"])
    if existing_sender_record is not None:
        if existing_sender_record.sender_device_key_id != expected_sender_device_key_id:
            raise ValueError("Existing team sender key does not belong to the bootstrapped device")
        sender_distribution = distribution_message_from_record(existing_sender_record)
    else:
        sender_distribution = _initialize_team_sender_key_state(
            device_local_db_path(root_dir, participant_hex),
            team_id,
            expected_sender_device_key_id,
        )

    participant_dir = root_dir / "Participants" / participant_hex
    team_sync_dir = participant_dir / team_name / "Sync"
    team_db_path = team_sync_dir / "core.db"
    engine = _sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            cert_inserted = _insert_team_certificate_if_missing(
                conn,
                cert,
                issuer_member_id=member_id,
            )
            berth_row = conn.execute(
                text("SELECT id FROM team_app_berth LIMIT 1")
            ).fetchone()
            try:
                local_cloud = get_cloud_storage(root_dir, participant_hex)
                local_protocol = local_cloud["protocol"]
                local_url = local_cloud["url"]
                local_bucket = (
                    _bucket_name_for_protocol(local_protocol, member_id, berth_row[0])
                    if berth_row is not None
                    else None
                )
            except ValueError:
                local_protocol = None
                local_url = None
                local_bucket = None
            _upsert_team_device_row(
                conn,
                member_id,
                session_row["team_device_public_key"],
                protocol=local_protocol,
                url=local_url,
                bucket=local_bucket,
            )
            _publish_local_device_prekey_bundle(
                root_dir,
                participant_hex,
                team_name,
                conn=conn,
                team_id=team_id,
                team_device_public_key=session_row["team_device_public_key"],
            )
    finally:
        engine.dispose()
    if cert_inserted:
        repo = _Repo(team_sync_dir / ".git", team_sync_dir)
        repo.stage(["core.db"])
        repo.commit("Received device link cert from bootstrap")

    response_payload = {
        "bootstrap_id_hex": bootstrap_id.hex(),
    }

    _update_linked_team_bootstrap_session(
        root_dir,
        participant_hex,
        bootstrap_id,
        ratchet_state=ratchet_state,
        one_time_prekey_private_key=None,
        finalized_at=_now_iso(),
        response_payload_json=json.dumps(response_payload, sort_keys=True),
    )

    return response_payload


def complete_linked_device_bootstrap(
    root_dir,
    participant_hex,
    team_name,
    sender_distribution_payload,
):
    raise NotImplementedError(
        "complete_linked_device_bootstrap is retired; finalize the bootstrap and "
        "then publish the new device sender key via redistribute_sender_key"
    )


def _init_team_db(db_path):
    """Initialize a team core.db with the team schema. Returns the engine."""
    engine = _sqlite_engine(db_path)
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
    _Repo(team_sync_dir / ".git", team_sync_dir).config(
        "merge.splice-sqlite.driver", driver_cmd
    )


def _initialize_team_sender_key_state(user_db_path, team_id, sender_device_key_id):
    sender_key, distribution = create_sender_key(team_id, sender_device_key_id)
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


def _same_team_certificate_row(row, cert: KeyCertificate, issuer_member_id: bytes) -> bool:
    return (
        row[0] == cert.cert_type.value
        and row[1] == cert.subject_key_id
        and row[2] == cert.subject_public_key
        and row[3] == cert.issuer_key_id
        and row[4] == issuer_member_id
        and row[5] == cert.issued_at_iso
        and json.loads(row[6]) == cert.claims
        and row[7] == cert.signature
    )


def _insert_team_certificate_if_missing(
    conn,
    cert: KeyCertificate,
    issuer_member_id: bytes,
) -> bool:
    result = conn.execute(
        text(
            "INSERT OR IGNORE INTO key_certificate ("
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
    if result.rowcount > 0:
        return True

    existing = conn.execute(
        text(
            "SELECT cert_type, subject_key_id, subject_public_key, issuer_key_id, "
            "issuer_member_id, issued_at, claims, signature "
            "FROM key_certificate WHERE cert_id = :cert_id"
        ),
        {"cert_id": cert.cert_id},
    ).fetchone()
    if existing is None or not _same_team_certificate_row(existing, cert, issuer_member_id):
        raise ValueError("Existing team certificate does not match bootstrap certificate")
    return False


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
        )
    return certs


def _load_member_transport_announcements(conn) -> list[MemberTransportAnnouncement]:
    rows = conn.execute(
        text(
            "SELECT announcement_id, member_id, protocol, url, bucket, announced_at, "
            "signer_key_id, signature "
            "FROM member_transport_announcement ORDER BY announcement_id DESC"
        )
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


def _device_public_keys_by_key_id(conn) -> dict[bytes, bytes]:
    rows = conn.execute(
        text("SELECT device_key_id, public_key FROM team_device")
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _legacy_transport_by_member(conn) -> dict[bytes, TransportEndpoint]:
    # TEMPORARY: remove when B5 stops relying on admission-time team_device
    # transport fields as a compatibility fallback.
    rows = conn.execute(
        text(
            "SELECT member_id, protocol, url, bucket "
            "FROM team_device "
            "WHERE url IS NOT NULL "
            "ORDER BY member_id ASC, created_at ASC, device_key_id ASC"
        )
    ).fetchall()
    fallback: dict[bytes, TransportEndpoint] = {}
    for member_id, protocol, url, bucket in rows:
        if member_id in fallback:
            continue
        if protocol is None or url is None:
            continue
        fallback[member_id] = TransportEndpoint(
            protocol=protocol,
            url=url,
            bucket=bucket or "",
        )
    return fallback


def _effective_transports_by_member(
    conn,
    *,
    team_id: bytes,
    member_ids: list[bytes],
) -> dict[bytes, EffectiveTransportSelection]:
    certs = _load_team_certificates(conn, team_id)
    announcements = _load_member_transport_announcements(conn)
    device_public_keys = _device_public_keys_by_key_id(conn)
    fallback_by_member = _legacy_transport_by_member(conn)
    trusted_by_member = resolve_trusted_device_keys_by_member(certs, team_id)
    return {
        member_id: select_effective_member_transport(
            member_id=member_id,
            announcements=announcements,
            certs=certs,
            team_id=team_id,
            device_public_keys_by_key_id=device_public_keys,
            legacy_fallback=fallback_by_member.get(member_id),
            trusted_public_keys=trusted_by_member.get(member_id, set()),
        )
        for member_id in member_ids
    }


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
    engine = _sqlite_engine(team_db_path)
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
    engine = _sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            certs = _load_team_certificates(conn, team_id)
    finally:
        engine.dispose()
    return resolve_trusted_device_keys_by_member(certs, team_id)


def _team_sync_dir(root_dir, participant_hex, team_name) -> pathlib.Path:
    team_name = _validate_team_name(team_name)
    return pathlib.Path(root_dir) / "Participants" / participant_hex / team_name / "Sync"


def announce_member_transport(
    root_dir,
    participant_hex,
    team_name,
    *,
    protocol: str,
    url: str,
    bucket: str,
) -> dict:
    root_dir = pathlib.Path(root_dir)
    team_id, member_id = _team_row(root_dir, participant_hex, team_name)
    private_key, public_key = get_current_team_device_key(root_dir, participant_hex, team_name)
    announcement_id = uuid7()
    signer_key_id = key_id_from_public(public_key)
    announcement = MemberTransportAnnouncement(
        announcement_id=announcement_id,
        member_id=member_id,
        protocol=protocol,
        url=url,
        bucket=bucket,
        announced_at=_now_iso(),
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = _sign_bytes(
        private_key,
        canonical_member_transport_announcement_bytes(announcement),
    )
    signed_announcement = replace(announcement, signature=signature)
    team_db_path = _team_sync_dir(root_dir, participant_hex, team_name) / "core.db"
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO member_transport_announcement "
                    "(announcement_id, member_id, protocol, url, bucket, announced_at, "
                    "signer_key_id, signature) "
                    "VALUES (:announcement_id, :member_id, :protocol, :url, :bucket, "
                    ":announced_at, :signer_key_id, :signature)"
                ),
                {
                    "announcement_id": signed_announcement.announcement_id,
                    "member_id": signed_announcement.member_id,
                    "protocol": signed_announcement.protocol,
                    "url": signed_announcement.url,
                    "bucket": signed_announcement.bucket,
                    "announced_at": signed_announcement.announced_at,
                    "signer_key_id": signed_announcement.signer_key_id,
                    "signature": signed_announcement.signature,
                },
            )
    finally:
        engine.dispose()
    return {
        "announcement_id_hex": signed_announcement.announcement_id.hex(),
        "member_id_hex": signed_announcement.member_id.hex(),
        "signer_key_id_hex": signed_announcement.signer_key_id.hex(),
        "protocol": signed_announcement.protocol,
        "url": signed_announcement.url,
        "bucket": signed_announcement.bucket,
        "announced_at": signed_announcement.announced_at,
        "team_id_hex": team_id.hex(),
    }


def _team_db_path(root_dir, participant_hex, team_name) -> pathlib.Path:
    return _team_sync_dir(root_dir, participant_hex, team_name) / "core.db"


def _validate_team_name(team_name: str) -> str:
    if "/" in team_name or "\\" in team_name or ".." in team_name:
        raise ValueError("Invalid team name")
    return team_name


def has_local_team_clone(root_dir, participant_hex, team_name) -> bool:
    return _team_db_path(root_dir, participant_hex, team_name).exists()


def get_note_to_self_adopted_signal_count(root_dir, participant_hex, berth_id: bytes) -> int | None:
    return get_note_to_self_adopted_count(root_dir, participant_hex, berth_id)


def set_note_to_self_adopted_signal_count(
    root_dir, participant_hex, berth_id: bytes, count: int
) -> None:
    set_note_to_self_adopted_count(root_dir, participant_hex, berth_id, count)


def _core_berth_role(conn, member_id: bytes) -> str | None:
    row = conn.execute(
        text(
            "SELECT br.role "
            "FROM berth_role br "
            "JOIN team_app_berth tab ON tab.id = br.berth_id "
            "JOIN app a ON a.id = tab.app_id "
            "WHERE br.member_id = :member_id AND a.name = 'SmallSeaCollectiveCore' "
            "LIMIT 1"
        ),
        {"member_id": member_id},
    ).fetchone()
    return row[0] if row is not None else None


def _delete_peer_sender_key_rows(db_path: str | pathlib.Path, team_id: bytes, sender_device_key_ids: list[bytes]):
    if not sender_device_key_ids:
        return
    placeholders = ", ".join("?" for _ in sender_device_key_ids)
    params = [team_id, *sender_device_key_ids]
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"DELETE FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id IN ({placeholders})",
            params,
        )
        conn.commit()


def _runtime_reconciliation_state_row(root_dir, participant_hex: str, team_id: bytes):
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT team_id,
                   trusted_member_ids_json,
                   trusted_device_key_ids_json,
                   last_sender_device_key_id,
                   last_sender_chain_id,
                   updated_at
            FROM runtime_reconciliation_state
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()


def _store_runtime_reconciliation_state(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    trusted_member_ids_hex: list[str],
    trusted_device_key_ids_hex: list[str],
    last_sender_device_key_id: bytes | None,
    last_sender_chain_id: bytes | None,
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runtime_reconciliation_state (
                team_id,
                trusted_member_ids_json,
                trusted_device_key_ids_json,
                last_sender_device_key_id,
                last_sender_chain_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                _json_dumps_sorted(sorted(trusted_member_ids_hex)),
                _json_dumps_sorted(sorted(trusted_device_key_ids_hex)),
                last_sender_device_key_id,
                last_sender_chain_id,
                _now_iso(),
            ),
        )
        conn.commit()


def _delivery_exists(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    sender_device_key_id: bytes,
    sender_chain_id: bytes,
    target_device_key_id: bytes,
) -> bool:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM redistribution_delivery
            WHERE team_id = ?
              AND sender_device_key_id = ?
              AND sender_chain_id = ?
              AND target_device_key_id = ?
            """,
            (team_id, sender_device_key_id, sender_chain_id, target_device_key_id),
        ).fetchone()
    return row is not None


def mark_redistribution_delivery(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    sender_device_key_id: bytes,
    sender_chain_id: bytes,
    target_device_key_id: bytes,
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO redistribution_delivery (
                team_id,
                sender_device_key_id,
                sender_chain_id,
                target_device_key_id,
                delivered_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                team_id,
                sender_device_key_id,
                sender_chain_id,
                target_device_key_id,
                _now_iso(),
            ),
        )
        conn.commit()


def _receipt_exists(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    sender_device_key_id: bytes,
    sender_chain_id: bytes,
    target_device_key_id: bytes,
) -> bool:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM redistribution_receipt
            WHERE team_id = ?
              AND sender_device_key_id = ?
              AND sender_chain_id = ?
              AND target_device_key_id = ?
            """,
            (team_id, sender_device_key_id, sender_chain_id, target_device_key_id),
        ).fetchone()
    return row is not None


def mark_redistribution_receipt(
    root_dir,
    participant_hex: str,
    *,
    team_id: bytes,
    sender_device_key_id: bytes,
    sender_chain_id: bytes,
    target_device_key_id: bytes,
) -> None:
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO redistribution_receipt (
                team_id,
                sender_device_key_id,
                sender_chain_id,
                target_device_key_id,
                received_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                team_id,
                sender_device_key_id,
                sender_chain_id,
                target_device_key_id,
                _now_iso(),
            ),
        )
        conn.commit()


def runtime_redistribution_artifact_path(target_device_key_id: bytes, sender_device_key_id: bytes) -> str:
    return (
        "runtime/redistribution/"
        f"{target_device_key_id.hex()}/{sender_device_key_id.hex()}.json"
    )


def peek_redistribution_payload_metadata(distribution_payload: str) -> dict:
    payload = _untokenize(distribution_payload)
    return {
        "team_id_hex": payload["team_id"],
        "sender_device_key_id_hex": payload["sender_device_key_id"],
        "sender_chain_id_hex": payload["sender_chain_id"],
        "target_device_key_id_hex": payload["target_device_key_id"],
    }


def rotate_team_sender_key(root_dir, participant_hex, team_name):
    root_dir = pathlib.Path(root_dir)
    team_id, _member_id = _team_row(root_dir, participant_hex, team_name)
    _team_device_private_key, team_device_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    sender_device_key_id = key_id_from_public(team_device_public_key)
    sender_record, distribution = create_sender_key(team_id, sender_device_key_id)
    user_db_path = device_local_db_path(root_dir, participant_hex)
    save_team_sender_key(user_db_path, team_id, sender_record)
    save_peer_sender_key(user_db_path, team_id, receiver_record_from_distribution(distribution))
    return {
        "team_id_hex": team_id.hex(),
        "sender_device_key_id_hex": distribution.sender_device_key_id.hex(),
        "sender_distribution": serialize_distribution_message(distribution),
    }


def redistribute_sender_key(root_dir, participant_hex, team_name, target_device_key_ids=None):
    root_dir = pathlib.Path(root_dir)
    team_id, _self_in_team = _team_row(root_dir, participant_hex, team_name)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    user_db_path = device_local_db_path(root_dir, participant_hex)
    sender_record = load_team_sender_key(user_db_path, team_id)
    if sender_record is None:
        raise ValueError(f"No local sender key found for team '{team_name}'")

    sender_distribution = distribution_message_from_record(sender_record)
    plaintext = _json_bytes(serialize_distribution_message(sender_distribution))
    _sender_private_key, sender_team_device_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    sender_device_key_id = key_id_from_public(sender_team_device_public_key)
    requested_target_ids = None
    if target_device_key_ids is not None:
        requested_target_ids = {
            bytes.fromhex(target) if isinstance(target, str) else target
            for target in target_device_key_ids
        }

    trusted_public_keys_by_member = get_trusted_device_keys_by_member(
        root_dir, participant_hex, team_name
    )
    candidate_public_keys: dict[bytes, bytes] = {}
    for member_id, public_keys in trusted_public_keys_by_member.items():
        for public_key in public_keys:
            device_key_id = key_id_from_public(public_key)
            if device_key_id == sender_device_key_id:
                continue
            if requested_target_ids is not None and device_key_id not in requested_target_ids:
                continue
            candidate_public_keys[device_key_id] = public_key

    engine = _sqlite_engine(team_db_path)
    artifacts = []
    skipped = []
    try:
        with engine.begin() as conn:
            for device_key_id, _public_key in sorted(candidate_public_keys.items()):
                bundle = _load_device_prekey_bundle(conn, device_key_id)
                if bundle is None:
                    skipped.append(device_key_id.hex())
                    continue
                sender_identity = generate_identity_key_pair()
                x3dh_result = x3dh_send(sender_identity, bundle)
                ratchet_state = initialize_as_sender(
                    x3dh_result.shared_secret,
                    x3dh_result.signed_prekey_public,
                )
                associated_data = _json_bytes(
                    {
                        "team_id": team_id.hex(),
                        "sender_device_key_id": sender_device_key_id.hex(),
                        "target_device_key_id": device_key_id.hex(),
                    }
                )
                ratchet_state, encrypted_message = ratchet_encrypt(
                    ratchet_state,
                    plaintext,
                    associated_data=associated_data,
                )
                artifact_body = {
                    "team_id": team_id.hex(),
                    "sender_team_device_public_key": sender_team_device_public_key.hex(),
                    "sender_device_key_id": sender_device_key_id.hex(),
                    "sender_chain_id": sender_distribution.sender_chain_id.hex(),
                    "target_device_key_id": device_key_id.hex(),
                    "x3dh_initial_message": _serialize_x3dh_initial_message(
                        x3dh_result.initial_message
                    ),
                    "ratchet_message": _serialize_encrypted_message(encrypted_message),
                }
                artifact_bytes = _json_bytes(artifact_body)
                artifact_body["team_device_signature"] = _sign_bytes(
                    _sender_private_key,
                    artifact_bytes,
                ).hex()
                artifacts.append(
                    {
                        "target_device_key_id_hex": device_key_id.hex(),
                        "sender_device_key_id_hex": sender_device_key_id.hex(),
                        "sender_chain_id_hex": sender_distribution.sender_chain_id.hex(),
                        "artifact_path": runtime_redistribution_artifact_path(
                            device_key_id,
                            sender_device_key_id,
                        ),
                        "distribution_payload": _tokenize(artifact_body),
                    }
                )
    finally:
        engine.dispose()

    return {
        "team_id_hex": team_id.hex(),
        "sender_device_key_id_hex": sender_device_key_id.hex(),
        "artifacts": artifacts,
        "skipped_device_key_ids_hex": skipped,
    }


def receive_sender_key_distribution(root_dir, participant_hex, team_name, distribution_payload):
    root_dir = pathlib.Path(root_dir)
    payload = _untokenize(distribution_payload)
    payload_body = {
        "team_id": payload["team_id"],
        "sender_team_device_public_key": payload["sender_team_device_public_key"],
        "sender_device_key_id": payload["sender_device_key_id"],
        "sender_chain_id": payload["sender_chain_id"],
        "target_device_key_id": payload["target_device_key_id"],
        "x3dh_initial_message": payload["x3dh_initial_message"],
        "ratchet_message": payload["ratchet_message"],
    }
    payload_bytes = _json_bytes(payload_body)

    team_id, _member_id = _team_row(root_dir, participant_hex, team_name)
    if bytes.fromhex(payload["team_id"]) != team_id:
        raise ValueError("Distribution payload team_id does not match local team")

    _local_private_key, local_team_device_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    local_device_key_id = key_id_from_public(local_team_device_public_key)
    target_device_key_id = bytes.fromhex(payload["target_device_key_id"])
    if target_device_key_id != local_device_key_id:
        raise ValueError("Distribution payload is not addressed to this device")

    sender_team_device_public_key = bytes.fromhex(payload["sender_team_device_public_key"])
    sender_device_key_id = bytes.fromhex(payload["sender_device_key_id"])
    if sender_device_key_id != key_id_from_public(sender_team_device_public_key):
        raise ValueError("Distribution payload sender key id does not match signer key")
    if not _verify_signature(
        sender_team_device_public_key,
        payload_bytes,
        bytes.fromhex(payload["team_device_signature"]),
    ):
        raise ValueError("Distribution payload signature is invalid")

    trusted_public_keys = {
        public_key
        for public_keys in get_trusted_device_keys_by_member(
            root_dir, participant_hex, team_name
        ).values()
        for public_key in public_keys
    }
    if sender_team_device_public_key not in trusted_public_keys:
        raise ValueError("Distribution payload signer is not trusted in this team")

    prekey_state = _redistribution_prekey_state_row(root_dir, participant_hex, team_id)
    if prekey_state is None:
        raise ValueError(f"No redistribution prekey state found for team '{team_name}'")

    identity = IdentityKeyPair(
        dh_public_key=prekey_state["identity_dh_public_key"],
        dh_private_key=prekey_state["identity_dh_private_key"],
        signing_public_key=prekey_state["identity_signing_public_key"],
        signing_private_key=prekey_state["identity_signing_private_key"],
    )
    initial_message = _deserialize_x3dh_initial_message(payload["x3dh_initial_message"])
    used_otp_id = initial_message.used_one_time_prekey_id
    otp_private_key = None
    if used_otp_id is not None:
        otp_private_key = _redistribution_one_time_prekey_private(
            root_dir,
            participant_hex,
            team_id=team_id,
            prekey_id=used_otp_id,
        )
        if otp_private_key is None:
            raise ValueError("Distribution payload consumed an unavailable one-time prekey")

    shared_secret = x3dh_receive(
        identity,
        prekey_state["signed_prekey_private_key"],
        otp_private_key,
        initial_message,
    )
    ratchet_state = initialize_as_receiver(
        shared_secret,
        (
            prekey_state["signed_prekey_public_key"],
            prekey_state["signed_prekey_private_key"],
        ),
    )
    associated_data = _json_bytes(
        {
            "team_id": team_id.hex(),
            "sender_device_key_id": sender_device_key_id.hex(),
            "target_device_key_id": target_device_key_id.hex(),
        }
    )
    _ratchet_state, plaintext = ratchet_decrypt(
        ratchet_state,
        _deserialize_encrypted_message(payload["ratchet_message"]),
        associated_data=associated_data,
    )
    if used_otp_id is not None:
        _consume_redistribution_one_time_prekey(
            root_dir,
            participant_hex,
            team_id=team_id,
            prekey_id=used_otp_id,
    )
    distribution = deserialize_distribution_message(json.loads(plaintext.decode("utf-8")))
    if distribution.sender_device_key_id != sender_device_key_id:
        raise ValueError("Distribution payload sender stream does not match decrypted sender key")
    if distribution.sender_chain_id.hex() != payload["sender_chain_id"]:
        raise ValueError("Distribution payload sender chain does not match decrypted sender key")
    save_peer_sender_key(
        device_local_db_path(root_dir, participant_hex),
        team_id,
        receiver_record_from_distribution(distribution),
    )
    return {
        "team_id_hex": team_id.hex(),
        "sender_device_key_id_hex": sender_device_key_id.hex(),
        "sender_chain_id_hex": distribution.sender_chain_id.hex(),
        "target_device_key_id_hex": target_device_key_id.hex(),
    }


def reconcile_runtime_state(root_dir, participant_hex, team_name):
    root_dir = pathlib.Path(root_dir)
    team_id, self_in_team = _team_row(root_dir, participant_hex, team_name)
    local_db_path = device_local_db_path(root_dir, participant_hex)
    _local_private_key, local_team_device_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    local_device_key_id = key_id_from_public(local_team_device_public_key)
    trusted_public_keys_by_member = get_trusted_device_keys_by_member(
        root_dir,
        participant_hex,
        team_name,
    )
    trusted_self_public_keys = trusted_public_keys_by_member.get(self_in_team, [])
    if local_team_device_public_key not in trusted_self_public_keys:
        return {
            "team_id_hex": team_id.hex(),
            "local_device_key_id_hex": local_device_key_id.hex(),
            "local_device_trusted": False,
            "rotated": False,
            "removed_member_ids_hex": [],
            "redistribution_artifacts": [],
            "skipped_device_key_ids_hex": [],
        }

    current_trusted_member_ids_hex = sorted(
        member_id.hex() for member_id in trusted_public_keys_by_member
    )
    current_trusted_device_key_ids = sorted(
        {
            key_id_from_public(public_key)
            for public_keys in trusted_public_keys_by_member.values()
            for public_key in public_keys
        }
    )
    current_trusted_device_key_ids_hex = sorted(
        device_key_id.hex() for device_key_id in current_trusted_device_key_ids
    )

    state_row = _runtime_reconciliation_state_row(root_dir, participant_hex, team_id)
    previous_member_ids_hex = set()
    if state_row is not None:
        previous_member_ids_hex = set(json.loads(state_row["trusted_member_ids_json"]))

    removed_member_ids_hex = sorted(
        previous_member_ids_hex - set(current_trusted_member_ids_hex) - {self_in_team.hex()}
    )
    rotated = False
    if removed_member_ids_hex:
        rotate_team_sender_key(root_dir, participant_hex, team_name)
        rotated = True

    sender_record = load_team_sender_key(local_db_path, team_id)
    if sender_record is None:
        raise ValueError(f"No local sender key found for team '{team_name}'")
    pending_target_device_key_ids = []
    for device_key_id in current_trusted_device_key_ids:
        if device_key_id == sender_record.sender_device_key_id:
            continue
        if not _delivery_exists(
            root_dir,
            participant_hex,
            team_id=team_id,
            sender_device_key_id=sender_record.sender_device_key_id,
            sender_chain_id=sender_record.chain_id,
            target_device_key_id=device_key_id,
        ):
            pending_target_device_key_ids.append(device_key_id)

    if pending_target_device_key_ids:
        redistribution = redistribute_sender_key(
            root_dir,
            participant_hex,
            team_name,
            target_device_key_ids=pending_target_device_key_ids,
        )
        artifacts = redistribution["artifacts"]
        skipped = redistribution["skipped_device_key_ids_hex"]
    else:
        artifacts = []
        skipped = []

    _store_runtime_reconciliation_state(
        root_dir,
        participant_hex,
        team_id=team_id,
        trusted_member_ids_hex=current_trusted_member_ids_hex,
        trusted_device_key_ids_hex=current_trusted_device_key_ids_hex,
        last_sender_device_key_id=sender_record.sender_device_key_id,
        last_sender_chain_id=sender_record.chain_id,
    )
    return {
        "team_id_hex": team_id.hex(),
        "local_device_key_id_hex": local_device_key_id.hex(),
        "local_device_trusted": True,
        "rotated": rotated,
        "removed_member_ids_hex": removed_member_ids_hex,
        "sender_device_key_id_hex": sender_record.sender_device_key_id.hex(),
        "sender_chain_id_hex": sender_record.chain_id.hex(),
        "redistribution_artifacts": artifacts,
        "skipped_device_key_ids_hex": skipped,
    }


def remove_member(root_dir, participant_hex, team_name, member):
    root_dir = pathlib.Path(root_dir)
    removed_member_id = bytes.fromhex(member) if isinstance(member, str) else member
    team_id, self_in_team = _team_row(root_dir, participant_hex, team_name)
    if removed_member_id == self_in_team:
        raise ValueError("remove_member cannot remove self in this branch")

    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    removed_device_public_keys = get_trusted_device_keys_for_member(
        root_dir, participant_hex, team_name, removed_member_id
    )
    removed_device_key_ids = [key_id_from_public(public_key) for public_key in removed_device_public_keys]

    try:
        with engine.begin() as conn:
            member_exists = conn.execute(
                text("SELECT 1 FROM member WHERE id = :member_id"),
                {"member_id": removed_member_id},
            ).fetchone()
            if member_exists is None:
                raise ValueError(f"Member '{removed_member_id.hex()}' not found")
            role = _core_berth_role(conn, self_in_team)
            if role != "read-write":
                raise ValueError("Removing a member requires read-write permission on the Core berth")
            certs = _load_team_certificates(conn, team_id)
            cert_ids_to_delete = [
                cert.cert_id
                for cert in certs
                if cert.claims.get("member_id") == removed_member_id.hex()
            ]
            for cert_id in cert_ids_to_delete:
                conn.execute(
                    text("DELETE FROM key_certificate WHERE cert_id = :cert_id"),
                    {"cert_id": cert_id},
                )
            conn.execute(
                text("DELETE FROM member WHERE id = :member_id"),
                {"member_id": removed_member_id},
            )
            _publish_local_device_prekey_bundle(
                root_dir,
                participant_hex,
                team_name,
                conn=conn,
                team_id=team_id,
                refresh=True,
            )
    finally:
        engine.dispose()

    team_sync_dir = _team_sync_dir(root_dir, participant_hex, team_name)
    repo = _Repo(team_sync_dir / ".git", team_sync_dir)
    repo.stage(["core.db"])
    repo.commit(f"Removed member {removed_member_id.hex()}")

    _delete_peer_sender_key_rows(
        device_local_db_path(root_dir, participant_hex),
        team_id,
        removed_device_key_ids,
    )
    rotated = rotate_team_sender_key(root_dir, participant_hex, team_name)
    redistribution = redistribute_sender_key(root_dir, participant_hex, team_name)
    return {
        "team_id_hex": team_id.hex(),
        "removed_member_id_hex": removed_member_id.hex(),
        "removed_device_key_ids_hex": [device_key_id.hex() for device_key_id in removed_device_key_ids],
        "rotated_sender_device_key_id_hex": rotated["sender_device_key_id_hex"],
        "redistribution_artifacts": redistribution["artifacts"],
        "skipped_device_key_ids_hex": redistribution["skipped_device_key_ids_hex"],
    }


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
    engine = _sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            _store_team_certificate(conn, cert, issuer_member_id=member_id)
            _upsert_team_device_row(
                conn,
                member_id,
                linked_device_public_key,
            )
    finally:
        engine.dispose()

    repo = _Repo(team_sync_dir / ".git", team_sync_dir)
    repo.stage(["core.db"])
    repo.commit("Linked additional team device")
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
        sender_device_key_id=bytes.fromhex(data["sender_device_key_id"]),
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
        sender_device_key_id=next_sender_key.sender_device_key_id,
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
        device_local_db_path(root_dir, participant_hex),
        team_id,
        key_id_from_public(team_keys["device_key"].public_key),
    )

    # --- Create team directory and its core.db ---
    team_sync_dir = participant_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    team_db_path = team_sync_dir / "core.db"
    team_engine = _init_team_db(team_db_path)

    # Populate the team DB: creator member, app, berth, and creator's role.
    creator_display_name = get_nickname(root_dir, participant_hex) or None
    with team_engine.begin() as conn:
        _upsert_member_row(conn, member_id, display_name=creator_display_name)
        _app_id, berth_id, _changed = _ensure_team_app_activation(
            conn,
            "SmallSeaCollectiveCore",
            lambda row_member_id: "read-write" if row_member_id == member_id else None,
        )
        try:
            creator_cloud = get_cloud_storage(root_dir, participant_hex)
            creator_protocol = creator_cloud["protocol"]
            creator_url = creator_cloud["url"]
            creator_bucket = _bucket_name_for_protocol(
                creator_protocol, member_id, berth_id
            )
        except ValueError:
            creator_protocol = None
            creator_url = None
            creator_bucket = None
        _upsert_team_device_row(
            conn,
            member_id,
            team_keys["device_key"].public_key,
            protocol=creator_protocol,
            url=creator_url,
            bucket=creator_bucket,
        )
        _store_team_certificate(conn, membership_cert, issuer_member_id=member_id)
        _publish_local_device_prekey_bundle(
            root_dir,
            participant_hex,
            team_name,
            conn=conn,
            team_id=team_id,
            team_device_public_key=team_keys["device_key"].public_key,
        )

    # --- Git init ---
    repo = _Repo.init(team_sync_dir / ".git").with_work_tree(team_sync_dir)
    _install_sqlite_merge_driver(team_sync_dir)
    repo.stage(["core.db", ".gitattributes"])
    repo.commit(f"New team: {team_name}")

    return {
        "team_id_hex": team_id.hex(),
        "member_id_hex": member_id.hex(),
        "berth_id_hex": berth_id.hex(),
    }


def _single_app_id_by_name(conn, app_name):
    rows = conn.execute(
        "SELECT id FROM app WHERE name = ?",
        (app_name,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError(f"Multiple app rows found for friendly name '{app_name}'")
    return rows[0][0] if rows else None


def _single_app_id_by_name_sa(conn, app_name):
    rows = conn.execute(
        text("SELECT id FROM app WHERE name = :name"),
        {"name": app_name},
    ).fetchall()
    if len(rows) > 1:
        raise ValueError(f"Multiple app rows found for friendly name '{app_name}'")
    return rows[0][0] if rows else None


def _ensure_participant_app_registration(conn, team_id, app_name):
    """Ensure NoteToSelf has an app row and berth for this participant app."""
    changed = False
    app_id = _single_app_id_by_name(conn, app_name)
    if app_id is None:
        app_id = uuid7()
        conn.execute(
            "INSERT INTO app (id, name) VALUES (?, ?)",
            (app_id, app_name),
        )
        changed = True

    berth_row = conn.execute(
        "SELECT id FROM team_app_berth WHERE team_id = ? AND app_id = ?",
        (team_id, app_id),
    ).fetchone()
    if berth_row is None:
        conn.execute(
            "INSERT INTO team_app_berth (id, team_id, app_id) VALUES (?, ?, ?)",
            (uuid7(), team_id, app_id),
        )
        changed = True

    return app_id, changed


def register_app_for_participant(root_dir, participant_hex, app_name):
    """Register an app for a participant's identity via NoteToSelf."""
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex
    app_dir = participant_dir / "NoteToSelf" / app_name
    changed = False

    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        team_row = conn.execute(
            "SELECT id FROM team WHERE name = ?",
            ("NoteToSelf",),
        ).fetchone()
        if team_row is None:
            raise ValueError("NoteToSelf team row not found")
        team_id = team_row[0]

        app_id, changed = _ensure_participant_app_registration(
            conn, team_id, app_name
        )
        conn.commit()

    if not app_dir.exists():
        app_dir.mkdir(parents=True)
        changed = True

    if changed:
        repo_dir = participant_dir / "NoteToSelf" / "Sync"
        nts_repo = _Repo(repo_dir / ".git", repo_dir)
        nts_repo.stage(["core.db"])
        nts_repo.commit(f"Registered app {app_name}")

    return app_id.hex()


def _ensure_team_app_activation(conn, app_name, role_for_member):
    """Ensure a team DB has an app berth and member roles for that app."""
    changed = False
    app_id = _single_app_id_by_name_sa(conn, app_name)
    if app_id is None:
        app_id = uuid7()
        conn.execute(
            text("INSERT INTO app (id, name) VALUES (:id, :name)"),
            {"id": app_id, "name": app_name},
        )
        changed = True

    berth_rows = conn.execute(
        text("SELECT id FROM team_app_berth WHERE app_id = :app_id"),
        {"app_id": app_id},
    ).fetchall()
    if len(berth_rows) > 1:
        raise ValueError(f"Multiple berths found for app '{app_name}'")
    if berth_rows:
        berth_id = berth_rows[0][0]
    else:
        berth_id = uuid7()
        conn.execute(
            text("INSERT INTO team_app_berth (id, app_id) VALUES (:id, :app_id)"),
            {"id": berth_id, "app_id": app_id},
        )
        changed = True

    member_rows = conn.execute(text("SELECT id FROM member")).fetchall()
    for (member_id,) in member_rows:
        role_row = conn.execute(
            text(
                "SELECT 1 FROM berth_role "
                "WHERE member_id = :member_id AND berth_id = :berth_id"
            ),
            {"member_id": member_id, "berth_id": berth_id},
        ).fetchone()
        if role_row is not None:
            continue
        role = role_for_member(member_id)
        if role is None:
            raise ValueError("Cannot activate app for member without Core berth role")
        conn.execute(
            text(
                "INSERT INTO berth_role (id, member_id, berth_id, role) "
                "VALUES (:id, :member_id, :berth_id, :role)"
            ),
            {
                "id": uuid7(),
                "member_id": member_id,
                "berth_id": berth_id,
                "role": role,
            },
        )
        changed = True

    return app_id, berth_id, changed


def activate_app_for_team(root_dir, participant_hex, team_name, app_name):
    """Activate an app berth for a team using generic Manager provisioning."""
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex
    team_sync_dir = participant_dir / team_name / "Sync"
    team_db_path = team_sync_dir / "core.db"
    engine = _sqlite_engine(team_db_path)
    changed = False

    with engine.begin() as conn:
        app_id, _berth_id, changed = _ensure_team_app_activation(
            conn,
            app_name,
            lambda member_id: _core_berth_role(conn, member_id),
        )

    engine.dispose()

    if changed:
        repo = _Repo(team_sync_dir / ".git", team_sync_dir)
        repo.stage(["core.db"])
        repo.commit(f"Activated app {app_name}")

    return app_id.hex()


def create_invitation(
    root_dir, participant_hex, team_name, inviter_cloud, invitee_label=None, role="admin"
):
    """Create a transcript-bound admission proposal token for a team."""
    _role_to_core_berth_role(role)
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
    ensure_team_db_schema(team_db_path)
    team_engine = _sqlite_engine(team_db_path)
    team_sync_dir = participant_dir / team_name / "Sync"

    inviter_sender_key = load_team_sender_key(
        device_local_db_path(root_dir, participant_hex), team_id
    )
    if inviter_sender_key is None:
        raise ValueError(f"No sender key found for team '{team_name}'")
    _inviter_private_key, _inviter_device_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )

    # Look up the berth ID from the team DB (to derive the bucket name).
    # Berth structural data lives in the team DB, not NoteToSelf.
    with team_engine.begin() as conn:
        berth_row = conn.execute(
            text("SELECT id FROM team_app_berth LIMIT 1")
        ).fetchone()
    if berth_row is None:
        raise ValueError(f"No berth found in team DB for '{team_name}'")
    inviter_bucket = _bucket_name_for_protocol(inviter_cloud["protocol"], inviter_member_id, berth_row[0])

    proposal_id = uuid7()
    invitee_member_id = uuid7()
    nonce = secrets.token_bytes(16)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    with team_engine.begin() as conn:
        quorum, expiry_seconds = _team_admission_policy(conn)
        if quorum < 1:
            raise ValueError("Admission quorum must be at least 1")
        anchor_commit = _team_head_commit(team_sync_dir)
        governance_snapshot = _governance_snapshot(conn)
        governance_digest = _governance_digest(governance_snapshot)
        conn.execute(
            text(
                "INSERT INTO admission_proposal ("
                "proposal_id, nonce, team_id, inviter_member_id, invitee_member_id, "
                "invitee_label, role, anchor_commit, governance_digest, governance_snapshot_json, "
                "state, created_at, expires_at"
                ") VALUES ("
                ":proposal_id, :nonce, :team_id, :inviter_member_id, :invitee_member_id, "
                ":invitee_label, :role, :anchor_commit, :governance_digest, :governance_snapshot_json, "
                "'awaiting_invitee', :created_at, :expires_at)"
            ),
            {
                "proposal_id": proposal_id,
                "nonce": nonce,
                "team_id": team_id,
                "inviter_member_id": inviter_member_id,
                "invitee_member_id": invitee_member_id,
                "invitee_label": invitee_label,
                "role": role,
                "anchor_commit": anchor_commit,
                "governance_digest": governance_digest,
                "governance_snapshot_json": _json_dumps_sorted(governance_snapshot),
                "created_at": now,
                "expires_at": _proposal_expiry(now_dt, expiry_seconds),
            },
        )

    # Build token with only the material the invitee needs for transcript-bound admission.
    token_data = {
        "proposal_id": proposal_id.hex(),
        "invitation_id": proposal_id.hex(),
        "nonce": nonce.hex(),
        "team_id": team_id.hex(),
        "team_name": team_name,
        "inviter_member_id": inviter_member_id.hex(),
        "invitee_member_id": invitee_member_id.hex(),
        "inviter_display_name": inviter_display_name,
        "invitee_label": invitee_label,
        "role": role,
        "inviter_cloud": {"protocol": inviter_cloud["protocol"], "url": inviter_cloud["url"]},
        "inviter_bucket": inviter_bucket,
        "inviter_sender_key": serialize_sender_key_record(inviter_sender_key),
    }
    token_b64 = _tokenize(token_data)

    repo = _Repo(team_sync_dir / ".git", team_sync_dir)
    repo.stage(["core.db"])
    repo.commit("Created admission proposal")

    return token_b64


def accept_invitation(
    root_dir,
    acceptor_participant_hex,
    token_b64,
    inviter_remote,
    acceptor_remote=None,
    acceptor_member_id=None,
):
    """Accept a transcript-bound team invitation token (invitee side).

    The invitee may clone the repo and prepare local keys, but does not write
    their own admission into the team DB.
    """
    root_dir = pathlib.Path(root_dir)

    token = _untokenize(token_b64)
    team_name = token["team_name"]
    team_id = bytes.fromhex(token["team_id"])
    inviter_member_id = bytes.fromhex(token["inviter_member_id"])
    inviter_cloud = token["inviter_cloud"]  # protocol + url only, no credentials
    inviter_bucket = token["inviter_bucket"]
    inviter_display_name = token.get("inviter_display_name") or None
    inviter_sender_key = deserialize_distribution_message(token["inviter_sender_key"])
    proposal_id = bytes.fromhex(token["proposal_id"])
    nonce = bytes.fromhex(token["nonce"])
    invitee_member_id = bytes.fromhex(token["invitee_member_id"])

    # Use pre-generated member ID if provided (required when acceptor_remote must
    # be constructed before this call, e.g. Dropbox folder-prefix naming).
    if acceptor_member_id is None:
        acceptor_member_id = invitee_member_id
    if acceptor_member_id != invitee_member_id:
        raise ValueError("Invitation token member binding mismatch")

    acceptor_dir = root_dir / "Participants" / acceptor_participant_hex

    # --- Create acceptor's team directory ---
    team_sync_dir = acceptor_dir / team_name / "Sync"
    os.makedirs(team_sync_dir, exist_ok=False)

    # --- Clone the team repo from inviter's cloud ---
    # Use git init + fetch_from_remote + checkout rather than clone_from_remote,
    # so this works when the workspace lives inside an existing git repo.

    repo = _Repo.init(team_sync_dir / ".git").with_work_tree(team_sync_dir)

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
        repo.checkout_branch("main", start_point=result)
    finally:
        os.chdir(saved_cwd)

    # --- Install sqlite merge driver ---
    _install_sqlite_merge_driver(team_sync_dir)

    with attached_note_to_self_connection(root_dir, acceptor_participant_hex) as conn:
        existing = conn.execute(
            "SELECT 1 FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Team '{team_name}' already exists in NoteToSelf")
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, acceptor_member_id),
        )
        # Core participant registration is identity-wide in NoteToSelf. Hub
        # berth resolution currently accepts any NoteToSelf Core berth for the
        # app, so accepting a team invitation does not add a per-team Core row.
        conn.commit()

    # --- Generate local current device key ---
    team_keys = _generate_initial_team_device_key(
        root_dir, acceptor_participant_hex, team_id
    )
    acceptor_device_key_id = key_id_from_public(team_keys["device_key"].public_key)

    team_db_path = team_sync_dir / "core.db"
    ensure_team_db_schema(team_db_path)
    # Team sender state is local-only and does not publish admission.
    save_peer_sender_key(
        device_local_db_path(root_dir, acceptor_participant_hex),
        team_id,
        receiver_record_from_distribution(inviter_sender_key),
    )
    _initialize_team_sender_key_state(
        device_local_db_path(root_dir, acceptor_participant_hex),
        team_id,
        key_id_from_public(team_keys["device_key"].public_key),
    )

    with attached_note_to_self_connection(root_dir, acceptor_participant_hex) as conn:
        device_row = _current_device_row(conn)
        invitee_bootstrap_key = device_row[1]
    transcript_payload = _proposal_transcript_payload(
        proposal_id=proposal_id,
        nonce=nonce,
        team_id=team_id,
        invitee_member_id=acceptor_member_id,
        invitee_device_public_key=team_keys["device_key"].public_key,
        invitee_bootstrap_key=invitee_bootstrap_key,
    )
    acceptance_signature = _sign_bytes(
        team_keys["device_private_key"],
        _json_bytes(transcript_payload),
    )

    acceptance_data = {
        "proposal_id": proposal_id.hex(),
        "invitation_id": proposal_id.hex(),
        "nonce": nonce.hex(),
        "team_id": team_id.hex(),
        "invitee_member_id": acceptor_member_id.hex(),
        "acceptor_member_id": acceptor_member_id.hex(),
        "invitee_device_key_id": acceptor_device_key_id.hex(),
        "acceptor_device_key_id": acceptor_device_key_id.hex(),
        "invitee_device_public_key": team_keys["device_key"].public_key.hex(),
        "acceptor_device_public_key": team_keys["device_key"].public_key.hex(),
        "invitee_bootstrap_key": invitee_bootstrap_key.hex(),
        "acceptance_signature": acceptance_signature.hex(),
    }
    return _tokenize(acceptance_data)


def complete_invitation_acceptance(
    root_dir, participant_hex, team_name, acceptance_b64
):
    """Record acceptance and finalize immediately when quorum is met."""
    root_dir = pathlib.Path(root_dir)
    participant_dir = root_dir / "Participants" / participant_hex

    acceptance = _untokenize(acceptance_b64)
    proposal_id = bytes.fromhex(acceptance["proposal_id"])
    nonce = bytes.fromhex(acceptance["nonce"])
    team_id = bytes.fromhex(acceptance["team_id"])
    invitee_member_id = bytes.fromhex(acceptance["invitee_member_id"])
    invitee_device_key_id = bytes.fromhex(acceptance["invitee_device_key_id"])
    invitee_device_public_key = bytes.fromhex(acceptance["invitee_device_public_key"])
    invitee_bootstrap_key = bytes.fromhex(acceptance["invitee_bootstrap_key"])
    acceptance_signature = bytes.fromhex(acceptance["acceptance_signature"])

    team_db_path = participant_dir / team_name / "Sync" / "core.db"
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    inviter_private_key, inviter_public_key = get_current_team_device_key(
        root_dir, participant_hex, team_name
    )
    inviter_device_key = _participant_key_from_public(inviter_public_key)
    acceptor_device_key = _participant_key_from_public(invitee_device_public_key)

    user_db_path = participant_dir / "NoteToSelf" / "Sync" / "core.db"
    user_engine = _sqlite_engine(user_db_path)
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
        admitted_member_id=invitee_member_id,
    )
    if not verify_membership_cert(
        membership_cert,
        issuer_public_key=inviter_public_key,
        team_id=team_id,
        issuer_member_id=inviter_member_id,
        admitted_member_id=invitee_member_id,
        subject_public_key=invitee_device_public_key,
    ):
        raise ValueError("Failed to issue a valid membership cert for the invitee")

    failure_reason = None
    with engine.begin() as conn:
        proposal_row = _load_admission_proposal_row(conn, proposal_id)
        ok, reason = _proposal_is_still_valid(conn, proposal_row)
        if not ok:
            failure_reason = reason
        elif proposal_row[10] != "awaiting_invitee":
            raise ValueError(f"Proposal is not awaiting invitee acceptance (state: {proposal_row[10]})")
        elif proposal_row[1] != nonce:
            raise ValueError("Nonce mismatch")
        elif proposal_row[2] != team_id:
            raise ValueError("Acceptance team_id does not match proposal")
        elif proposal_row[4] != invitee_member_id:
            raise ValueError("Acceptance member_id does not match proposal")
        else:
            transcript_payload = _proposal_transcript_payload(
                proposal_id=proposal_id,
                nonce=nonce,
                team_id=team_id,
                invitee_member_id=invitee_member_id,
                invitee_device_public_key=invitee_device_public_key,
                invitee_bootstrap_key=invitee_bootstrap_key,
            )
            if key_id_from_public(invitee_device_public_key) != invitee_device_key_id:
                raise ValueError("Acceptance device_key_id does not match invitee public key")
            if not _verify_signature(
                invitee_device_public_key,
                _json_bytes(transcript_payload),
                acceptance_signature,
            ):
                raise ValueError("Acceptance signature is invalid")

            transcript_digest = _proposal_transcript_digest(transcript_payload)
            now = _now_iso()
            quorum = _proposal_quorum(conn)
            conn.execute(
                text(
                    "UPDATE admission_proposal SET "
                    "state = :state, acceptance_recorded_at = :acceptance_recorded_at, "
                    "invitee_device_public_key = :invitee_device_public_key, "
                    "invitee_bootstrap_key = :invitee_bootstrap_key, "
                    "acceptance_signature = :acceptance_signature, "
                    "transcript_digest = :transcript_digest, transcript_json = :transcript_json "
                    "WHERE proposal_id = :proposal_id"
                ),
                {
                    "proposal_id": proposal_id,
                    "state": "awaiting_quorum" if quorum > 1 else "awaiting_invitee",
                    "acceptance_recorded_at": now,
                    "invitee_device_public_key": invitee_device_public_key,
                    "invitee_bootstrap_key": invitee_bootstrap_key,
                    "acceptance_signature": acceptance_signature,
                    "transcript_digest": transcript_digest,
                    "transcript_json": _json_dumps_sorted(transcript_payload),
                },
            )
            approval_signature = _sign_bytes(
                inviter_private_key,
                _approval_payload(
                    proposal_id=proposal_id,
                    transcript_digest=transcript_digest,
                    admin_member_id=inviter_member_id,
                ),
            )
            _insert_admin_approval(
                conn,
                proposal_id=proposal_id,
                admin_member_id=inviter_member_id,
                approver_device_key_id=key_id_from_public(inviter_public_key),
                signature=approval_signature,
                transcript_digest=transcript_digest,
            )

            refreshed_row = _load_admission_proposal_row(conn, proposal_id)
            if _proposal_has_quorum(conn, refreshed_row):
                _upsert_member_row(conn, invitee_member_id, display_name=proposal_row[5])
                _store_team_certificate(conn, membership_cert, issuer_member_id=inviter_member_id)
                stored_device_key_id = _upsert_team_device_row(
                    conn,
                    invitee_member_id,
                    invitee_device_public_key,
                )
                if stored_device_key_id != invitee_device_key_id:
                    raise ValueError("Acceptance device_key_id does not match invitee public key")

                berth_rows = conn.execute(text("SELECT id FROM team_app_berth")).fetchall()
                core_role = _role_to_core_berth_role(proposal_row[6])
                for berth_row in berth_rows:
                    conn.execute(
                        text(
                            "INSERT INTO berth_role (id, member_id, berth_id, role) "
                            "VALUES (:id, :member_id, :berth_id, :role)"
                        ),
                        {
                            "id": uuid7(),
                            "member_id": invitee_member_id,
                            "berth_id": berth_row[0],
                            "role": core_role,
                        },
                    )
                finalization_signature = _sign_bytes(
                    inviter_private_key,
                    _finalization_payload(
                        proposal_id=proposal_id,
                        transcript_digest=transcript_digest,
                        invitee_member_id=invitee_member_id,
                    ),
                )
                conn.execute(
                    text(
                        "UPDATE admission_proposal SET state = 'finalized', "
                        "finalized_at = :finalized_at, finalization_signature = :finalization_signature "
                        "WHERE proposal_id = :proposal_id"
                    ),
                    {
                        "proposal_id": proposal_id,
                        "finalized_at": now,
                        "finalization_signature": finalization_signature,
                    },
                )
            else:
                conn.execute(
                    text(
                        "UPDATE admission_proposal SET state = 'awaiting_quorum' "
                        "WHERE proposal_id = :proposal_id"
                    ),
                    {"proposal_id": proposal_id},
                )

    engine.dispose()
    if failure_reason is not None:
        raise ValueError(failure_reason)

    team_sync_dir = participant_dir / team_name / "Sync"
    repo = _Repo(team_sync_dir / ".git", team_sync_dir)
    repo.stage(["core.db"])
    repo.commit("Recorded admission acceptance")


def sign_admin_approval(root_dir, participant_hex, team_name, proposal_id_hex):
    root_dir = pathlib.Path(root_dir)
    proposal_id = bytes.fromhex(proposal_id_hex)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    approver_private_key, approver_public_key = get_current_team_device_key(
        root_dir,
        participant_hex,
        team_name,
    )
    approver_device_key_id = key_id_from_public(approver_public_key)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            "SELECT self_in_team FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Team '{team_name}' not found in NoteToSelf")
    admin_member_id = row[0]
    failure_reason = None
    try:
        with engine.begin() as conn:
            proposal_row = _load_admission_proposal_row(conn, proposal_id)
            ok, reason = _proposal_is_still_valid(conn, proposal_row)
            if not ok:
                failure_reason = reason
            elif proposal_row[17] is None:
                raise ValueError("Proposal does not have a recorded transcript yet")
            else:
                snapshot = _load_governance_snapshot_json(proposal_row)
                if not _proposal_admin_device_is_valid(snapshot, admin_member_id, approver_device_key_id):
                    raise ValueError("Approver device was not linked to an admin at the proposal anchor")
                signature = _sign_bytes(
                    approver_private_key,
                    _approval_payload(
                        proposal_id=proposal_id,
                        transcript_digest=proposal_row[17],
                        admin_member_id=admin_member_id,
                    ),
                )
                _insert_admin_approval(
                    conn,
                    proposal_id=proposal_id,
                    admin_member_id=admin_member_id,
                    approver_device_key_id=approver_device_key_id,
                    signature=signature,
                    transcript_digest=proposal_row[17],
                )
    finally:
        engine.dispose()
    if failure_reason is not None:
        raise ValueError(failure_reason)
    repo_dir = _team_sync_dir(root_dir, participant_hex, team_name)
    repo = _Repo(repo_dir / ".git", repo_dir)
    repo.stage(["core.db"])
    repo.commit("Recorded admin approval")


def finalize_admission(root_dir, participant_hex, team_name, proposal_id_hex):
    root_dir = pathlib.Path(root_dir)
    proposal_id = bytes.fromhex(proposal_id_hex)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    inviter_private_key, inviter_public_key = get_current_team_device_key(
        root_dir,
        participant_hex,
        team_name,
    )
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            "SELECT self_in_team FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Team '{team_name}' not found in NoteToSelf")
    self_member_id = row[0]
    failure_reason = None
    try:
        with engine.begin() as conn:
            proposal_row = _load_admission_proposal_row(conn, proposal_id)
            ok, reason = _proposal_is_still_valid(conn, proposal_row)
            if not ok:
                failure_reason = reason
            elif proposal_row[3] != self_member_id:
                raise ValueError("Only the inviter may finalize this proposal")
            elif proposal_row[17] is None or proposal_row[14] is None:
                raise ValueError("Proposal transcript is incomplete")
            elif not _proposal_has_quorum(conn, proposal_row):
                raise ValueError("Proposal has not met quorum")
            else:
                invitee_member_id = proposal_row[4]
                invitee_device_public_key = proposal_row[14]
                membership_cert = issue_membership_cert(
                    subject_key=_participant_key_from_public(invitee_device_public_key),
                    issuer_key=_participant_key_from_public(inviter_public_key),
                    issuer_private_key=inviter_private_key,
                    team_id=proposal_row[2],
                    issuer_member_id=self_member_id,
                    admitted_member_id=invitee_member_id,
                )
                _upsert_member_row(conn, invitee_member_id, display_name=proposal_row[5])
                _store_team_certificate(conn, membership_cert, issuer_member_id=self_member_id)
                _upsert_team_device_row(conn, invitee_member_id, invitee_device_public_key)
                berth_rows = conn.execute(text("SELECT id FROM team_app_berth")).fetchall()
                role = _role_to_core_berth_role(proposal_row[6])
                for berth_row in berth_rows:
                    existing = conn.execute(
                        text(
                            "SELECT 1 FROM berth_role WHERE member_id = :member_id AND berth_id = :berth_id"
                        ),
                        {"member_id": invitee_member_id, "berth_id": berth_row[0]},
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            text(
                                "INSERT INTO berth_role (id, member_id, berth_id, role) "
                                "VALUES (:id, :member_id, :berth_id, :role)"
                            ),
                            {
                                "id": uuid7(),
                                "member_id": invitee_member_id,
                                "berth_id": berth_row[0],
                                "role": role,
                            },
                        )
                conn.execute(
                    text(
                        "UPDATE admission_proposal SET state = 'finalized', "
                        "finalized_at = :finalized_at, finalization_signature = :finalization_signature "
                        "WHERE proposal_id = :proposal_id"
                    ),
                    {
                        "proposal_id": proposal_id,
                        "finalized_at": _now_iso(),
                        "finalization_signature": _sign_bytes(
                            inviter_private_key,
                            _finalization_payload(
                                proposal_id=proposal_id,
                                transcript_digest=proposal_row[17],
                                invitee_member_id=invitee_member_id,
                            ),
                        ),
                    },
                )
    finally:
        engine.dispose()
    if failure_reason is not None:
        raise ValueError(failure_reason)
    repo_dir = _team_sync_dir(root_dir, participant_hex, team_name)
    repo = _Repo(repo_dir / ".git", repo_dir)
    repo.stage(["core.db"])
    repo.commit("Finalized admission proposal")


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
    """Invalidate an open admission proposal."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = (
        root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    invitation_id = bytes.fromhex(invitation_id_hex)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT state FROM admission_proposal WHERE proposal_id = :id"),
            {"id": invitation_id},
        ).fetchone()
        if row is None:
            raise ValueError("Admission proposal not found")
        if row[0] not in {"awaiting_invitee", "awaiting_quorum"}:
            raise ValueError(f"Proposal is not revocable (state: {row[0]})")
        conn.execute(
            text(
                "UPDATE admission_proposal SET state = 'invalidated', invalid_reason = 'revoked' "
                "WHERE proposal_id = :id"
            ),
            {"id": invitation_id},
        )
    engine.dispose()
    team_sync_dir = root_dir / "Participants" / participant_hex / team_name / "Sync"
    repo = _Repo(team_sync_dir / ".git", team_sync_dir)
    repo.stage(["core.db"])
    repo.commit("Revoked admission proposal")


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


def get_self_in_team(root_dir, participant_hex, team_name):
    """Return this participant's member ID for one team, or None if absent."""
    root_dir = pathlib.Path(root_dir)
    with attached_note_to_self_connection(root_dir, participant_hex) as conn:
        row = conn.execute(
            "SELECT self_in_team FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    return row[0].hex() if row is not None else None


def list_members(root_dir, participant_hex, team_name):
    """List members of a team with their berth roles. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_id, self_in_team = _team_row(root_dir, participant_hex, team_name)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    engine = _sqlite_engine(team_db_path)

    with engine.begin() as conn:
        members = conn.execute(text("SELECT id, display_name FROM member")).fetchall()
        role_rows = conn.execute(
            text("SELECT member_id, berth_id, role FROM berth_role")
        ).fetchall()
        transport_by_member = _effective_transports_by_member(
            conn,
            team_id=team_id,
            member_ids=[row[0] for row in members],
        )

    engine.dispose()

    roles_by_member = {}
    for r in role_rows:
        key = r[0].hex()
        roles_by_member.setdefault(key, []).append(
            {"berth_id": r[1].hex(), "role": r[2]}
        )

    result = []
    for row in members:
        member_id = row[0]
        transport = transport_by_member.get(member_id)
        transport_dict = None
        if transport is not None and transport.transport is not None:
            transport_dict = {
                "protocol": transport.transport.protocol,
                "url": transport.transport.url,
                "bucket": transport.transport.bucket,
            }
        result.append(
            {
                "id": member_id.hex(),
                "display_name": row[1],
                "berth_roles": roles_by_member.get(member_id.hex(), []),
                "transport_status": transport.status if transport is not None else "missing",
                "effective_transport": transport_dict,
                "needs_transport_announcement": (
                    member_id == self_in_team
                    and (transport is None or transport.status == "missing")
                ),
            }
        )
    return result


def list_invitations(root_dir, participant_hex, team_name):
    """List admission proposals for a team. Returns list of dicts."""
    root_dir = pathlib.Path(root_dir)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    ensure_team_db_schema(team_db_path)
    engine = _sqlite_engine(team_db_path)

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT proposal_id, state, invitee_label, role, created_at "
                "FROM admission_proposal ORDER BY created_at DESC, proposal_id DESC"
            )
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


def _admission_event_store_path(root_dir, participant_hex, team_name):
    root_dir = pathlib.Path(root_dir)
    team_name = _validate_team_name(team_name)
    return root_dir / "Participants" / participant_hex / team_name / "admission-events-local.db"


def _team_db_path(root_dir, participant_hex, team_name):
    root_dir = pathlib.Path(root_dir)
    team_name = _validate_team_name(team_name)
    return root_dir / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _create_admission_event_disposition_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE admission_event_disposition (
            event_type TEXT NOT NULL,
            artifact_id BLOB NOT NULL,
            disposition TEXT NOT NULL CHECK(disposition IN ('dismissed', 'notified')),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_type, artifact_id, disposition)
        )
        """
    )


def _ensure_admission_event_store_meta(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_event_store_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _admission_event_disposition_sql(conn) -> str | None:
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'admission_event_disposition'
        """
    ).fetchone()
    if row is None:
        return None
    return row[0] or ""


def _admission_event_store_needs_rebuild(conn) -> bool:
    sql = _admission_event_disposition_sql(conn)
    if sql is None:
        return False
    normalized = " ".join(sql.split())
    return (
        "CHECK(disposition IN ('dismissed', 'notified'))" not in normalized
        or "PRIMARY KEY (event_type, artifact_id, disposition)" not in normalized
    )


def _rebuild_admission_event_disposition_table(conn) -> None:
    conn.execute("ALTER TABLE admission_event_disposition RENAME TO admission_event_disposition_old")
    _create_admission_event_disposition_table(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO admission_event_disposition
        (event_type, artifact_id, disposition, updated_at)
        SELECT event_type, artifact_id, disposition, updated_at
        FROM admission_event_disposition_old
        WHERE disposition IN ('dismissed', 'notified')
        """
    )
    conn.execute("DROP TABLE admission_event_disposition_old")


def _visible_linked_device_artifact_ids(conn, team_db_path) -> list[bytes]:
    if team_db_path is None:
        return []
    team_db_path = pathlib.Path(team_db_path)
    if not team_db_path.exists():
        return []
    conn.execute("ATTACH DATABASE ? AS teamdb", (str(team_db_path),))
    try:
        rows = conn.execute(
            """
            SELECT cert_id
            FROM teamdb.key_certificate
            WHERE cert_type = 'device_link'
            """
        ).fetchall()
    finally:
        conn.execute("DETACH DATABASE teamdb")
    return [row[0] for row in rows]


def _maybe_seed_linked_device_notification_backlog(conn, team_db_path) -> None:
    _ensure_admission_event_store_meta(conn)
    seed_key = "linked_device_notification_seeded_v1"
    row = conn.execute(
        "SELECT value FROM admission_event_store_meta WHERE key = ?",
        (seed_key,),
    ).fetchone()
    if row is not None:
        return

    artifact_ids = _visible_linked_device_artifact_ids(conn, team_db_path)
    now_iso = _now_iso()
    conn.executemany(
        """
        INSERT OR IGNORE INTO admission_event_disposition
        (event_type, artifact_id, disposition, updated_at)
        VALUES (?, ?, 'notified', ?)
        """,
        [
            ("linked_device", artifact_id, now_iso)
            for artifact_id in artifact_ids
        ],
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO admission_event_store_meta (key, value)
        VALUES (?, ?)
        """,
        (seed_key, now_iso),
    )


def _ensure_admission_event_store(conn, *, team_db_path=None) -> None:
    # Callers are responsible for transaction scope / commit behavior.
    sql = _admission_event_disposition_sql(conn)
    if sql is None:
        _create_admission_event_disposition_table(conn)
    elif _admission_event_store_needs_rebuild(conn):
        _rebuild_admission_event_disposition_table(conn)
    _ensure_admission_event_store_meta(conn)
    _maybe_seed_linked_device_notification_backlog(conn, team_db_path)


def _list_admission_event_dispositions(
    root_dir,
    participant_hex,
    team_name,
    *,
    dispositions: tuple[str, ...],
):
    db_path = _admission_event_store_path(root_dir, participant_hex, team_name)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    with sqlite3.connect(db_path) as conn:
        _ensure_admission_event_store(conn, team_db_path=team_db_path)
        placeholders = ", ".join("?" for _ in dispositions)
        rows = conn.execute(
            f"""
            SELECT event_type, artifact_id
            FROM admission_event_disposition
            WHERE disposition IN ({placeholders})
            """,
            dispositions,
        ).fetchall()
    return {(row[0], row[1].hex()) for row in rows}


def list_dismissed_admission_events(root_dir, participant_hex, team_name):
    return _list_admission_event_dispositions(
        root_dir,
        participant_hex,
        team_name,
        dispositions=("dismissed",),
    )


def list_notified_admission_events(root_dir, participant_hex, team_name):
    return _list_admission_event_dispositions(
        root_dir,
        participant_hex,
        team_name,
        dispositions=("notified",),
    )


def dismiss_admission_event(root_dir, participant_hex, team_name, event_type, artifact_id_hex):
    """Persist a local dismissal for one admission event."""
    db_path = _admission_event_store_path(root_dir, participant_hex, team_name)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_admission_event_store(conn, team_db_path=team_db_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO admission_event_disposition
            (event_type, artifact_id, disposition, updated_at)
            VALUES (?, ?, 'dismissed', ?)
            """,
            (event_type, bytes.fromhex(artifact_id_hex), _now_iso()),
        )
        conn.commit()


def mark_admission_event_notified(root_dir, participant_hex, team_name, event_type, artifact_id_hex):
    """Persist a local "notified" mark for one admission event."""
    db_path = _admission_event_store_path(root_dir, participant_hex, team_name)
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_admission_event_store(conn, team_db_path=team_db_path)
        conn.execute(
            """
            INSERT OR IGNORE INTO admission_event_disposition
            (event_type, artifact_id, disposition, updated_at)
            VALUES (?, ?, 'notified', ?)
            """,
            (event_type, bytes.fromhex(artifact_id_hex), _now_iso()),
        )
        conn.commit()


def _ensure_participant_app_disposition_store(conn) -> None:
    # V1 dispositions key by friendly name. This intentionally follows the
    # current branch-plan risk posture: duplicate-name/unification semantics are
    # deferred until app_unification exists.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS participant_app_sighting_disposition (
            app_name TEXT PRIMARY KEY,
            disposition TEXT NOT NULL CHECK(disposition IN ('dismissed')),
            updated_at TEXT NOT NULL
        )
        """
    )


def _ensure_team_app_disposition_store(conn) -> None:
    # V1 dispositions key by friendly name. This intentionally follows the
    # current branch-plan risk posture: duplicate-name/unification semantics are
    # deferred until app_unification exists.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_app_sighting_disposition (
            app_name TEXT PRIMARY KEY,
            disposition TEXT NOT NULL CHECK(disposition IN ('dismissed')),
            updated_at TEXT NOT NULL
        )
        """
    )


def dismiss_participant_app_sighting(root_dir, participant_hex, app_name):
    """Suppress participant-level prompts for an app on this device."""
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        _ensure_participant_app_disposition_store(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO participant_app_sighting_disposition
            (app_name, disposition, updated_at)
            VALUES (?, 'dismissed', ?)
            """,
            (app_name, _now_iso()),
        )
        conn.commit()


def dismiss_team_app_sighting(root_dir, participant_hex, team_name, app_name):
    """Suppress team-scoped prompts for an app on this device."""
    with attached_note_to_self_connection(root_dir, participant_hex) as nts:
        team_row = nts.execute(
            "SELECT 1 FROM team WHERE name = ?",
            (team_name,),
        ).fetchone()
    if team_row is None:
        raise ValueError(f"Team '{team_name}' not found in NoteToSelf")

    db_path = _admission_event_store_path(root_dir, participant_hex, team_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_team_app_disposition_store(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO team_app_sighting_disposition
            (app_name, disposition, updated_at)
            VALUES (?, 'dismissed', ?)
            """,
            (app_name, _now_iso()),
        )
        conn.commit()


def app_sighting_dismissed(root_dir, participant_hex, sighting):
    """Return True if Manager should suppress this sighting on this device."""
    app_name = sighting["app_name"]
    team_name = sighting.get("team_name")
    with sqlite3.connect(device_local_db_path(root_dir, participant_hex)) as conn:
        _ensure_participant_app_disposition_store(conn)
        row = conn.execute(
            """
            SELECT 1 FROM participant_app_sighting_disposition
            WHERE app_name = ? AND disposition = 'dismissed'
            """,
            (app_name,),
        ).fetchone()
        if row is not None:
            return True

    if team_name:
        db_path = _admission_event_store_path(root_dir, participant_hex, team_name)
        with sqlite3.connect(db_path) as conn:
            _ensure_team_app_disposition_store(conn)
            row = conn.execute(
                """
                SELECT 1 FROM team_app_sighting_disposition
                WHERE app_name = ? AND disposition = 'dismissed'
                """,
                (app_name,),
            ).fetchone()
            if row is not None:
                return True
    return False
