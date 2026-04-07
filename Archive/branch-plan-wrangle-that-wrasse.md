# Branch Plan: First Team-Device Trust Slice

**Branch:** `wrangle-that-wrasse`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/device_provisioning_todo.md`

## Context

The trust docs have converged enough to support a narrow first landing:

- a participant's identity is per-team (`Alice/Sharks`)
- a team is a **derived principal** represented by admin and membership history,
  not by a shared team private key
- each physical device should get its own per-team operational key
- the rare-use private key for `Alice/Sharks` should live in `NoteToSelf`,
  wrapped for authorized devices, not in the Sharks team repo
- public proofs that teammates rely on should live in the team repo

What is **not** ready is the full multi-device flow. The repo still lacks a
real device-linking implementation, recovery design, epoch enforcement, and the
rest of the broader Wrasse Trust model.

So this branch should land only the smallest end-to-end slice that proves the
shape is workable:

> When the current device creates or joins a team, generate a per-team identity
> and a per-team device key, certify the device key from the team identity, and
> persist enough public history in the team repo that teammates can verify that
> certification locally.

That is narrow enough to land, and broad enough to unblock future work on
actual device registration.

## Goal

After this branch lands, the current device can establish this trust shape for
one team:

1. `Alice/{Team}` team-membership identity key exists
2. its private key is stored in `NoteToSelf` as encrypted key material with one
   wrapper for the current device
3. `Alice/{Team}/{CurrentDevice}` operational device key exists
4. the team repo contains a public `device_binding` certificate from
   `Alice/{Team}` to `Alice/{Team}/{CurrentDevice}`
5. teammates can verify that the current operational key is backed by a valid
   team-local cert chain

Existing bundle-signature behavior should keep working. For now,
`member.public_key` remains the operational signing key used by the current
device. The new trust data sits alongside that path rather than replacing the
entire sync stack in one jump.

## Outcome

This branch landed the narrow first slice successfully.

Implemented:

- team-scoped typed certificates in `wrasse-trust`, including `device_binding`
- NoteToSelf storage for `team_identity`, `wrapped_team_identity_key`, and
  `team_device_key`
- local-only current-device private key storage via `FakeEnclave/`
- team DB member rows with both `identity_public_key` and `device_public_key`
- team DB `key_certificate` storage for public trust proofs
- `create_team(...)` flow that creates a team identity, a current device key,
  and the initial `device_binding` cert
- invitation acceptance flow that carries and verifies the same public proof
  shape for a newly joining member
- micro tests covering create-team, invitation flow, and signed bundle
  verification with the new current-device key model

Validation completed:

- `uv run pytest packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_invitation.py packages/small-sea-manager/tests/test_signed_bundles.py`
- Result: `6 passed`

Not finished on this branch:

- second-device registration flow
- revocation and rotation enforcement
- epoch enforcement
- cleanup of old `team_signing_key` schema/spec leftovers
- full spec/doc convergence across the repo

## Concrete Scope

### In scope

- typed, team-scoped certificate support sufficient for `device_binding`
- local/private storage for one wrapped `Alice/{Team}` key in NoteToSelf
- local-only storage for one current-device per-team operational key
- team DB public state for member identity public key and device-binding certs
- `create_team` flow updated to generate and publish the first cert chain
- invitation acceptance flow updated to generate and publish the same shape for
  the joining member
- verification helpers and micro tests for the new cert chain

### Out of scope

- actual "add second device" flow, QR exchange, or device-link tokens
- cross-team identity linking
- ambient proximity certs
- offline roots / recovery keys / paper keys
- contested team governance or quorum signing
- epoch enforcement on sync writes
- teammate removal and device removal rotation logic
- revocation certificate handling (no `revocation` cert issuance or
  enforcement in this branch — implied by the "no removal" exclusion above,
  but called out so reviewers don't have to infer it)
- replacing the entire existing Wrasse Trust model in one branch

This branch is a foundation for device registration, not device registration
itself.

## Changes

### 1. `wrasse-trust` — typed certs for the first real use case

The current cert model is still mostly generic and placeholder-oriented. This
branch should make it concrete enough for team-local device bindings.

**Update `wrasse_trust.identity`:**

- add an explicit `cert_type` field to `KeyCertificate`
- add explicit team scoping to certificates (`team_id` or equivalent)
- keep `claims` for extensibility, but do not use it as the primary type system
- add helper(s) for issuing and verifying `device_binding` certs
- switch `cert_id` from random bytes to a content-addressed hash of the
  canonical signed bytes. This makes certs immutable by construction, makes
  dedup automatic, and turns `cert_id` into a meaningful reference rather than
  an opaque tag. Cheap to do now, annoying to retrofit once certs are flowing
  through DBs.

**Constraint for this branch:**

- only the team-membership identity key may issue a `device_binding` cert

**Canonical signing scope (important):**

- the canonical signed bytes for a `device_binding` cert **must include
  `team_id`**, even though the team DB does not store `team_id` as a column on
  the cert table (team scope is implicit at the DB level). Without this, a cert
  signed for team Sharks could be lifted into team Jets' DB and would verify
  cleanly. The verification helper's "wrong team scope" test must exercise
  exactly this lift.

This is the first place where the cert model stops being an abstract graph and
starts reflecting a concrete protocol rule.

### 2. NoteToSelf schema — local/private trust state

The team-membership private key belongs in NoteToSelf, not in the team repo.

Add minimal synced/private metadata tables to
`small_sea_manager/sql/core_note_to_self_schema.sql` and its migrations:

- `team_identity`
  - `team_id`
  - `member_id` — the participant's **per-team** member ID for this team. This
    is not a NoteToSelf-global handle and must not be reused across teams; the
    whole per-team isolation story depends on this column never becoming a
    cross-team identifier.
  - `public_key`
  - `created_at`
- `wrapped_team_identity_key`
  - `team_id`
  - `device_id`
  - `wrapped_private_key`
  - `wrapper_version`
  - `created_at`
  - `revoked_at`

Add minimal local-only metadata for the current per-team device key:

- `team_device_key`
  - `team_id`
  - `device_id`
  - `public_key`
  - `private_key_ref`
  - `created_at`
  - `revoked_at`

**Storage rule:**

- `wrapped_team_identity_key` lives in the synced NoteToSelf DB
- the private key bytes for the current per-team device key do **not** live in
  the synced DB
- for this first slice, store current-device private key material via a local
  placeholder under `FakeEnclave/` and persist only a reference in the DB

Reusing `FakeEnclave/` as the local-only placeholder is preferable to teaching
the repo the wrong lesson by syncing per-device private keys through
`NoteToSelf/core.db`.

**Wrapping helper rule:**

All production and consumption of `wrapped_team_identity_key` rows must go
through exactly one `wrap_team_identity_key(...)` and one
`unwrap_team_identity_key(...)` helper. No inline wrapping logic in
`create_team`, `accept_invitation`, or anywhere else. The wrapper format is a
labeled placeholder for this branch, and funneling all use through a single
pair of helpers is what makes the eventual real-crypto swap a one-file edit
instead of a hunt.

**Migration files:**

Add **new** migration files for the schema changes rather than editing
existing ones, even though no production DBs exist. New files are the pattern
future contributors will copy, and they keep the migration history honest.

### 3. Team DB schema — public trust state teammates can inspect

The team repo needs enough public material to prove:

- who the member is in team terms
- what their current operational device key is
- that the current operational device key was certified by the member's
  team-membership identity

Update `small_sea_manager/sql/core_other_team.sql` and migrations:

- extend `member` with `identity_public_key`
- **rename** `member.public_key` to `member.device_public_key` to reflect its
  new meaning (the current device's per-team operational key, not a
  participant-global key). Keeping the old name would actively mislead future
  readers; this branch is the right moment to pay the rename churn.
- add `key_certificate` (or `trust_certificate`) table with typed cert fields

Suggested fields:

- `cert_id`
- `cert_type`
- `team_id` omitted here because team DB scope is implicit
- `subject_key_id`
- `subject_public_key`
- `issuer_key_id`
- `issuer_member_id`
- `issued_at`
- `claims`
- `signature`

This table is intentionally broader than just `device_binding`, but the branch
should only exercise `device_binding`.

### 4. `provisioning.py` — create/join the first cert chain

Update the current team creation and invitation flows so they emit the new
shape without requiring multi-device support.

**`create_team(...)`:**

- generate `Alice/{Team}` team-membership identity keypair
- wrap and store its private key in NoteToSelf for the current device
- generate `Alice/{Team}/{CurrentDevice}` operational keypair locally
- store only the public key + local key ref in NoteToSelf
- insert `member` row with:
  - `id = member_id`
  - `identity_public_key = Alice/{Team} public key`
  - `public_key = Alice/{Team}/{CurrentDevice} public key`
- issue `device_binding` cert and write it to the team DB

**`accept_invitation(...)`:**

- do the same local generation and storage for the acceptor
- include the following in the acceptance payload:
  - acceptor member ID
  - acceptor `Alice/{Team}` identity public key
  - acceptor current device public key
  - acceptor `device_binding` cert

**`complete_invitation_acceptance(...)`:**

- when inviter records the new member, persist:
  - acceptor member row with both public keys
  - acceptor `device_binding` cert in inviter's team DB

The goal is that both sides' copies of the team repo converge on the same
public proof structure.

### 5. Operational signing lookup — rename and narrow the meaning

Today `get_team_signing_key(...)` returns the per-team private/public key used
for bundle signing. Its meaning is changing: it now returns the **current
device's** per-team operational key, not a team-wide signing key. The old name
would lie about that.

For this branch:

- **rename** `get_team_signing_key(...)` to `get_current_device_team_key(...)`
  (or similarly explicit). Update all call sites.
- change its backing storage so it returns the current device's per-team
  operational key, not the team-membership identity key
- align `member.device_public_key` with this current device key so existing
  signed-bundle tests and Cod Sync integration still make sense

The implementation stays conservative — Cod Sync still uses one per-device
key for bundle signing — but the names now match the new trust model. This
branch is the moment to pay the rename churn; doing it later means doing it
on top of more call sites.

### 6. Verification helpers — prove the public history is meaningful

Add a small verification helper on the Manager or Wrasse Trust side that can
answer:

> Is the current `member.public_key` in this team DB backed by a valid
> `device_binding` certificate from `member.identity_public_key` for this team?

The helper should:

- load the member row
- find the matching `device_binding` cert
- verify issuer/subject/team/member constraints
- verify the cert signature

This gives the branch a crisp validation target without requiring the whole Hub
or Cod Sync stack to enforce the new trust logic immediately.

## Validation and Micro Tests

This branch should be judged by focused micro tests first, then by broader
integration coverage.

### New micro tests

- `create_team` creates:
  - one `team_identity` row in NoteToSelf
  - one `wrapped_team_identity_key` row for the current device
  - one local `team_device_key` record
  - one `member` row with both `identity_public_key` and `device_public_key`
  - one valid `device_binding` cert in the team DB
- `accept_invitation` + `complete_invitation_acceptance` persist the same
  public shape for the joining member on both sides
- verification helper accepts a valid cert chain
- verification helper rejects:
  - wrong issuer
  - wrong subject key
  - wrong member binding
  - wrong team scope — exercised by signing a `device_binding` cert with
    `team_id = A` and attempting to verify it as a cert for `team_id = B`.
    This must fail because `team_id` is part of the canonical signed bytes.

### Regression tests to keep passing

- `packages/small-sea-manager/tests/test_create_team.py`
- `packages/small-sea-manager/tests/test_invitation.py`
- `packages/small-sea-manager/tests/test_hub_invitation_flow.py`
- `packages/small-sea-manager/tests/test_signed_bundles.py`

### Success criteria

A bright critic should be convinced that this branch:

1. makes future device registration easier rather than harder
2. preserves existing current-device operational signing behavior
3. moves private team identity material out of the team repo
4. makes public team-local trust state inspectable and verifiable
5. does not smuggle in cross-team leakage through NoteToSelf-visible proofs

## Risks To Watch

- accidentally storing current-device private keys in synced NoteToSelf state
- confusing the team-membership identity key with the operational device key
- over-generalizing the cert format before the first concrete use case lands
- bloating invitation acceptance payloads with private or cross-team material
- breaking existing signed-bundle tests while changing `member.public_key`
- designing a wrapper format that looks "final" when it is really a placeholder

## Migration / Compatibility

- There are no production databases to migrate
- Existing dev/test DBs should be recreated
- Backward compatibility with the current placeholder trust model is **not** a
  goal
- The wrapped-key envelope and `FakeEnclave` integration should be explicitly
  labeled provisional in code comments and docs

## Order of Operations

1. Extend `wrasse_trust.identity` for typed, team-scoped certs (including
   `team_id` in canonical signed bytes and content-addressed `cert_id`)
2. Add NoteToSelf schema for `team_identity`, `wrapped_team_identity_key`, and
   `team_device_key` (as new migration files)
3. Add the single `wrap_team_identity_key` / `unwrap_team_identity_key` helper
   pair and the local-only placeholder storage for current per-team device
   private keys
4. Add team DB public fields: rename `member.public_key` to
   `member.device_public_key`, add `member.identity_public_key`, and add the
   typed cert table
5. Update `create_team(...)`
6. Update `accept_invitation(...)` and `complete_invitation_acceptance(...)`
7. Rename `get_team_signing_key(...)` to `get_current_device_team_key(...)`
   and point it at the current device key; update all call sites
8. Add verification helpers and focused micro tests (including the
   cross-team-lift rejection test)
9. Run the regression suite and fix any fallout
