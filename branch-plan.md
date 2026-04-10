# Strengthen Identity-Join Verification

Branch plan for `strengthen-identity-join-verification`.
Primary tracker: #63.

## Context

The identity-bootstrap branch (#58, PR #65) landed a working join flow:

1. joining device generates keypair + UUID, exports a join request artifact
2. authorizing device receives it, both devices display a short authentication string (truncated SHA-256 of the artifact, formatted as `XXXX-XXXX-XXXX-XXXX`)
3. human confirms the strings match
4. authorizing device admits the new device, produces an encrypted welcome bundle
5. joining device bootstraps from the bundle

The current auth string is a reasonable v1 guard against OOB mixups — it confirms both devices are looking at the same join request.
But it has limitations:

- it only proves artifact integrity, not that the authorizing device is who it claims to be
- there is no mutual authentication — the joining device trusts whatever welcome bundle arrives
- there is no post-bootstrap verification that the pulled NoteToSelf actually came from the expected identity

This branch should add a practical verification improvement without redesigning the entire trust model.

## Branch Goal

Strengthen the identity-join handshake so that:

- the joining device has a way to verify the welcome bundle came from an authorized device in the expected identity
- the improvement is compatible with the existing join-request / welcome-bundle flow
- the UX remains practical for device-to-device confirmation

## What This Branch Is Not

- a full signed admission cert for NoteToSelf (that may come later, but is a bigger trust-model change)
- a redesign of the join flow steps
- peer-to-peer key exchange or a new transport channel
- revocation or device-removal

## Proposed Approach: Authorizing-Device Signature in the Welcome Bundle

The simplest strengthening that fits the current flow:

The authorizing device **signs the welcome bundle plaintext** with its own NoteToSelf device key before encrypting to the joining device's key.
The joining device, after pulling NoteToSelf, can verify that signature against the `user_device` table — confirming the bundle was produced by a device that is (or was) part of the identity.

Why this works:

- the authorizing device already has a NoteToSelf device key (stored in the enclave / FakeEnclave)
- the joining device already pulls NoteToSelf after bootstrap, which contains the `user_device` rows with public keys
- verification is a post-pull cross-check, not a gate before the pull — the joining device needs the NoteToSelf data to verify, so the check happens after bootstrap completes
- if verification fails, the joining device can warn or refuse to proceed further (e.g. refuse team-join requests)

What it adds over v1:

- the welcome bundle now carries proof that it was produced by a specific device in the identity, not just any party who knew the joining device's public key
- a MITM who intercepts the join request and produces a fake welcome bundle would need to control a device key already in the identity's `user_device` table

Limitations (acceptable for this branch):

- the check is post-bootstrap, not pre-bootstrap — the joining device has already pulled NoteToSelf before it can verify.
   This is inherent: you need the identity's device list to verify against.
- if the authorizing device is compromised, this doesn't help.
   But that's true of any scheme where the authorizing device holds the keys.

## In Scope

- add a signing step to welcome bundle creation (authorizing device signs with its NoteToSelf device key)
- add the authorizing device's public key or device ID to the welcome bundle metadata so the joining device knows which key to verify against
- add a post-bootstrap verification step that checks the signature against the pulled `user_device` table
- add the signing/verification primitives to Cuttlefish if not already present
- update tests to cover: valid signature passes, tampered bundle fails, signature by unknown device fails
- update docs

## Out Of Scope

- signed admission certs (audit-trail-level trust)
- challenge-response protocols
- periodic cross-device sanity checks
- changes to the OOB channel or transport
- revocation

## Concrete Change Areas

### `cuttlefish`

- if X25519 keys can't sign (they can't — X25519 is DH-only), the branch needs to decide: add an Ed25519 signing keypair alongside the X25519 encryption key, or switch the NoteToSelf device key to Ed25519 (which can derive X25519 for encryption).
   This is the main design decision.

### `small-sea-note-to-self`

- add `authorizing_device_id_hex` and `authorizing_device_signature` fields to the welcome bundle
- add a verification helper

### `small-sea-manager`

- authorizing-side: sign the bundle plaintext before encryption
- joining-side: after NoteToSelf pull, verify the signature

### Tests

- valid signature from known device passes
- tampered plaintext with valid signature fails
- signature from a device not in `user_device` fails
- the existing expiry and round-trip tests still pass

## Key Decision: Key Type

X25519 is Diffie-Hellman only — it cannot sign.
The current `generate_bootstrap_keypair()` in Cuttlefish produces X25519 keys.

Options:

- **(a) Switch to Ed25519 for NoteToSelf device keys.**
   Ed25519 can sign natively and can derive X25519 for the welcome bundle encryption via standard conversion.
   One keypair serves both purposes.
- **(b) Keep X25519 for encryption, add a separate Ed25519 signing key.**
   Two keypairs per device.
   More explicit separation but more key material to manage.

Default: **(a)** — Ed25519 as the primary NoteToSelf device key, with X25519 derived for encryption.
This is the standard pattern (used by Signal, age, etc.) and avoids doubling the key management surface.

This means `generate_bootstrap_keypair()` changes to produce Ed25519 keys, and `seal_welcome_bundle` derives X25519 internally for the DH step.

## Implementation Order

### Phase 0: Lock the key-type decision

Confirm Ed25519-primary or dual-key.
Check that the existing bootstrap test and `user_device` schema can absorb the key type change cleanly (fresh-schema rules still apply).

### Phase 1: Cuttlefish key + signing primitives

- switch `generate_bootstrap_keypair()` to Ed25519
- add `sign_welcome_bundle(signing_private_key, plaintext)` → signature bytes
- add `verify_welcome_bundle_signature(signing_public_key, plaintext, signature)` → bool
- update `seal_welcome_bundle` to derive X25519 from Ed25519 for the DH step

### Phase 2: Welcome bundle signature

- add `authorizing_device_id_hex` and `signature` to the welcome bundle
- authorizing side: sign plaintext, include signature in sealed bundle
- the signature covers the canonical JSON plaintext (same bytes that get encrypted)

### Phase 3: Post-bootstrap verification

- after NoteToSelf pull, the joining device:
  - looks up `authorizing_device_id_hex` in the pulled `user_device` table
  - verifies the signature against that device's public key
  - warns or refuses if verification fails

### Phase 4: Tests + docs

- update existing bootstrap tests for the new key type
- add signature verification tests (valid, tampered, unknown device)
- update spec.md

## Validation

- existing bootstrap round-trip still works with Ed25519 keys
- welcome bundle carries a valid signature from the authorizing device
- verification passes after a successful bootstrap
- tampered bundle plaintext fails verification
- signature from an unknown device (not in `user_device`) fails verification
- the short auth string still works as before (it's orthogonal)

## Risks

- **Ed25519 → X25519 derivation is a well-known pattern but adds complexity to Cuttlefish.**
   Mitigation: use `cryptography` library's standard conversion; do not implement from scratch.
- **Changing key type could break existing test fixtures or stored keys.**
  Mitigation: fresh-schema rules apply; no migration needed for pre-alpha.
- **Post-bootstrap verification is inherently after-the-fact.**
   Mitigation: this is acceptable — document it clearly.
   Pre-bootstrap verification would require a different architecture.
