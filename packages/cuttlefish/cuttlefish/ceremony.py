# Cuttlefish — Key signing ceremony helpers
#
# A "ceremony" is a short, physical-proximity signing event between two
# participants. The goal is that signing a teammate's key should be as simple
# as bumping phones or scanning a QR code.
#
# Design intent: the ceremony signs the GUARDED key (or a ceremony-specific
# delegation). The local CA hierarchy then propagates trust down to DAILY keys
# without any further teammate interaction.
#
# Open question: should the ceremony sign a single key, or a *binding* — a
# signed statement that a set of keys all belong to the same participant?
# Bindings are more powerful but have trickier revocation semantics.
#
# Related prior work to investigate:
#   - Signal Safety Number / fingerprint comparison
#   - CONIKS / Key Transparency
#   - Keybase social proof model (no central server analogue needed here)
#   - TOFU + key continuity (SSH model)

from __future__ import annotations

from dataclasses import dataclass

from .identity import KeyCertificate
from .keys import ParticipantKey


@dataclass
class CeremonyPayload:
    """The blob exchanged during a physical ceremony (QR code or bump).

    Contains enough information for the other party to issue a certificate
    without any further network round-trip.
    """

    participant_id: bytes
    target_key: ParticipantKey       # The key to be signed (typically GUARDED)
    hierarchy_certs: list[KeyCertificate]  # Proves target_key is in a valid hierarchy
    nonce: bytes                     # Prevents replay


def generate_ceremony_payload(
    participant_key: ParticipantKey,
    hierarchy_certs: list[KeyCertificate],
) -> CeremonyPayload:
    """Produce the payload this participant presents during a ceremony."""
    raise NotImplementedError


def encode_ceremony_payload_qr(payload: CeremonyPayload) -> bytes:
    """Encode a ceremony payload as a compact bytes blob suitable for a QR code."""
    raise NotImplementedError


def decode_ceremony_payload_qr(data: bytes) -> CeremonyPayload:
    raise NotImplementedError


def complete_ceremony(
    their_payload: CeremonyPayload,
    my_signing_key: ParticipantKey,
    my_signing_private_key: bytes,
) -> KeyCertificate:
    """Verify a received ceremony payload and issue a certificate for it.

    The returned certificate should be published to cloud storage.
    """
    raise NotImplementedError
