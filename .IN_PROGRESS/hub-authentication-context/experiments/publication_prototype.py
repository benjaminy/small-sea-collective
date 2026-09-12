"""Disposable verifier experiment, not a supported envelope or Hub API.

Keep the current Sender Keys KDF, but make context, expected owner, and
candidate state explicit. No storage, routing, Core policy, or wire parser.
The caller supplies an independently established sender-key record and accepted
ownership evidence. This module cannot establish their provenance itself.
"""

from dataclasses import dataclass, replace
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cuttlefish.group import GroupMessage, SenderKeyRecord, _advance_chain_key, _derive_message_key


class ContextMismatch(ValueError):
    pass


class WriterMismatch(ValueError):
    pass


class OwnershipUnavailable(ValueError):
    pass


class OwnershipAmbiguous(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def context(team, berth, path):
    return encode(["small-sea-publication-prototype", 1, team.hex(), berth.hex(), path])


@dataclass(frozen=True)
class Publication:
    context: bytes
    message: GroupMessage


def aad(context_bytes, message):
    return encode(["prototype-header", 1, context_bytes.hex(),
                   message.sender_device_key_id.hex(), message.sender_chain_id.hex(), message.iteration])


def transcript(publication):
    m = publication.message
    return encode(["prototype-signature", 1, aad(publication.context, m).hex(),
                   m.iv.hex(), m.ciphertext.hex()])


def candidate_key(record, iteration):
    """Retain readable keys exactly for this experiment; not a solution to #264."""
    retained = dict(record.skipped_message_keys)
    chain, position = record.chain_key, record.iteration
    if iteration < position:
        return replace(record, skipped_message_keys=retained), retained[iteration]
    while position <= iteration:
        retained[position] = _derive_message_key(chain)
        chain = _advance_chain_key(chain)
        position += 1
    return replace(record, chain_key=chain, iteration=position, skipped_message_keys=retained), retained[iteration]


def seal(record: SenderKeyRecord, context_bytes: bytes, plaintext: bytes):
    updated, key = candidate_key(record, record.iteration)
    message = GroupMessage(record.sender_device_key_id, record.chain_id, record.iteration,
                           os.urandom(12), b"", b"")
    message = replace(message, ciphertext=AESGCM(key).encrypt(message.iv, plaintext, aad(context_bytes, message)))
    publication = Publication(context_bytes, message)
    signature = Ed25519PrivateKey.from_private_bytes(record.signing_private_key).sign(transcript(publication))
    return updated, replace(publication, message=replace(message, signature=signature))


def open_publication(publication, record, *, expected_context, expected_publisher, ownership):
    """Return plaintext and candidate receiver state only after every check.

    ownership maps device IDs to sets of accepted teammate IDs, so ambiguity
    cannot disappear through dict construction. None means no projection.
    No state is persisted here; callers must separately arrange commit ordering.
    """
    m = publication.message
    if type(m.iteration) is not int or m.iteration < 0:
        raise ValueError("Invalid iteration")
    Ed25519PublicKey.from_public_bytes(record.signing_public_key).verify(m.signature, transcript(publication))
    if (m.sender_device_key_id, m.sender_chain_id) != (record.sender_device_key_id, record.chain_id):
        raise ValueError("Authenticated sender header disagrees with established key record")
    if publication.context != expected_context:
        raise ContextMismatch("Different authenticated object context")
    if ownership is None:
        raise OwnershipUnavailable("Projection absent")
    possible_owners = ownership.get(m.sender_device_key_id, set())
    if not possible_owners:
        raise OwnershipUnavailable("Device association absent")
    if len(possible_owners) != 1:
        raise OwnershipAmbiguous("Device has competing associations")
    if possible_owners != {expected_publisher}:
        raise WriterMismatch("Authenticated device belongs to another publisher")
    updated, key = candidate_key(record, m.iteration)
    plaintext = AESGCM(key).decrypt(m.iv, m.ciphertext, aad(expected_context, m))
    return updated, plaintext
