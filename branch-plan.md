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
- for this branch, the cert subject key is the newly linked device key
- for this branch, the required claims should be just:
  - `member_id`

Issuer rule for this branch:

- the issuer is an already-trusted device key for the same member UUID in the
  same team

### 2. Add an explicit device-set table to the team DB

The current `member` table only has one `device_public_key`, which is not
enough to represent a device set.

Recommended branch shape:

- keep `member.device_public_key` for one branch as the **primary
  peer/session device key** used by the current runtime outside Git-signature
  verification
- add a new `member_device` table that records all linked device keys for a
  member

Likely fresh-schema shape:

```sql
CREATE TABLE IF NOT EXISTS member_device (
    member_id BLOB NOT NULL,
    device_public_key BLOB NOT NULL,
    added_at TEXT NOT NULL,
    revoked_at TEXT,
    PRIMARY KEY (member_id, device_public_key),
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);
```

The authoritative device set would then be:

- `membership` subject key for the initial device
- plus later `device_link` subject keys
- mirrored into `member_device` for fast local operations

### 3. Add a local Manager provisioning path for linking a new device key

The first implementation slice does **not** need to solve full new-machine
bootstrap.

Instead, add a local provisioning/helper path that proves the trust flow and DB
shape. For example:

- generate a new team-device key for an existing member
- issue a `device_link` cert from the current active device key
- insert the new device key into `member_device`
- optionally switch `member.device_public_key` to the new key

This could be exposed as a lower-level provisioning helper first, with Manager
UI/wrapper methods added only if they stay cheap.

The important thing is to land one end-to-end path that creates and stores a
real `device_link` cert in a real team DB.

### 4. Make Git link signatures device-aware

For this branch, we should go a bit further than the first draft:

- allow a pushed link to be signed by any currently linked device key for that
  member
- make verification succeed if the signature matches any trusted linked device
  key for that member
- stop assuming that `member.device_public_key` is the only valid link-signing
  key

This likely means changing the CodSync signature payload from a bare
`member_id -> signature` map to a richer shape that identifies the signing
device key (or key id) alongside the member.

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

### 2. Team schema

Expected work:

- add `member_device`
- keep `member.device_public_key` for current runtime compatibility
- store linked device keys in both cert history and `member_device`

### 3. `small_sea_manager.provisioning`

Expected work:

- add a helper to generate an additional local team-device key for an existing
  member
- add a helper to issue/store a `device_link` cert
- update the team DB accordingly
- if needed, add a narrow helper for "make this newly linked key the primary
  peer/session key on this installation"

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
  - verify `member_device` contains both keys
  - verify whichever field remains in `member` matches the branch's chosen
    primary-device semantics
- update signed-bundle tests so one member can be verified through more than one
  linked device key

## Risks

### 1. `member.device_public_key` can become ambiguous

Once a member has multiple linked devices, the current column name starts to
mean something narrower like "primary peer/session device key on this
installation," not "the only device key." That is okay for one branch, but the
plan should name the ambiguity directly so later work can remove it cleanly.

### 2. Git signature shape churn can spill into verification paths

Changing link signatures from member-only to device-aware is still a cross-cut
through CodSync and signed-bundle tests. That is manageable, but it should stay
deliberately narrow.

### 3. It is easy to over-promise multi-device support

If the branch lands `device_link` certs plus device-aware Git signatures,
readers may infer that all linked devices are fully live. The docs and tests
should be explicit that this is not yet true for sender-key behavior or peer
routing.

### 4. The bootstrap story is still unresolved

The current spec imagines a device-link token that clones NoteToSelf and then
fans out across teams. This branch should not accidentally half-implement that
story without enough validation.

## Validation

This branch is successful if:

- the code can issue and verify a real `device_link` cert
- the team DB can store more than one device key for one member
- the Manager provisioning layer can record a second linked device key for one
  member in at least one realistic local happy path
- signed-bundle verification succeeds for a member whose valid signer is one of
  several linked device keys
- the updated micro tests pass

Suggested validation command:

`uv run pytest packages/wrasse-trust/tests/test_identity.py packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_signed_bundles.py`

## Locked Scoping Decision

This first `device_link` branch **should** include device-aware Git signing and
verification, not just cert/data-model work.

It still should **not** try to make multiple devices simultaneously live for
sender-key state, peer routing, or whole-install bootstrap. That keeps the
branch manageable while landing a meaningful multi-device capability.
