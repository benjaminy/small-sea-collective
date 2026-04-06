import json
from dataclasses import replace

from cuttlefish.group import (_advance_chain_key, _derive_message_key, GroupMessage,
                              group_decrypt, group_encrypt)
from small_sea_manager.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    save_peer_sender_key,
    save_team_sender_key,
)


def serialize_group_message(message: GroupMessage) -> bytes:
    return json.dumps(
        {
            "sender_participant_id": message.sender_participant_id.hex(),
            "sender_chain_id": message.sender_chain_id.hex(),
            "iteration": message.iteration,
            "iv": message.iv.hex(),
            "ciphertext": message.ciphertext.hex(),
            "signature": message.signature.hex(),
        },
        sort_keys=True,
    ).encode("utf-8")


def deserialize_group_message(payload: bytes) -> GroupMessage:
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


def prepare_encrypted_upload(ss_session, plaintext: bytes) -> tuple[object, bytes]:
    user_db_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
    sender_key = load_team_sender_key(user_db_path, ss_session.team_id)
    if sender_key is None:
        raise ValueError(f"No team sender key for {ss_session.team_name!r}")
    next_sender_key, message = group_encrypt(ss_session.team_id, sender_key, plaintext)
    return next_sender_key, serialize_group_message(message)


def commit_encrypted_upload(ss_session, next_sender_key) -> None:
    user_db_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
    save_team_sender_key(user_db_path, ss_session.team_id, next_sender_key)


def decrypt_group_payload(ss_session, payload: bytes) -> bytes:
    user_db_path = ss_session.participant_path / "NoteToSelf" / "Sync" / "core.db"
    message = deserialize_group_message(payload)
    sender_key = load_peer_sender_key(
        user_db_path, ss_session.team_id, message.sender_participant_id
    )
    if sender_key is None:
        raise ValueError(
            f"Missing sender key for participant {message.sender_participant_id.hex()}"
        )
    replay_message_key = _message_key_for(message, sender_key)
    next_sender_key, plaintext = group_decrypt(message, sender_key)
    replayable_keys = dict(next_sender_key.skipped_message_keys)
    replayable_keys[message.iteration] = replay_message_key
    next_sender_key = replace(next_sender_key, skipped_message_keys=replayable_keys)
    save_peer_sender_key(user_db_path, ss_session.team_id, next_sender_key)
    return plaintext
