import json
from dataclasses import replace

from cryptography.exceptions import InvalidSignature, InvalidTag
from cuttlefish.group import (_advance_chain_key, _derive_message_key, ContextMismatch,
                              GroupMessage, InvalidIteration, SenderHeaderMismatch, group_decrypt,
                              group_encrypt)
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    save_peer_sender_key,
    save_team_sender_key,
)


class PublicationExn(Exception):
    """An encrypted object could not be accepted as an authentic publication.

    Never an absent object and never a successful read: every subclass keeps a
    caller from mistaking a refused publication for empty storage.
    """

    error_code = "publication_failed"


class PublicationPendingExn(PublicationExn):
    """Evidence needed to judge this publication has not arrived on this device.

    Distinct from a rejection: nothing here says the bytes are bad, only that
    the local view cannot decide yet. Retrying is the caller's business; the
    Hub runs no polling or repair on its behalf.
    """


class SenderKeyUnavailableExn(PublicationPendingExn):
    """The device holds no sender key for the peer device that wrote a payload.

    Sender-key delivery is a separate exchange that may simply not have
    arrived yet.
    """

    error_code = "sender_key_unavailable"


class OwnershipProjectionAbsentExn(PublicationPendingExn):
    """This session has no accepted device-ownership projection at all.

    Its own outcome, not a missing row: a session kind that never carries the
    projection would otherwise look like a team whose sync is merely late, and
    an empty mapping must never be read as permission to skip the check.
    """

    error_code = "ownership_projection_absent"


class DeviceOwnershipUnavailableExn(PublicationPendingExn):
    """Core has no accepted association for the device that signed the payload.

    Normal during admission — a new invitee holds its own sender key before its
    accepted ownership row reaches its snapshot — and also what an invalid
    claim looks like. The reader cannot yet tell those apart.
    """

    error_code = "device_ownership_unavailable"


class DeviceOwnershipAmbiguousExn(PublicationPendingExn):
    """Core associates the signing device with more than one teammate.

    Left for a person to resolve. Picking a winner here would silently decide a
    membership question on a download path.
    """

    error_code = "device_ownership_ambiguous"


class ExpectedPublisherUnavailableExn(PublicationPendingExn):
    """The read cannot name the teammate it expects to have published.

    Reads are never accepted without that expectation, so an unresolvable
    publisher pauses the operation rather than widening it to any known sender.
    """

    error_code = "expected_publisher_unknown"


class PublicationNotAuthenticExn(PublicationExn):
    """The retrieved bytes are not this object's authentic publication."""

    error_code = "publication_not_authentic"

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        super().__init__(detail)


class UnexpectedPublisherExn(PublicationNotAuthenticExn):
    """The signing device belongs to a teammate other than the expected one."""

    def __init__(self, detail: str):
        super().__init__("unexpected_publisher", detail)


# --- Publication context ---

PUBLICATION_CONTEXT_PURPOSE = "small-sea/object-publication"
PUBLICATION_CONTEXT_VERSION = 1

ENVELOPE_FORMAT = "small-sea/group-publication/1"


def publication_context(team_id: bytes, berth_id: bytes, path: str) -> bytes:
    """The authenticated logical coordinates of one published object.

    Logical, not physical: the same object copied to another authorized
    location keeps its context, which is what lets a retained candidate be
    inspected. `path` is the exact string the Hub received after JSON or
    query-parameter decoding, with no normalization — two different strings are
    two different objects even where a provider treats them as aliases.
    """
    return json.dumps(
        [
            PUBLICATION_CONTEXT_PURPOSE,
            PUBLICATION_CONTEXT_VERSION,
            team_id.hex(),
            berth_id.hex(),
            path,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def serialize_group_message(message: GroupMessage) -> bytes:
    return json.dumps(
        {
            "format": ENVELOPE_FORMAT,
            "sender_device_key_id": message.sender_device_key_id.hex(),
            "sender_chain_id": message.sender_chain_id.hex(),
            "iteration": message.iteration,
            "context": message.context.hex(),
            "iv": message.iv.hex(),
            "ciphertext": message.ciphertext.hex(),
            "signature": message.signature.hex(),
        },
        sort_keys=True,
    ).encode("utf-8")


def deserialize_group_message(payload: bytes) -> GroupMessage:
    """Parse an envelope, rejecting anything that is not this bound format.

    An unbound older envelope is refused rather than read on a weaker
    contract; there is no automatic fallback to reinstate the gap this format
    closes.
    """
    try:
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Envelope is not a JSON object")
        if data.get("format") != ENVELOPE_FORMAT:
            raise ValueError(f"Unsupported publication format: {data.get('format')!r}")
        return GroupMessage(
            sender_device_key_id=bytes.fromhex(data["sender_device_key_id"]),
            sender_chain_id=bytes.fromhex(data["sender_chain_id"]),
            iteration=int(data["iteration"]),
            context=bytes.fromhex(data["context"]),
            iv=bytes.fromhex(data["iv"]),
            ciphertext=bytes.fromhex(data["ciphertext"]),
            signature=bytes.fromhex(data["signature"]),
        )
    except Exception as exn:
        raise PublicationNotAuthenticExn(
            "unreadable_envelope", f"Not a readable publication envelope: {exn}"
        ) from exn


def _message_key_for(message: GroupMessage, sender_key) -> bytes:
    """Re-derive the key this message used, from the pre-read record.

    Pure in `sender_key`, so callers can run it only after authentication has
    succeeded and still retain a readable key for a later read.
    """
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


def prepare_encrypted_upload(
    ss_session, plaintext: bytes, context: bytes
) -> tuple[object, bytes]:
    user_db_path = device_local_db_path(
        ss_session.participant_path.parent.parent, ss_session.participant_id.hex()
    )
    sender_key = load_team_sender_key(user_db_path, ss_session.team_id)
    if sender_key is None:
        raise ValueError(f"No team sender key for {ss_session.team_name!r}")
    next_sender_key, message = group_encrypt(
        ss_session.team_id, sender_key, plaintext, context
    )
    replayable_keys = dict(next_sender_key.skipped_message_keys)
    replayable_keys[message.iteration] = _derive_message_key(sender_key.chain_key)
    next_sender_key = replace(next_sender_key, skipped_message_keys=replayable_keys)
    return next_sender_key, serialize_group_message(message)


def commit_encrypted_upload(ss_session, next_sender_key) -> None:
    user_db_path = device_local_db_path(
        ss_session.participant_path.parent.parent, ss_session.participant_id.hex()
    )
    save_team_sender_key(user_db_path, ss_session.team_id, next_sender_key)


def _require_expected_publisher(
    sender_device_key_id: bytes,
    expected_publisher: bytes,
    device_ownership: dict[bytes, set[bytes]] | None,
) -> None:
    """Check Core's accepted ownership of the signing device against the read.

    `device_ownership` maps a team device key id to the set of teammates Core
    associates with it; `None` means this session has no projection at all. The
    values are sets so an ambiguity cannot vanish while the mapping is built.
    """
    if device_ownership is None:
        raise OwnershipProjectionAbsentExn(
            "No accepted device-ownership projection for this session"
        )
    owners = device_ownership.get(sender_device_key_id) or set()
    if not owners:
        raise DeviceOwnershipUnavailableExn(
            f"No accepted owner for device key {sender_device_key_id.hex()}"
        )
    if len(owners) > 1:
        raise DeviceOwnershipAmbiguousExn(
            f"Device key {sender_device_key_id.hex()} is associated with "
            f"{len(owners)} teammates"
        )
    if owners != {expected_publisher}:
        raise UnexpectedPublisherExn(
            f"Device key {sender_device_key_id.hex()} belongs to another teammate, "
            f"not {expected_publisher.hex()}"
        )


def decrypt_group_payload(
    ss_session,
    payload: bytes,
    *,
    expected_context: bytes,
    expected_publisher: bytes,
    device_ownership: dict[bytes, set[bytes]] | None,
) -> bytes:
    """Accept one published object, or refuse it without side effects.

    Plaintext is returned and receiver state is committed only after the
    signature, the sender header, the object context, and Core's accepted
    ownership of the signing device all agree with what this read expects. A
    key that happens to decrypt does not choose the expected publisher.
    """
    user_db_path = device_local_db_path(
        ss_session.participant_path.parent.parent, ss_session.participant_id.hex()
    )
    message = deserialize_group_message(payload)
    sender_key = load_peer_sender_key(
        user_db_path, ss_session.team_id, message.sender_device_key_id
    )
    if sender_key is None:
        raise SenderKeyUnavailableExn(
            f"Missing sender key for device key {message.sender_device_key_id.hex()}"
        )
    try:
        next_sender_key, plaintext = group_decrypt(message, sender_key, expected_context)
    except InvalidIteration as exn:
        raise PublicationNotAuthenticExn("invalid_iteration", str(exn)) from exn
    except InvalidSignature as exn:
        raise PublicationNotAuthenticExn(
            "invalid_signature", "Publication signature does not verify"
        ) from exn
    except SenderHeaderMismatch as exn:
        raise PublicationNotAuthenticExn("header_mismatch", str(exn)) from exn
    except ContextMismatch as exn:
        raise PublicationNotAuthenticExn("context_mismatch", str(exn)) from exn
    except InvalidTag as exn:
        raise PublicationNotAuthenticExn(
            "ciphertext_invalid", "Publication ciphertext failed authenticated decryption"
        ) from exn
    # Ownership comes after authentication, so a pending ownership outcome only
    # ever describes bytes that are otherwise valid. group_decrypt has confirmed
    # the header names this record's device; nothing is committed yet.
    _require_expected_publisher(
        message.sender_device_key_id, expected_publisher, device_ownership
    )
    replay_message_key = _message_key_for(message, sender_key)
    replayable_keys = dict(next_sender_key.skipped_message_keys)
    replayable_keys[message.iteration] = replay_message_key
    next_sender_key = replace(next_sender_key, skipped_message_keys=replayable_keys)
    save_peer_sender_key(user_db_path, ss_session.team_id, next_sender_key)
    return plaintext
