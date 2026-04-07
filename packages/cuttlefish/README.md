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

The Hub's crypto surface stays narrow:

- The **Hub** depends on `cuttlefish.group` for encrypted team sessions.
- The **Manager** depends on `cuttlefish.group` and `cuttlefish.ratchet` for
  key distribution and pairwise encrypted coordination.

## Relationship to Wrasse Trust

`cuttlefish.prekeys.IdentityKeyPair` is deliberately narrow: it is the X25519 +
Ed25519 bootstrap identity needed for session establishment. It is not the same
thing as the richer trust-side identity model in `wrasse-trust`, which handles
certification, revocation, ceremony exchange, and trust traversal.
