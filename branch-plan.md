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

## Proposed Approach: Signed Welcome Bundle with Dual Device Keys

The simplest strengthening that fits the current flow:

Each NoteToSelf device gets **two** bootstrap-relevant keys:

- an **X25519 bootstrap-encryption key** for sealing / opening the welcome bundle
- an **Ed25519 signing key** for signing the welcome bundle plaintext

The authorizing device **signs the canonical welcome bundle plaintext** with its
Ed25519 signing key, then encrypts the signed bundle to the joining device's
X25519 bootstrap-encryption key.

After pulling NoteToSelf, the joining device verifies that signature against the
authorizing device's signing public key in `user_device`.

Why this works:

- signing and encryption stay cleanly separated
- the joining device already pulls NoteToSelf after bootstrap, which contains
  the `user_device` rows with public signing keys
- verification is a post-pull cross-check, not a gate before the pull — the
  joining device needs the NoteToSelf data to verify, so the check happens
  after bootstrap completes
- if verification fails, the joining device can warn or refuse to proceed
  further (for example, refuse later team-join actions)

What it adds over v1:

- the welcome bundle now carries proof that it was produced by a specific
  device in the **pulled** identity, not just any party who knew the joining
  device's encryption public key
- a MITM who intercepts the join request and produces a fake welcome bundle
  would need to control a signing key already present in the pulled identity's
  `user_device` table

Limitations (acceptable for this branch):

- the check is post-bootstrap, not pre-bootstrap — the joining device has
  already pulled NoteToSelf before it can verify. This is inherent: you need
  the identity's device list to verify against.
- by itself, this still proves "bundle signed by a device in the pulled
  identity", not "this is definitely the identity the human intended"
- if the authorizing device is compromised, this doesn't help. But that's true
  of any scheme where the authorizing device holds the keys.

### Practical extra check when both devices are present

Because the common case is having both devices side by side, this branch should
also add a second short confirmation string tied to the signed bundle:

- the authorizing device computes it from the join session values
- the joining device computes it after decrypt + pull + signature verification
- the human compares the two devices again

This does not create a central authority or redesign the flow, but it gives a
much stronger practical check that both devices are talking about the same
signed bootstrap event and the same pulled identity.

## In Scope

- add a signing step to welcome bundle creation (authorizing device signs with
  its Ed25519 NoteToSelf signing key)
- add the authorizing device's device ID to the welcome bundle metadata so the
  joining device knows which key to verify against
- add a post-bootstrap verification step that checks the signature against the pulled `user_device` table
- add the signing/verification primitives to Cuttlefish if not already present
- add a second short confirmation string derived from the signed bundle / join
  session values
- update tests to cover: valid signature passes, AEAD tampering still fails,
  signature by unknown device fails, wrong signing key fails
- update docs

## Out Of Scope

- signed admission certs (audit-trail-level trust)
- challenge-response protocols
- periodic cross-device sanity checks
- changes to the OOB channel or transport
- revocation

## Concrete Change Areas

### `cuttlefish`

- keep X25519 welcome-bundle encryption as-is
- add Ed25519 sign / verify helpers for welcome bundle plaintext
- no Ed25519→X25519 conversion in this branch

### `small-sea-note-to-self`

- extend `user_device` to carry both public keys explicitly
  - `bootstrap_encryption_key`
  - `signing_key`
- extend the local NoteToSelf device-key-secret table to carry both private-key
  refs explicitly
- add `authorizing_device_id_hex` and `authorizing_device_signature` fields to the welcome bundle
- add a verification helper
- add a helper for the second short confirmation string

### `small-sea-manager`

- participant creation: generate/store both device keypairs
- join-request creation: emit both public keys
- authorizing-side: sign the bundle plaintext before encryption
- joining-side: after NoteToSelf pull, verify the signature
- joining-side: if verification passes, compute/display the second short
  confirmation string

### Tests

- valid signature from known device passes
- AEAD tampering still fails before plaintext verification
- signature from a device not in `user_device` fails
- signature checked with the wrong known signing key fails
- second short confirmation string matches on both devices for the same join
  session
- the existing expiry and round-trip tests still pass

## Key Decision: Key Type

X25519 is Diffie-Hellman only — it cannot sign.
The current `generate_bootstrap_keypair()` in Cuttlefish produces X25519 keys.

Default for this branch: **dual-key**

- keep X25519 for welcome-bundle encryption
- add a separate Ed25519 signing keypair per NoteToSelf device

Why this is the default:

- it avoids pulling Ed25519→X25519 conversion logic into this branch
- it keeps the crypto roles explicit
- it works cleanly with the repo's current `cryptography`-based stack

Possible later simplification, but **not** the default for this branch:

- Ed25519-primary with derived X25519, likely via a library such as PyNaCl

## Implementation Order

### Phase 0: Lock the key-type decision

Confirm the dual-key schema shape explicitly.
Check that the existing bootstrap tests and `user_device` / local-secret schema
can absorb the additional key columns cleanly (fresh-schema rules still apply).

### Phase 1: Cuttlefish key + signing primitives

- keep `generate_bootstrap_keypair()` for X25519 bundle encryption
- add `generate_bootstrap_signing_keypair()` for Ed25519 signing
- add `sign_welcome_bundle(signing_private_key, plaintext)` → signature bytes
- add `verify_welcome_bundle_signature(signing_public_key, plaintext, signature)` → bool

### Phase 2: Schema + key storage

- extend shared `user_device`
  - keep/add explicit X25519 bootstrap-encryption public key
  - add Ed25519 signing public key
- extend local NoteToSelf device-key-secret storage
  - bootstrap-encryption private-key ref
  - signing private-key ref
- update participant creation and join-request creation to generate/store both
  keypairs

### Phase 3: Welcome bundle signature

- add `authorizing_device_id_hex` and `signature` to the welcome bundle
- authorizing side: sign plaintext, include signature in sealed bundle
- the signature covers the canonical JSON plaintext (same bytes that get encrypted)

### Phase 4: Post-bootstrap verification

- after NoteToSelf pull, the joining device:
  - looks up `authorizing_device_id_hex` in the pulled `user_device` table
  - verifies the signature against that device's Ed25519 signing public key
  - warns or refuses if verification fails
  - if verification passes, computes/displays the second short confirmation
    string for side-by-side human confirmation

### Phase 5: Tests + docs

- update existing bootstrap tests for the dual-key device shape
- add signature verification tests (valid, unknown device, wrong key)
- add second-short-code tests
- update spec.md

## Validation

- existing bootstrap round-trip still works with dual device keys
- welcome bundle carries a valid signature from the authorizing device
- verification passes after a successful bootstrap
- AEAD tampering still fails before plaintext verification
- signature from an unknown device (not in `user_device`) fails verification
- signature checked against the wrong known signing key fails verification
- second short confirmation string matches on both devices for the same join
  session
- the short auth string still works as before (it's orthogonal)

## Risks

- **Dual-key NoteToSelf devices increase schema and key-management surface.**
  Mitigation: keep the roles explicit (`bootstrap_encryption_key` vs
  `signing_key`) and keep the branch tightly scoped to identity bootstrap.
- **The strengthened check still only proves "signed by a device in the pulled
  identity".**
  Mitigation: do not over-claim in docs, and require the second side-by-side
  confirmation string for the common both-devices-present case.
- **Post-bootstrap verification is inherently after-the-fact.**
  Mitigation: this is acceptable — document it clearly. Pre-bootstrap
  verification would require a different architecture.
