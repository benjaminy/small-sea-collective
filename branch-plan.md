# Branch Plan: Device Linking First Slice

**Branch:** `device-linking`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`,
`architecture.md`  
**Related archive plans:** `Archive/branch-plan-identity-model-rethink.md`,
`Archive/branch-plan-admin-control-clarification.md`,
`Archive/branch-plan-device-oriented-identity-first-steps.md`

## Context

The repo now has the first half of the device-only model in code:

- team creation emits a self-issued `membership` cert
- invitations admit a new member by issuing `membership`
- the old per-team identity-key layer is gone from the live path

What is still missing is the next trust edge:

- `device_link` exists in docs but is not yet a supported live cert type
- there is no code path for "same member, second team-device key"
- the team DB still treats a member as having one operational
  `device_public_key`

There is also an important runtime constraint:

- signed bundle verification is keyed by `member_id -> signature`
- the Hub/Cuttlefish sender-key tables are keyed by `sender_participant_id`,
  which today is the team member UUID
- `member.device_public_key` is the runtime lookup for link-signature
  verification

So there are really three layers here:

- trust/data model (`device_link`, device sets)
- Git link signing and verification
- sender-key / peer-routing / "fully live co-device" behavior

The first two can plausibly travel together in one branch. The third is the
part that grows scope sharply, because it spills into sender-key identity shape
and Hub peer/session assumptions.

## Proposed Goal

Land the first **honest, reviewable** `device_link` slice:

1. `device_link` becomes a supported live cert type with issue/verify helpers
2. the team DB can represent more than one trusted team-device key for one
   member
3. Manager provisioning can record and certify a new device key for an existing
   member
4. Git link signing and verification become device-aware, so more than one
   linked device key can validly sign for the same member
5. the branch explicitly does **not** claim full simultaneous co-device
   operation for sender keys, peer routing, or whole-install bootstrap yet

This is a stepping-stone branch: it gives us the trust/data-model backbone plus
device-aware Git signing, without pretending we have already solved every
operational consequence of multi-device.

## Why This Slice

This branch should separate three changes that are easy to conflate:

- **Trust/data model:** "one member can have multiple linked team-device keys"
- **Git/runtime signing:** "multiple linked devices can sign and verify pushed
  git links for the same member"
- **Full co-device runtime:** "multiple devices for one member can all push,
  send encrypted payloads, and act as peers independently right now"

The first two are ready to land together. The third still touches enough moving
parts that it should get its own branch once the device-link data model is
stable.

## Proposed Scope

### 1. Add live `device_link` cert support

Update `wrasse_trust.identity` so `device_link` is a first-class live cert:

- add `CertType.DEVICE_LINK` to `SUPPORTED_CERT_TYPES`
- add `issue_device_link_cert(...)`
- add `verify_device_link_cert(...)`
- the cert subject key is the newly linked device key
- the required claims are just:
  - `member_id`
- the authorizing device (D_old) is identified by the cert envelope's signer
  key; no separate claim field is needed for it. A verifier reads D_old off
  the envelope, not the claims.

Issuer rule for this branch:

- the signer must already be in the member's trusted device set for the
  team. Initially that set contains just the `membership` initial device;
  as `device_link` certs are admitted, subject keys are added to the set,
  so trust extends **transitively** — a later device authorized by a
  previously-linked device is valid.

Trust model for this branch:

- **Trust is monotonic and validate-on-insert.** When a `device_link` cert
  is admitted, its signer is checked against the member's current trusted
  device set; if the check passes, the new subject key is added to the
  set. After that, "is D trusted for M?" is a flat set-membership check.
  No recursive walk through cert history at verification time.
- This skips the "was D trusted *at the time* it signed?" question because
  there is no revocation in this branch. The revocation branch will need
  to revisit this and consult cert history (or a revocation-aware view of
  the device set). That re-examination is deferred, not avoided.

### 2. Acknowledge device multiplicity in the schema

The current data model encodes a lie: `member.device_public_key` (singular)
says each member has exactly one device key, and `team_device_key` is keyed
by `(team_id, device_id)` which forbids rotation history for the same
device. Both assumptions are wrong in the long run and this branch removes
them together.

**Team DB (`member` and `member_device`):**

- **drop** `member.device_public_key` entirely — the team DB should no
  longer carry a singular device key per member
- **add** a new `member_device` table that records the trusted device set
  for each member:

```sql
CREATE TABLE IF NOT EXISTS member_device (
    member_id         BLOB NOT NULL,
    device_public_key BLOB NOT NULL,
    added_at          TEXT NOT NULL,
    PRIMARY KEY (member_id, device_public_key),
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);
```

The table is append-only in this branch (no `revoked_at` column —
revocation semantics are deferred to the revocation branch, and adding a
column now risks encoding the wrong meaning). The founding `membership`
cert's initial device key is inserted on admission; each `device_link`
cert adds one more row.

`member_device` is a **materialized projection**, not independent
authority:

- `key_certificate` remains the canonical signed history
- `member_device` is the current accepted device-set index derived from
  admitted certs
- every `member_device` row must be justified by admitted cert history
- if `member_device` and `key_certificate` ever disagree, the cert history
  wins and `member_device` must be rebuildable from it

**NoteToSelf (`team_device_key`):**

- **loosen** the primary key from `(team_id, device_id)` to
  `(team_id, device_id, public_key)` so that rotation history for the
  same device on the same team is admissible without making timestamp
  metadata part of row identity
- keep the existing `created_at` and `revoked_at` columns as-is — in this
  branch `revoked_at` just means "no longer current," and the
  "current local device key" query already filters `revoked_at IS NULL`
  and picks the most recent `created_at`
- no rotation code is written in this branch; the looser PK is deliberate
  unused capacity so the schema stops lying about what's possible

**Source of truth and helpers:**

- canonical trust history (team-scoped, shared) lives in `key_certificate`
- trusted *public* device set per member (team-scoped, shared) is
  materialized into `member_device`
- local *private* device key per team (installation-scoped, local) lives
  in NoteToSelf's `team_device_key`
- `get_current_team_device_key(...)` already exists and answers "what is
  my current local signing key for team T" — no new helper needed for
  this branch
- add `get_trusted_device_keys_for_member(member_id)` over `member_device`
  for verification-time lookups
- add a rebuild helper/test path proving `member_device` can be derived
  from `key_certificate` without loss
- a historical-lookup helper (returning revoked/superseded rows) is **not**
  added in this branch; defer until the first caller actually needs it

### 3. Add a local Manager provisioning path for linking a new device key

The first implementation slice does **not** need to solve full new-machine
bootstrap.

Instead, add a local provisioning/helper path that proves the trust flow and DB
shape. For example:

- accept an externally supplied new device public key for an existing
  member
- issue a `device_link` cert signed by the linking installation's current
  active device key
- insert the new device public key into `member_device` on the team DB
  side (the validate-on-insert step for the trusted device set)

This could be exposed as a lower-level provisioning helper first, with Manager
UI/wrapper methods added only if they stay cheap.

Production semantics for this branch should remain:

- the target device generates its own keypair locally, ideally in a TEE or
  other device-local keystore
- only the public key leaves that device for admission

Micro tests may simulate both sides in one workspace, but the production
flow should still be phrased as "admit this externally generated public
key," not "mint a foreign device key on behalf of another installation."

The important thing is to land one end-to-end path that creates and stores a
real `device_link` cert in a real team DB.

### 4. Make Git link signatures device-aware

For this branch, we should go a bit further than the first draft:

- allow a pushed link to be signed by any currently linked device key for that
  member
- make verification succeed if the signature matches any trusted linked device
  key for that member (looked up via `get_trusted_device_keys_for_member`)
- stop reading `member.device_public_key` entirely — the column no longer
  exists

This means changing the CodSync signature payload from a bare
`member_id -> signature` map to a richer shape that identifies the signing
device by its raw public key alongside the member. No "key id" concept is
introduced in this branch — raw pubkeys are used directly. Verification
looks up the member's trusted device set and accepts the signature if the
named device is in it.

Rationale for raw pubkeys in this branch:

- the signing public keys are already public in the team trust model
- using the raw key keeps CodSync verification self-contained
- it avoids inventing an extra indirection layer in the same branch that is
  changing the signature shape already
- if CodSync is later generalized beyond Small Sea, this can be revisited

The narrow branch promise is:

- multiple linked devices may validly sign git links for the same member
- only one device still needs to be locally active at a time on any given
  installation
- sender-key and peer-routing behavior remain member-scoped for now

### 5. Do targeted doc cleanup

This branch should align docs around the staged approach:

- `packages/small-sea-manager/spec.md` currently implies a broader
  NoteToSelf-clone token flow for second-device setup
- the branch should clarify that the first implementation slice is per-team
  `device_link` trust/data-model work, not the full bootstrap story

If we introduce `member_device`, the relevant schema docs should mention it.

## Explicitly Out of Scope

- cloning `NoteToSelf/Sync` onto a new machine
- sharing cloud credentials via device-link token
- creating a new installation that is immediately live across all teams
- changing Cuttlefish sender-key identity from member-based to device-based
- simultaneous encrypted-send behavior from multiple linked devices for one
  member
- peer download/routing that distinguishes sibling devices of one member
- revocation traversal over `device_link`
- device removal/re-key UX
- cross-team batching of device enrollment

## Concrete Change Areas

### 1. `wrasse_trust.identity`

Expected work:

- add live `DEVICE_LINK` support
- add issue/verify helpers
- add micro tests covering happy-path verification and wrong-member/wrong-team
  rejection

### 2. Schema changes

Team DB:

- add `member_device`
- **drop** `member.device_public_key`
- every writer currently setting `member.device_public_key` (`create_team`
  and `complete_invitation_acceptance` in `provisioning.py`) is redirected
  to insert into `member_device` instead
- every reader currently querying `member.device_public_key` (tests and
  future CodSync verification) is redirected to `member_device`

NoteToSelf:

- loosen `team_device_key` PK to `(team_id, device_id, public_key)`
- no other changes; `get_current_team_device_key` keeps working unchanged

### 3. `small_sea_manager.provisioning`

Expected work:

- add a helper to generate an additional local team-device key for an existing
  member
- add a helper to issue/store a `device_link` cert
- add a helper to insert the linked device key into `member_device` on the
  team DB (the validate-on-insert trust step)
- make the public admission input explicit in the API shape: the helper
  should admit an externally generated device public key, not silently
  generate a foreign keypair on behalf of another device
- add `get_trusted_device_keys_for_member(member_id)` for verification
  call sites
- add a helper or test-only path to rebuild `member_device` from admitted
  `key_certificate` history

### 4. `cod_sync` signature shape and verification

Expected work:

- update the link supplement signature shape to identify the signing device
  alongside the member
- update verification helpers/tests to accept any trusted linked device for the
  member
- keep backward expectations inside this branch's test set coherent; no
  compatibility layer is required beyond what current tests need

### 5. Micro tests

Expected test updates/additions:

- `packages/wrasse-trust/tests/test_identity.py`
- a new or expanded manager micro test covering:
  - create team
  - link a second device key for the same member
  - verify the `device_link` cert
  - verify `member_device` contains both the founding and linked device
    keys
  - verify the transitive case: a third device linked via a cert signed by
    the *second* (non-founding) device is accepted
- update existing tests that read `member.device_public_key` to use
  `member_device` / `get_trusted_device_keys_for_member` instead
  (`test_signed_bundles.py`, `test_invitation.py`)
- update signed-bundle tests so one member can be verified through more
  than one linked device key

## Risks

### 1. Git signature shape churn can spill into verification paths

Changing link signatures from member-only to device-aware is still a cross-cut
through CodSync and signed-bundle tests. That is manageable, but it should stay
deliberately narrow.

### 2. It is easy to over-promise multi-device support

If the branch lands `device_link` certs plus device-aware Git signatures,
readers may infer that all linked devices are fully live. The docs and tests
should be explicit that this is not yet true for sender-key behavior or peer
routing.

### 3. The bootstrap story is still unresolved

The current spec imagines a device-link token that clones NoteToSelf and then
fans out across teams. This branch should not accidentally half-implement that
story without enough validation.

## Validation

This branch is successful if:

- the code can issue and verify a real `device_link` cert
- `member.device_public_key` no longer exists; the team DB represents a
  member's trusted device set through `member_device`
- `team_device_key` admits rotation history (loosened PK), even though no
  code in this branch exercises rotation
- the Manager provisioning layer can record a second linked device key for
  one member in at least one realistic local happy path
- a transitively-linked device (signed by a non-founding device) verifies
  correctly
- rebuilding `member_device` from admitted `key_certificate` history yields
  the same trusted device set
- that rebuilt/set-projected device set still matches after a normal repo
  sync/merge path
- signed-bundle verification succeeds for a member whose valid signer is
  any of several linked device keys
- the updated micro tests pass

Suggested validation command:

`uv run pytest packages/wrasse-trust/tests/test_identity.py packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_invitation.py packages/small-sea-manager/tests/test_signed_bundles.py`

## Locked Scoping Decision

This first `device_link` branch **should** include device-aware Git signing and
verification, not just cert/data-model work.

It still should **not** try to make multiple devices simultaneously live for
sender-key state, peer routing, or whole-install bootstrap. That keeps the
branch manageable while landing a meaningful multi-device capability.
