# Branch Plan: Linked-Device Encrypted Team Bootstrap

**Branch:** `issue-69-linked-device-encrypted-team-bootstrap`  
**Base:** `main` (incorporating `issue-59-sender-device-runtime-identity`)  
**Primary issue:** #69 "Bootstrap encrypted team access for a newly linked device"  
**Related issues:** #59, #43, #48, #58  
**Related docs:** `packages/cuttlefish/README.md`,
`packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-device-linking.md`

## Context

After the issue-59 branch:

- sender-key runtime identity is device-scoped: each active team-device key has
  its own sender stream, identified by `sender_device_key_id`
- device_link certs exist: a member can have multiple verified team-device keys
- `issue_device_link_for_member` creates the cert and commits it to the team DB
- `get_trusted_device_keys_for_member` can look up all trusted device keys for a
  member in a team

What is missing: there is no flow for Device B — a device that already belongs
to the identity but is new to an existing encrypted team — to become a live
recipient for its already-active sibling device's sender-key broadcast.

Concretely: Alice has Device A (founding device, active in Team X) and Device B
(newly linked). Device B holds a valid device_link cert. It does not yet have:

- a local sender-key record for Team X (its own sender stream)
- peer sender-key receiver records for Alice/Device A
- a pairwise bootstrap channel for receiving Team X crypto runtime from Device A

Before Device B can decrypt or send encrypted team bundles, it needs an honest
bootstrap flow.

The crypto primitives required already exist:

- `cuttlefish.prekeys` — X3DH prekey bundle generation and storage
- `cuttlefish.x3dh` — asynchronous pairwise key agreement
- `cuttlefish.ratchet` — Double Ratchet for forward-secret message delivery
- `cuttlefish.group` — Sender Keys team encryption

None of them are yet wired for the linked-device bootstrap use case.

## Proposed Goal

After this branch lands:

1. Device B can initiate a same-member team bootstrap request for Team X using:
   its already-existing NoteToSelf device signing key, its already-existing
   Team X team-device key, and a fresh X3DH prekey bundle generated on-device
2. Device A can process that request, verify that it came from a legitimately
   linked sibling device for Alice's own Team X member, and produce an encrypted
   bootstrap response that delivers Device A's sender-key distribution to Device B
   via X3DH + Double Ratchet
3. Device B can process the response, initialize its local receiver state for
   Device A, generate its own sender key locally, and return a signed
   distribution payload so Device A can decrypt Device B's future sends
4. After the exchange, Device B can decrypt future encrypted team bundles from
   Device A, and Device A can decrypt future encrypted team bundles from Device B
5. Current persistent team state availability and sender-key history remain
   separate concerns: Device B may receive a readable current baseline by fresh
   CodSync snapshot, but it cannot decrypt pre-bootstrap sender-key history

## Why This Slice

The smallest honest implementation is the same-member case: Device A and Device
B belong to the same participant; Device A bootstraps Device B.

This slice does **not** need to solve:

- other team members (Bob, Carol…) distributing their sender keys to Device B
  — that requires a separate trigger/notification round and belongs to a later
  slice or #43
- periodic sender-key rotation and redistribution (#43)
- async prekey bundle publication infrastructure: this branch uses the same
  manual out-of-band exchange pattern as the invitation flow, not a
  full Hub-mediated async prekey service
- NoteToSelf cross-device sync and team discovery (#48)

But it **does** need to leave those later branches possible without rework.

## Preconditions

This branch assumes all of the following are already true before the bootstrap
exchange begins:

- Device B has already completed identity join and holds its local NoteToSelf
  device private keys
- Device B has already generated its own Team X team-device keypair locally;
  no sibling device mints team-device private keys for it
- Device B's team-device public key has already been admitted to Team X trust
  history via the existing `device_link` path
- Device B can already obtain a readable current Team X baseline; the default
  strategy is that the sponsoring device publishes a fresh CodSync full-snapshot
  bundle or equivalent resealed current-state export
- manual out-of-band transfer of bootstrap payloads is acceptable for this slice

This branch therefore does **not** own:

- generating or certifying Device B's team-device key
- discovering Team X from NoteToSelf sync
- optimizing current-baseline delivery beyond the fresh-snapshot default

## Scope Decisions Already Made

### 1. Current baseline vs. historical sender-key access

Device B starts its sender-key chain fresh. It does not receive historical
message keys for messages encrypted before the bootstrap completed.

This branch treats two things separately:

- **current persistent team state:** Device B can receive a readable current
  baseline through a fresh CodSync full snapshot or equivalent export
- **historical encrypted sender-key traffic:** Device B does not receive old
  sender-chain state and cannot decrypt messages from before the bootstrap

That is the correct default: it is honest about the difference between "can read
the current repo state" and "can decrypt old encrypted payloads," and it does
not accidentally grant catch-up access that the broader team did not explicitly
authorize.

Later policy decisions (e.g. controlled historical export) belong to a dedicated
branch when there is an identified product need.

### 2. Same-member security binding only for this branch

This branch proves the bootstrap mechanism between two devices of the same
member. Cross-member sender-key redistribution (Alice's Device B needs Bob's
sender-key distribution too) is a follow-up. It requires a different trigger:
Bob must notice Device B exists and resend. That is closer to #43 than to #69.

The request is accepted only if all of the following hold:

- Device B signs the join request with its NoteToSelf device signing key
- Device B signs the same request with its Team X team-device private key
- Device A verifies the NoteToSelf signature against Device B's `user_device`
  signer key from shared NoteToSelf state
- Device A verifies the team-device signature against the team-device public key
  named in the request
- Device A verifies that the named team-device public key is trusted for
  Device A's own `self_in_team` member in Team X

This keeps the branch narrow: same participant, same team member, same-team
crypto release.

### 3. Three-payload manual out-of-band exchange

This branch uses the same pattern as the invitation flow: serialized token
payloads exchanged out-of-band. No Hub-mediated async prekey service is needed.

The three payloads:

1. Device B → Device A: **join request bundle**
   `bootstrap_id + team_id + device_id + team-device public key + X3DH prekey
   bundle + NoteToSelf signature + team-device signature`
2. Device A → Device B: **bootstrap bundle**
   `bootstrap_id + X3DH initial message + ratchet-encrypted Device A sender-key
   distribution + active sender-device identifier + Device A team-device signature`
3. Device B → Device A: **sender distribution payload**
   `bootstrap_id + Device B SenderKeyDistributionMessage + Device B team-device
   signature`

All device-related keys are generated on-device. No sibling device creates or
copies another device's team-device or X3DH private key material.

### 4. Separate X3DH bootstrap keys from durable device identities

`cuttlefish.prekeys.IdentityKeyPair` uses X25519 (DH) + Ed25519 (signing).
These are distinct from the team-device Ed25519 signing key that appears in
wrasse_trust cert history. The X3DH identity keys are ephemeral bootstrap
session keys; the team-device key remains the durable trust-side identity.

For this branch:

- Device B generates a fresh X3DH identity keypair, signed prekey, and at least
  one one-time prekey for each bootstrap session
- Device A may generate an ephemeral X3DH sender identity for each bootstrap
  response
- these bootstrap-session keys are stored only as long as needed to complete
  the bootstrap and are then deleted or allowed to expire locally
- the durable NoteToSelf and team-device keys remain the identities that
  authorize the exchange

## In Scope

### 1. Device-local prekey storage schema

Add tables to `device_local_schema.sql` (and corresponding migration) for:

- bootstrap session records keyed by `bootstrap_id`
- Device B's own X3DH identity key pair (per team, per bootstrap session)
- Device B's signed prekey private key
- Device B's one-time prekey private keys (consumed on use)
- any ratchet/bootstrap state that must survive process restarts until the
  three-payload exchange is complete

These are device-local secrets; they must not appear in the shared NoteToSelf
sync DB.

### 2. `prepare_linked_device_team_join`

New Manager function. Device B calls this before initiating bootstrap.

Steps:
- assert that Device B already has a local NoteToSelf device signing key and a
  local Team X team-device private key
- generate a fresh X3DH identity key pair, signed prekey, and at least one
  one-time prekey
- store the bootstrap-session private state keyed by `bootstrap_id`
- return a serialized join-request bundle containing the prekey bundle plus
  identifying context (`bootstrap_id`, `team_id`, `device_id`,
  Device B's team-device public key)
- sign the join-request bundle with both Device B's NoteToSelf device key and
  Device B's Team X team-device key

### 3. `create_linked_device_bootstrap`

New Manager function. Device A calls this with Device B's join-request bundle.

Steps:
- deserialize the join request
- verify the NoteToSelf signature against Device B's `user_device.signing_key`
- verify the Team X team-device signature against the team-device public key
  named in the request
- look up the named team-device public key in the trusted device keys for
  Device A's own `self_in_team` member; reject if not found
- initiate X3DH using Device B's prekey bundle → get shared secret
- initialize a Double Ratchet sender session
- encrypt Device A's `SenderKeyDistributionMessage` (from Device A's local sender
  record) inside a ratchet message
- serialize and return the bootstrap bundle, signed by Device A's Team X
  team-device key
- persist any bootstrap state needed to safely accept Device B's later sender
  distribution for this `bootstrap_id`

### 4. `finalize_linked_device_bootstrap`

New Manager function. Device B calls this with Device A's bootstrap bundle.

Steps:
- verify Device A's Team X team-device signature on the bootstrap bundle
- load the stored bootstrap-session state by `bootstrap_id`
- receive X3DH and ratchet state; derive shared secret
- decrypt the ratchet message; extract Device A's sender-key distribution
- store the distribution as a peer receiver record in device-local DB
- generate Device B's own sender key for the team
- return Device B's signed `SenderKeyDistributionMessage` payload (for
  Device A to store)
- mark the bootstrap session finalized in an idempotent way

Device A's caller verifies the returned signature and stores this distribution as
a peer receiver record for Device B.

### 5. Schema migration

Bump `LOCAL_SCHEMA_VERSION` and add the new prekey/ratchet session tables. Add a
migration path in `_migrate_device_local_db`.

### 6. Micro tests

Minimum expected coverage:

- full same-member round-trip across all three payloads: Device B ends up able
  to decrypt a group message from Device A; Device A ends up able to decrypt a
  group message from Device B
- rejection: `create_linked_device_bootstrap` raises if either join-request
  signature is invalid, or if the requesting team-device key is not in the
  trusted device keys for Device A's own member
- historical access boundary: a message encrypted by Device A before the
  bootstrap is not decryptable by Device B after it (Device B's receiver record
  starts at the current chain iteration, not at 0)
- idempotent finalization: finalizing the same bootstrap bundle twice does not
  corrupt local state or mint a second sender stream for Device B
- no bootstrap private material appears in the shared NoteToSelf DB

## Out Of Scope

- cross-member sender-key redistribution to a newly linked device (#43 / #59
  second slice)
- generating Device B's Team X team-device keypair
- issuing or syncing Device B's `device_link` trust material
- async Hub-mediated prekey bundle publication and retrieval
- periodic sender-key rotation and redistribution (#43)
- NoteToSelf cross-device sync and team discovery (#48)
- device-aware peer routing or watch behavior (#59)
- revocation of a linked device and its sender-key material
- provider-specific optimization for current-baseline delivery beyond the fresh
  CodSync full-snapshot default
- implementing `make_device_link_invitation`; this branch leaves that stub alone

## Concrete Change Areas

### 1. `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`

New tables for bootstrap session state, prekey private keys, and any ratchet
state needed during the three-payload exchange.

### 2. `packages/small-sea-note-to-self/small_sea_note_to_self/db.py`

Schema version bump and migration.

### 3. `packages/small-sea-manager/small_sea_manager/provisioning.py`

Three new functions:
- `prepare_linked_device_team_join`
- `create_linked_device_bootstrap`
- `finalize_linked_device_bootstrap`

`make_device_link_invitation` remains a stub in this branch.

### 4. Micro tests

New test file `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
covering the cases listed above.

## Open Questions

### 1. Should Device A's bootstrap response also carry a NoteToSelf signature?

The request clearly benefits from both signatures: NoteToSelf binds the shared
identity-side `device_id`, while Team X team-device signing binds the request to
team trust. For the response, Team X team-device signing is definitely needed;
the remaining question is whether a second NoteToSelf signature adds enough
clarity or defense-in-depth to justify the extra surface.

### 2. What is the cleanup policy for completed or abandoned bootstrap sessions?

The branch now keys bootstrap state by `bootstrap_id`, but implementation still
needs a small policy:

- when to delete consumed one-time prekeys
- when to delete finalized bootstrap-session state
- whether abandoned sessions expire on time, on next startup, or only when a
  new bootstrap supersedes them

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- a newly linked sibling device can decrypt future encrypted team bundles from
  its already-active counterpart after completing the three-payload bootstrap
- the already-active device can decrypt future bundles from the newly bootstrapped
  device
- no message encrypted before the bootstrap is decryptable by the new device
- the bootstrap is rejected if the request is not validly signed by the linked
  device's NoteToSelf key and Team X team-device key
- the bootstrap is rejected if the requesting team-device key is not in the
  trusted device key history for Device A's own member
- a readable current Team X baseline and historical sender-key access are
  treated separately and honestly
- no fake historical export, no shared synced storage of private sender-key state
- the exchange uses X3DH + Double Ratchet honestly, not a raw key copy
- all new X3DH, prekey, and bootstrap-session private material stays in the
  device-local DB or local secret files; none appears in shared NoteToSelf or a
  team DB
- Manager remains the owner of direct team DB writes; the branch does not add
  a side door around that rule
- micro tests stay local-only and do not require internet services

## Outcome

To be filled in at wrap-up.
