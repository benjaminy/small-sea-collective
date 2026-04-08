# Branch Plan: Device-Oriented Identity First Steps

**Branch:** `device-oriented-identity-first-steps`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`,
`architecture.md`  
**Related archive plans:** `Archive/branch-plan-wrangle-that-wrasse.md`,
`Archive/branch-plan-typed-cert-format.md`,
`Archive/branch-plan-identity-model-rethink.md`,
`Archive/branch-plan-admin-control-clarification.md`

## Context

The docs now describe a **device-only, per-team** identity model:

- no per-team private identity key above device keys
- no synced wrapped private key material in `NoteToSelf`
- each team membership is identified by a fresh per-team participant UUID
- each device enrolled in that team has its own team-device key
- `membership` admits a per-team participant UUID and names its founding
  device key
- `device_link` later expands the device set for an existing UUID

The code still implements the older **layered** model:

- `team_identity` and `wrapped_team_identity_key` tables exist in NoteToSelf
- `member.identity_public_key` exists in the team DB
- `device_binding` is the live cert type for proving a current device key
- invitation acceptance carries `acceptor_identity_public_key` and
  `acceptor_device_binding_cert`

Trying to convert all of that at once would be a large risky branch.

The smallest meaningful implementation step is to convert the **founding-device
flow** first:

- team creation
- invitation acceptance
- signed bundle verification

That gets the current system onto the new trust root shape without yet solving
second-device enrollment, revocation graph traversal, epoch enforcement, or
cross-team identity linking.

## Proposed Goal

After this branch lands:

1. the founding trust proof for a member is a `membership` cert, not a
   `device_binding` cert
2. the current device remains the signing key used for bundle signatures
3. team creation and invitation acceptance generate only a per-team
   participant UUID plus a founding team-device key
4. the layered per-team identity-key storage (`team_identity`,
   `wrapped_team_identity_key`, and `member.identity_public_key`) is removed
   from the live path
5. the legacy `team_signing_key` table is removed from NoteToSelf
6. the codebase is positioned for a later branch to add `device_link` for
   second-device enrollment

## Scope Decisions Already Made

- **Delete the old live path during the refactor.** The branch should remove
  the layered artifacts from live code and schema rather than keeping them in
  coexistence mode. Docs may continue mentioning the deprecated model where
  useful for orientation, but the implementation should pick one model.
- **No data migration required.** Since the system is pre-alpha and the identity
  model change is structural, we will perform a "destructive" update. Existing
  test sandboxes should be reset (`rm -rf`) rather than migrated.
- **Prefer better structure over preserving old invitation choreography.** If a
  cleaner inviter/acceptor split falls naturally out of the `membership` model,
  take it. Pre-1.0 is the right time to do that.
- **Allow opportunistic `device_link` scaffolding, but avoid scope creep.** If
  a small amount of scaffolding naturally fits while doing the founding-device
  refactor, that's welcome. The branch should still be judged on the
  founding-device flow, not on second-device support.

## Why This Slice

This slice is big enough to matter and small enough to review.

It updates the system's trust root from:

`member identity key -> device_binding -> current device key`

to:

`membership(founding_device=K0) -> current device key`

while preserving the rest of the current operational flow:

- one current team-device key per member in the happy path
- invitation workflow still exists
- bundle-signature verification still uses the member's current device key
- no attempt to implement second-device enrollment yet

## In Scope

### 1. Replace founding `device_binding` with `membership`

Update `wrasse_trust.identity` so the first actually-supported team-admission
cert is `MEMBERSHIP`, not `DEVICE_BINDING`.

Concrete direction:

- add `CertType.MEMBERSHIP` to `SUPPORTED_CERT_TYPES`
- add `CertType.DEVICE_LINK` as scaffolding (unissued in this branch)
- add helper(s) to issue and verify a founding-device `membership` cert
- the minimum useful claim shape should include:
  - `member_id`
  - `founding_device_key`
  - possibly nothing else, since `team_id` and `subject_public_key` already
    live in the cert envelope
- keep `DEVICE_BINDING` parseable for transitional deserialization only if the
  branch needs it for test fixtures or upgrade paths, but stop emitting it in
  all live flows

### 2. Remove synced private team-identity storage from NoteToSelf

Delete the live use of:

- `team_identity`
- `wrapped_team_identity_key`
- `team_signing_key` (legacy migration 47 artifact)

Keep:

- `team` pointer table
- `team_device_key`
- sender-key state and other Cuttlefish-adjacent local state

The current per-team device private key should continue to live locally via
`FakeEnclave/` or the device keystore path already used by `team_device_key`.

### 3. Simplify the team DB member model

Update the team DB so `member` records no longer carry both:

- `identity_public_key`
- `device_public_key`

Instead, the member row should carry only the current founding/current device
public key needed for current operations, plus the cert history in
`key_certificate`.

Likely shape for this branch:

- remove `identity_public_key`
- keep `device_public_key` as the member's current bundle-signing key

This is still not the final multi-device data model, but it avoids keeping a
fake higher-level identity key around after the docs have killed it.

### 4. Convert team creation to the device-only model

`create_team(...)` should:

- mint a fresh per-team member UUID
- generate one team-device key for the current device
- store that key in `team_device_key`
- emit a self-issued `membership` cert whose subject is the founding
  device key and whose claims bind the new member UUID into the team
- store the member row with that device key as its current public key
- store the `membership` cert in `key_certificate`

No team identity key should be created anywhere in this flow.

### 5. Convert invitation acceptance to the device-only model

The invitation flow may be reshaped if the new model wants a cleaner split of
responsibility, but the trust payload definitely changes:

- the acceptor generates a per-team member UUID and a founding team-device key
- the acceptance side should send only the public material actually needed by
  the inviter to admit that member:
  - proposed per-team member UUID
  - founding device public key
  - cloud/peer/bootstrap details already needed by the invitation flow
- inviter-side completion issues the `membership` cert, stores it in the team
  history, and stores the acceptor's current device public key, not an identity
  key plus a `device_binding` cert

The branch should keep Hub/CodSync responsibilities stable unless the cleaner
model clearly wants a different split.

This implies an honest provisional state on the invitee side:

- the invitee may have cloned the team and prepared their local device key
- but they are not fully admitted until the inviter's `membership` cert comes
  back through sync
- the invitee's local `member` row for themselves will exist, but the
  `key_certificate` table will be empty until the sync completes.

### 6. Keep signed bundle verification working

The current path in `test_signed_bundles.py` assumes:

- `get_team_signing_key(...)` returns the current team-device signing key
- the team DB `member` row exposes the public key Bob uses to verify Alice's
  pushed link

That should remain true after the refactor, even if helper and column names are
cleaned up.

## Out of Scope

- second-device enrollment UI or protocol
- `device_link` issuance in live flows
- cross-team `identity_link`
- revocation and transitive revocation logic
- epoch enforcement
- trust graph traversal beyond the founding-device happy path
- invitation/governance redesign beyond what is needed to swap in
  `membership` certs
- broad cleanup of every doc mentioning old schema names

## Concrete Change Areas

### 1. `wrasse_trust.identity`

Expected work:

- add `MEMBERSHIP` and `DEVICE_LINK` to supported live cert types
- add `issue_membership_cert(...)`
- add `verify_membership_cert(...)`
- decide what to do with `DEVICE_BINDING`:
  - ideally stop issuing it entirely
  - possibly keep deserializing/verifying it for transitional tolerance

### 2. `small_sea_manager.provisioning`

Expected work:

- replace `_generate_team_identity_and_device_key(...)` with a device-only
  helper that creates only the current team-device key and founding
  `membership` cert
- update `_store_team_certificate(...)` call sites
- update `create_team(...)`
- update `accept_invitation(...)`
- update `complete_invitation_acceptance(...)`
- update acceptance-token fields and verification logic
- decide whether `get_team_signing_key(...)` keeps its current name for one
  branch or gets renamed now

### 3. NoteToSelf schema and migration logic

Expected work:

- remove `team_identity`, `wrapped_team_identity_key`, and `team_signing_key`
  from the schema and migration path
- keep `team_device_key`
- update `USER_SCHEMA_VERSION` and reset sandboxes

### 4. Team schema and migration logic

Expected work:

- remove `identity_public_key` from `member`
- keep `device_public_key`
- leave `key_certificate` in place, but change its live contents from
  founding `device_binding` certs to founding `membership` certs

### 5. Micro tests

Expected test updates:

- `packages/small-sea-manager/tests/test_create_team.py`
- `packages/small-sea-manager/tests/test_invitation.py`
- `packages/small-sea-manager/tests/test_signed_bundles.py`
- `packages/wrasse-trust/tests/test_identity.py`

Add or update micro tests so they prove:

- no wrapped team-identity key is created
- no `identity_public_key` is required in the team DB happy path
- founding membership proofs verify correctly
- bundle-signature verification still uses the current device public key

## Risks

### 1. Invitation flow shape may need more than a cert swap

The current inviter/acceptor handshake is built around the old layered proof
shape. It may resist a simple field swap more than expected.

### 2. Schema churn can spill into unrelated manager logic

`identity_public_key`, `team_identity`, and `wrapped_team_identity_key` appear
in tests, provisioning helpers, and specs. This branch should keep the live
code slice small even if some doc cleanup is needed.

### 3. `membership` semantics are broader than this first slice

Long-term, `membership` interacts with governance, forked local views, and
epoch transitions. This branch should not pretend to solve all of that. It only
needs a correct founding-device happy path.

## Validation

This branch is successful if:

- the code no longer creates or relies on per-team private identity keys in the
  founding-device flow
- team creation and invitation acceptance emit `membership` certs instead of
  `device_binding`
- the current device key remains sufficient for signed-bundle verification
- the updated micro tests pass

**IMPORTANT:** Before running tests, reset any existing sandbox data:
`rm -rf /Users/ben8/.gemini/tmp/small-sea-collective/Scratch/Sandbox` (or equivalent)

Suggested validation command:

`uv run pytest packages/wrasse-trust/tests/test_identity.py packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_invitation.py packages/small-sea-manager/tests/test_signed_bundles.py`


## Questions To Resolve Before Locking Scope

### 1. Who signs the `membership` cert in the invitation flow?

The current docs imply:

- team creation: self-issued genesis `membership`
- joining an existing team: an **existing member device** issues the new
  member's `membership` cert

If we follow that literally, then the acceptor should not return a finished
membership cert in the acceptance token. Instead, the acceptor should return the
public ingredients needed for admission:

- proposed per-team member UUID
- founding device public key
- cloud endpoint info and any other invitation-response data

and the inviter should issue the `membership` cert during
`complete_invitation_acceptance(...)`.

That seems cleaner to me and more faithful to the current docs, but it does
mean the acceptor's local clone temporarily knows "I intend to join" before it
has received the inviter-issued `membership` cert back through sync.

Decision: the inviter issues the `membership` cert.

Rationale:

- it matches the current docs for "joining an existing team"
- it keeps `membership` semantics crisp
- it avoids smuggling a second meaning into invitee-generated certs
- it makes the provisional invitee state explicit instead of hidden
