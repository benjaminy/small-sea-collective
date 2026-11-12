import json
import sqlite3
from pathlib import Path

from cuttlefish.group import (
    SenderKeyDistributionMessage,
    SenderKeyRecord,
    process_sender_key_distribution,
)


def distribution_message_from_record(record: SenderKeyRecord) -> SenderKeyDistributionMessage:
    return SenderKeyDistributionMessage(
        group_id=record.group_id,
        sender_device_key_id=record.sender_device_key_id,
        sender_chain_id=record.chain_id,
        iteration=record.iteration,
        chain_key=record.chain_key,
        signing_public_key=record.signing_public_key,
    )


def serialize_distribution_message(msg: SenderKeyDistributionMessage) -> dict:
    return {
        "group_id": msg.group_id.hex(),
        "sender_device_key_id": msg.sender_device_key_id.hex(),
        "sender_chain_id": msg.sender_chain_id.hex(),
        "iteration": msg.iteration,
        "chain_key": msg.chain_key.hex(),
        "signing_public_key": msg.signing_public_key.hex(),
    }


def serialize_sender_key_record(record: SenderKeyRecord) -> dict:
    return {
        "group_id": record.group_id.hex(),
        "sender_device_key_id": record.sender_device_key_id.hex(),
        "sender_chain_id": record.chain_id.hex(),
        "iteration": record.iteration,
        "chain_key": record.chain_key.hex(),
        "signing_public_key": record.signing_public_key.hex(),
        "skipped_message_keys": {
            str(iteration): key.hex()
            for iteration, key in record.skipped_message_keys.items()
        },
    }


def deserialize_distribution_message(data: dict) -> SenderKeyDistributionMessage:
    return SenderKeyDistributionMessage(
        group_id=bytes.fromhex(data["group_id"]),
        sender_device_key_id=bytes.fromhex(data["sender_device_key_id"]),
        sender_chain_id=bytes.fromhex(data["sender_chain_id"]),
        iteration=int(data["iteration"]),
        chain_key=bytes.fromhex(data["chain_key"]),
        signing_public_key=bytes.fromhex(data["signing_public_key"]),
    )


def deserialize_sender_key_record(data: dict) -> SenderKeyRecord:
    return SenderKeyRecord(
        group_id=bytes.fromhex(data["group_id"]),
        sender_device_key_id=bytes.fromhex(data["sender_device_key_id"]),
        chain_id=bytes.fromhex(data["sender_chain_id"]),
        chain_key=bytes.fromhex(data["chain_key"]),
        iteration=int(data["iteration"]),
        signing_public_key=bytes.fromhex(data["signing_public_key"]),
        signing_private_key=None,
        skipped_message_keys={
            int(iteration): bytes.fromhex(key_hex)
            for iteration, key_hex in data.get("skipped_message_keys", {}).items()
        },
    )


def receiver_record_from_distribution(msg: SenderKeyDistributionMessage) -> SenderKeyRecord:
    return process_sender_key_distribution(msg)


def _serialize_skipped(skipped_message_keys: dict[int, bytes]) -> str:
    return json.dumps(
        {str(iteration): key.hex() for iteration, key in skipped_message_keys.items()},
        sort_keys=True,
    )


def _deserialize_skipped(raw_value: str | None) -> dict[int, bytes]:
    if not raw_value:
        return {}
    raw_dict = json.loads(raw_value)
    return {int(iteration): bytes.fromhex(key_hex) for iteration, key_hex in raw_dict.items()}


def _record_from_row(row) -> SenderKeyRecord | None:
    if row is None:
        return None
    return SenderKeyRecord(
        group_id=row["group_id"],
        sender_device_key_id=row["sender_device_key_id"],
        chain_id=row["chain_id"],
        chain_key=row["chain_key"],
        iteration=row["iteration"],
        signing_public_key=row["signing_public_key"],
        signing_private_key=row["signing_private_key"],
        skipped_message_keys=_deserialize_skipped(row["skipped_message_keys"]),
    )


def _save_record(
    db_path: str | Path,
    table_name: str,
    team_id: bytes,
    record: SenderKeyRecord,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    conn = connection if connection is not None else sqlite3.connect(str(db_path))
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {table_name} (
                team_id,
                group_id,
                sender_device_key_id,
                chain_id,
                chain_key,
                iteration,
                signing_public_key,
                signing_private_key,
                skipped_message_keys
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                record.group_id,
                record.sender_device_key_id,
                record.chain_id,
                record.chain_key,
                record.iteration,
                record.signing_public_key,
                record.signing_private_key,
                _serialize_skipped(record.skipped_message_keys),
            ),
        )
        if connection is None:
            conn.commit()
    finally:
        if connection is None:
            conn.close()


def save_team_sender_key(db_path: str | Path, team_id: bytes, record: SenderKeyRecord) -> None:
    _save_record(db_path, "team_sender_key", team_id, record)


def save_peer_sender_key(
    db_path: str | Path,
    team_id: bytes,
    record: SenderKeyRecord,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Update an installed chain after decryption without creating another chain."""
    conn = connection if connection is not None else sqlite3.connect(str(db_path))
    try:
        updated = conn.execute(
            """
            UPDATE peer_sender_key
            SET chain_key = ?, iteration = ?, skipped_message_keys = ?
            WHERE team_id = ? AND sender_device_key_id = ? AND chain_id = ?
            """,
            (record.chain_key, record.iteration, _serialize_skipped(record.skipped_message_keys),
             team_id, record.sender_device_key_id, record.chain_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Cannot update a receiver chain that is not installed")
        if connection is None:
            conn.commit()
    finally:
        if connection is None:
            conn.close()


def install_peer_sender_key(
    db_path: str | Path,
    team_id: bytes,
    record: SenderKeyRecord,
    *,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Install a distribution without replacing a chain already held locally.

    Repeated or delayed distributions must not discard retained message keys
    or rewind a receiver. Chain IDs identify chains; they do not order them.
    A supplied connection owns its transaction.
    """
    conn = connection if connection is not None else sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO peer_sender_key (
                team_id, group_id, sender_device_key_id, chain_id, chain_key,
                iteration, signing_public_key, signing_private_key, skipped_message_keys
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (team_id, sender_device_key_id, chain_id) DO NOTHING
            """,
            (team_id, record.group_id, record.sender_device_key_id, record.chain_id,
             record.chain_key, record.iteration, record.signing_public_key,
             record.signing_private_key, _serialize_skipped(record.skipped_message_keys)),
        )
        if connection is None:
            conn.commit()
    finally:
        if connection is None:
            conn.close()


def load_team_sender_key(db_path: str | Path, team_id: bytes) -> SenderKeyRecord | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT group_id, sender_device_key_id, chain_id, chain_key, iteration,
                   signing_public_key, signing_private_key, skipped_message_keys
            FROM team_sender_key
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()
    finally:
        conn.close()
    return _record_from_row(row)


def load_peer_sender_key(
    db_path: str | Path, team_id: bytes, sender_device_key_id: bytes, chain_id: bytes
) -> SenderKeyRecord | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT group_id, sender_device_key_id, chain_id, chain_key, iteration,
                   signing_public_key, signing_private_key, skipped_message_keys
            FROM peer_sender_key
            WHERE team_id = ? AND sender_device_key_id = ? AND chain_id = ?
            """,
            (team_id, sender_device_key_id, chain_id),
        ).fetchone()
    finally:
        conn.close()
    return _record_from_row(row)


def load_all_peer_sender_keys(db_path: str | Path, team_id: bytes) -> list[SenderKeyRecord]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT group_id, sender_device_key_id, chain_id, chain_key, iteration,
                   signing_public_key, signing_private_key, skipped_message_keys
            FROM peer_sender_key
            WHERE team_id = ?
            ORDER BY sender_device_key_id ASC, chain_id ASC
            """,
            (team_id,),
        ).fetchall()
    finally:
        conn.close()
    records = []
    for row in rows:
        record = _record_from_row(row)
        if record is not None:
            records.append(record)
    return records
