import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from cuttlefish.group import (
    SenderKeyDistributionMessage,
    SenderKeyRecord,
    process_sender_key_distribution,
)


def distribution_message_from_record(record: SenderKeyRecord) -> SenderKeyDistributionMessage:
    return SenderKeyDistributionMessage(
        group_id=record.group_id,
        sender_participant_id=record.sender_participant_id,
        sender_chain_id=record.chain_id,
        iteration=record.iteration,
        chain_key=record.chain_key,
        signing_public_key=record.signing_public_key,
    )


def serialize_distribution_message(msg: SenderKeyDistributionMessage) -> dict:
    return {
        "group_id": msg.group_id.hex(),
        "sender_participant_id": msg.sender_participant_id.hex(),
        "sender_chain_id": msg.sender_chain_id.hex(),
        "iteration": msg.iteration,
        "chain_key": msg.chain_key.hex(),
        "signing_public_key": msg.signing_public_key.hex(),
    }


def deserialize_distribution_message(data: dict) -> SenderKeyDistributionMessage:
    return SenderKeyDistributionMessage(
        group_id=bytes.fromhex(data["group_id"]),
        sender_participant_id=bytes.fromhex(data["sender_participant_id"]),
        sender_chain_id=bytes.fromhex(data["sender_chain_id"]),
        iteration=int(data["iteration"]),
        chain_key=bytes.fromhex(data["chain_key"]),
        signing_public_key=bytes.fromhex(data["signing_public_key"]),
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
        sender_participant_id=row["sender_participant_id"],
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
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {table_name} (
                team_id,
                group_id,
                sender_participant_id,
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
                record.sender_participant_id,
                record.chain_id,
                record.chain_key,
                record.iteration,
                record.signing_public_key,
                record.signing_private_key,
                _serialize_skipped(record.skipped_message_keys),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def save_team_sender_key(db_path: str | Path, team_id: bytes, record: SenderKeyRecord) -> None:
    _save_record(db_path, "team_sender_key", team_id, record)


def save_peer_sender_key(db_path: str | Path, team_id: bytes, record: SenderKeyRecord) -> None:
    _save_record(db_path, "peer_sender_key", team_id, record)


def load_team_sender_key(db_path: str | Path, team_id: bytes) -> SenderKeyRecord | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT group_id, sender_participant_id, chain_id, chain_key, iteration,
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
    db_path: str | Path, team_id: bytes, sender_participant_id: bytes
) -> SenderKeyRecord | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT group_id, sender_participant_id, chain_id, chain_key, iteration,
                   signing_public_key, signing_private_key, skipped_message_keys
            FROM peer_sender_key
            WHERE team_id = ? AND sender_participant_id = ?
            """,
            (team_id, sender_participant_id),
        ).fetchone()
    finally:
        conn.close()
    return _record_from_row(row)
