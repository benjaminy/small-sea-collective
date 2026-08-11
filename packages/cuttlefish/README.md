<img src="../../Documentation/Images/cuttlefish.png">

# Cuttlefish — Small Sea Session Crypto

Cuttlefish is the Small Sea package for session and transport cryptography.
It covers the Signal-inspired machinery used to bootstrap pairwise sessions
and encrypt team data, while the separate `wrasse-trust` package owns
identity, certificates, ceremonies, and trust-chain logic.

## Scope

Cuttlefish currently owns:

- X3DH-style prekey bundles and session bootstrap
- Double Ratchet for pairwise channels
- Sender Keys for team broadcast encryption

Cuttlefish does **not** own the BURIED/GUARDED/DAILY identity hierarchy or the
web-of-trust model. Those live in `wrasse-trust`.

## Module Map

- `cuttlefish.prekeys` — X3DH prekey bundles and bootstrap key material
- `cuttlefish.x3dh` — asynchronous pairwise key agreement
- `cuttlefish.ratchet` — Double Ratchet session state and message encryption
- `cuttlefish.group` — Sender Keys group encryption

## Design Notes

Small Sea follows Signal-style layering:

- prekey bundles make offline initiation possible
- X3DH establishes a shared secret
- Double Ratchet provides forward secrecy and post-compromise recovery
- Sender Keys make team broadcast efficient

**AEAD associated data must be constructible before decryption.**
Every associated-data input must come from protocol constants, receiver-held state, or cleartext envelope
fields that the authenticated construction binds.
An arbitrary value available only inside the ciphertext cannot be used to open that ciphertext.
Protocol versions may be associated data when the receiver independently knows the version it accepts;
single-version code should use one shared expected-version constant and reject other versions explicitly.
A cleartext version selector for simultaneous versions must itself be bound into the authenticated construction.

**Sender key state is a protocol-layer access convention, not a cryptographic
enforcement boundary.** Distributing a sender key to a party signals that they
are expected to be a legitimate reader; it does not prevent an admitted party
from relaying plaintext or receiver state to others. Read access is therefore
endpoint-trust-scoped.

**The admission and encryption extensions determine where a participant distributes sender key state.**
The current `team_id` is a technical replay and storage domain, not proof of one permanent social team identity.
If a durable team split produces multiple active continuations with incompatible recipient sets, those continuations need distinct group and sender-key namespaces even though they share constitutional ancestry.
Choosing those namespaces is operational containment, not a judgment about which continuation is the real team.

*Linked-device admission* is a unilateral identity-owner act: the existing
sibling runs a bootstrap flow, hands the new device its snapshot of peer sender
keys (giving join-time-forward access across all senders the sibling held), and
publishes a `device_link` cert. No per-sender redistribution ceremony is
required.

*Teammate admission* is an inviter-orchestrated, transcript-bound evidence flow.
Key facts for Cuttlefish consumers:

- The **inviter allocates the invitee's `teammate_id`** at proposal creation.
  The invitee binds to it in their signed acceptance blob but does not choose it.
- The **proposal shell is published before the invitee is contacted**.
- The **admission transcript** binds the invitee's concrete device keys and the pre-allocated `teammate_id`.
  Transport metadata is explicitly excluded; post-admission transport setup is a separate flow.
- The **inviter publishes the completed transcript and endorsement**.
- The current Manager admission extension decides which endorsements are sufficient before distributing future key material.

The future Constitution envelope can carry the admission extension's signed events without interpreting them as membership.
Cuttlefish supplies signatures and encrypted coordination; it does not define admission policy or infer domain facts from Git commit authorship.

**Rotation serves containment and hygiene only; it is never used to admit a new
party or erase earlier disclosure.** See `architecture.md` §1 for the full
model.

The Hub's crypto surface stays narrow:

- The **Hub** depends on `cuttlefish.group` for encrypted team sessions.
- The **Manager** depends on `cuttlefish.group` and `cuttlefish.ratchet` for
  key distribution and pairwise encrypted coordination.

## Relationship to Wrasse Trust

`cuttlefish.prekeys.IdentityKeyPair` is deliberately narrow: it is the X25519 +
Ed25519 bootstrap identity needed for session establishment. It is not the same
thing as the richer trust-side identity model in `wrasse-trust`, which handles
certification, revocation, ceremony exchange, and trust traversal.
