# Branch Plan: Sender-Device Runtime Identity

**Branch:** `issue-59-sender-device-runtime-identity`  
**Base:** roadmap commit `a792ec5` (`issue-44-sender-key-runtime`) on top of `main`  
**Primary issue:** #59 "Make linked devices first-class for sender keys and peer routing"  
**Related issues:** #69, #43, #48, #4  
**Related docs:** `architecture.md`, `packages/cuttlefish/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-issue-44-sender-key-runtime.md`,
`Archive/branch-plan-device-linking.md`,
`Archive/branch-plan-note-to-self-shared-device-local-split.md`

## Context

The roadmap branch settled the big-picture direction for encrypted team runtime:

- pairwise device-specific channels for the control plane
- sender-key broadcast for the data plane
- sender runtime should be device-scoped, not member-scoped
- sender-key runtime stays device-local

Current code still encodes the older member-scoped shape in several places:

- `cuttlefish.group` uses `sender_participant_id` throughout its public API
- device-local sender-key tables are keyed by `(team_id, sender_participant_id)`
- Hub decrypt looks up receiver state by `message.sender_participant_id`
- provisioning initializes sender-key state from `member_id`
- invitation tests explicitly assert that sender-key identity equals the team
  member ID

That mismatch is now the first blocking implementation problem under `#59`.
Before we can honestly bootstrap an already-linked device into an encrypted team
(`#69`) or route peer updates by sibling device, the runtime needs one settled
answer to "which sender device does this key stream belong to?"

## Proposed Goal

After this branch lands:

1. sender-key runtime identity is device-scoped across Cuttlefish, local
   sender-key storage, Hub crypto helpers, and current bootstrap flows
2. one recipient device can store two sender-key receiver records for two
   linked devices of the same team member without collision
3. existing first-device flows (`create_team`, invitation acceptance) still
   work, but now seed sender-key state with the founding device's identity
   rather than the member UUID
4. the codebase is positioned for `#69` to solve encrypted team bootstrap for a
   newly linked device without first undoing member-scoped assumptions

## Why This Slice

This slice is the smallest implementation branch that changes the runtime model
honestly.

It does **not** need to solve:

- how a newly linked device receives sender-key material for an existing team
- how sender keys rotate and redistribute over the encrypted control plane
- how sibling devices become separate routing or notification endpoints

But it **does** need to make those later branches possible without semantic
backtracking.

## Scope Decisions Already Made

### 1. Use device-key identity, not NoteToSelf `user_device.id`

This branch should identify a sender stream by the current **team-device key**,
not by the NoteToSelf `user_device.id`.

Reason:

- teammates can verify and reason about team-device public keys through
  `wrasse_trust` cert history
- teammates do **not** share each other's private NoteToSelf `user_device`
  namespace
- using `user_device.id` as the cross-team sender identity would introduce a
  hidden dependency on data peers do not have

### 2. Use `key_id(public_key)` as the concrete runtime identifier

The concrete sender runtime identifier should be the Wrasse Trust key ID of the
current team-device public key:

- `sender_device_key_id = SHA-256(team_device_public_key)[:16]`

This should be the value carried in sender-key distribution messages, group
messages, and local sender-key tables.

Why this is the cleanest first choice:

- it is already the trust-side notion of stable public-key identity
- it is compact enough for wire payloads and SQLite keys
- it avoids embedding a 32-byte public key everywhere just to get a stable name

### 3. Rename cleanly; do not preserve member-scoped field names

This repo is pre-alpha. The branch should rename the sender-key runtime fields
and helpers to match their new semantics rather than keeping
`sender_participant_id` around as a misleading alias.

Expected direction:

- `sender_participant_id` → `sender_device_key_id`

across:

- `cuttlefish.group`
- JSON serialization helpers
- device-local sender-key schema
- Hub crypto message payloads
- tests

### 4. Keep the branch honest about what remains unsolved

This branch should not fake linked-device support by:

- copying sender-key state across devices through synced storage
- inventing a temporary historical-key export
- adding peer-routing endpoint semantics prematurely

Those belong to later branches (`#69`, `#43`, `#59` second slice).

## In Scope

### 1. Update `cuttlefish.group` to device-scoped sender identity

Change the low-level sender-key API so the sender stream is named by device-key
identity rather than team-member identity.

Concrete direction:

- rename dataclass fields and constructor parameters from
  `sender_participant_id` to `sender_device_key_id`
- update docstrings so receiver records are keyed by
  `(group_id, sender_device_key_id)`
- keep the actual sender-key crypto unchanged; this branch is about identity
  semantics, not a new group protocol

### 2. Update device-local sender-key storage and helpers

Update `small_sea_note_to_self` sender-key storage to use device-scoped naming
and keys.

Concrete direction:

- update `device_local_schema.sql`
- update `sender_keys.py`
- key both `team_sender_key` and `peer_sender_key` by sender-device identity
- keep the state device-local

Implementation note:

- each installation still has only one **local** active team-device key in the
  current happy path, but the schema should stop pretending the sender runtime
  is member-scoped

### 3. Add one public helper for key ID derivation

The repo already derives key IDs from public keys in more than one place.
Promote one public helper in `wrasse_trust.keys` and use it rather than
repeating the SHA-256 logic ad hoc in Manager/runtime code.

This is a small integrity win that naturally belongs in this slice.

### 4. Update Hub runtime lookup paths

Update `small_sea_hub.crypto` so:

- upload paths load the local sender record for the current device-scoped sender
  identity
- decrypt paths look up receiver state by `message.sender_device_key_id`
- error messages and serialized payloads use device-scoped naming

### 5. Update current bootstrap/provisioning paths

This branch should keep existing first-device flows working.

Concrete direction:

- `create_team(...)` should initialize the creator's sender-key state from the
  creator's current team-device key identity
- invitation token and acceptance token sender-key payloads should carry sender
  device-key identity, not member ID
- `accept_invitation(...)` and `complete_invitation_acceptance(...)` should
  persist peer sender-key receiver state keyed by sender device-key identity

This does **not** mean invitation/bootstrap is "done" for linked devices; it
only means the existing first-device path stays coherent after the identity
flip.

### 6. Update specs and micro tests

Update the relevant specs and tests so they describe and enforce the new model.

Minimum expected micro test coverage:

- `cuttlefish` tests updated for the renamed sender identity field
- `test_create_team.py` updated to assert local sender-key state is keyed by the
  founding team-device key ID rather than the member ID
- `test_invitation.py` updated to assert inviter/acceptor sender-key payloads
  and local receiver state use device-key identity
- one new focused runtime micro test proving that a recipient device can hold
  two peer sender-key records for the same team from two different sender
  devices of the same member without collision

## Out Of Scope

- encrypted team bootstrap for a newly linked device (`#69`)
- current-baseline publication or fresh snapshot export for joiners (`#69`)
- encrypted sender-key rotation and redistribution (`#43`)
- periodic rotation trigger policy (`#43`)
- device-aware peer routing, watch behavior, or peer table redesign (`#59`
  second slice)
- OS keychain / enclave redesign
- broader trust-schema projection work (`#57`)

## Concrete Change Areas

### 1. `packages/cuttlefish/cuttlefish/group.py`

- rename sender identity fields and parameters
- update related tests under `packages/cuttlefish/tests/test_group.py` and any
  sender-key serialization tests

### 2. `packages/wrasse-trust/wrasse_trust/keys.py`

- expose a public helper for deriving `key_id` from a public key

### 3. `packages/small-sea-note-to-self`

- `small_sea_note_to_self/sql/device_local_schema.sql`
- `small_sea_note_to_self/sender_keys.py`

Expected outcome:

- local sender-key tables and helpers speak in terms of sender device-key ID
- helper signatures stop encoding member-scoped assumptions

### 4. `packages/small-sea-hub/small_sea_hub/crypto.py`

- load/save sender-key runtime using device-scoped keys
- serialize group messages with the new field name
- keep encryption behavior otherwise unchanged

### 5. `packages/small-sea-manager/small_sea_manager/provisioning.py`

- derive sender-device key ID from the current team-device public key when
  initializing sender runtime
- update invitation/acceptance bootstrap payloads and local persistence paths

### 6. Specs and tests

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-hub/spec.md`
- relevant micro tests in Manager / Hub / Cuttlefish

## Validation

This branch should convince a skeptical reviewer that it solved the intended
problem if all of the following are true:

- no live sender-key runtime path still keys sender state by member UUID
- the new runtime identifier is explicitly tied to a team-device public key, not
  to private NoteToSelf-only identifiers
- two sender streams from two linked devices of the same member can coexist on a
  recipient device without overwriting each other
- current first-device team creation and invitation flows still pass after the
  identity flip
- the branch does not smuggle in fake solutions for `#69`, `#43`, or peer
  routing
- the code gets a little cleaner, not just more different:
  - field names match semantics
  - key-ID derivation stops being duplicated ad hoc

## Validation and Micro Tests

The branch should aim to prove, at minimum:

1. low-level group sender-key behavior is unchanged apart from renamed sender
   identity fields
2. `create_team(...)` stores a local sender record whose sender identity matches
   the current team-device key ID
3. the invitation flow stores inviter/acceptor peer sender records keyed by
   team-device identity rather than member ID
4. a recipient device can decrypt messages from two distinct sender-device IDs
   in one team without state collision

## Risks To Avoid

- choosing NoteToSelf `user_device.id` as the cross-team sender identity
- overloading the sender-key signing public key as the sender device identity
- solving linked-device bootstrap implicitly in this branch instead of in `#69`
- leaving member-scoped names in place after changing the actual semantics

## Outcome

To be filled in at wrap-up.
