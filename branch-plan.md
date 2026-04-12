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
recipient for that team's sender-key broadcast.

Concretely: Alice has Device A (founding device, active in Team X) and Device B
(newly linked). Device B holds a valid device_link cert. It does not yet have:

- a local sender-key record for Team X (its own sender stream)
- peer sender-key receiver records for Alice/Device A or any other team member

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

1. Device B can initiate a team bootstrap request, producing a bundle that
   carries its X3DH prekey material for the target team
2. Device A can process that bundle, verify Device B is a legitimately linked
   sibling device, and produce an encrypted bootstrap response that delivers
   Alice's sender-key distribution to Device B via X3DH + Double Ratchet
3. Device B can process the response, initialize its local sender-key state for
   Team X, generate its own sender key, and return a distribution message so
   Device A can decrypt Device B's future sends
4. After the exchange, Device B can decrypt future encrypted team bundles from
   Device A, and Device A can decrypt future encrypted team bundles from Device B
5. The historical access boundary is explicit and enforced: Device B cannot
   decrypt messages from before the bootstrap completed

## Why This Slice

The smallest honest implementation is the same-member case: Device A and Device
B belong to the same participant; Device A bootstraps Device B.

This slice does **not** need to solve:

- other team members (Bob, Carol…) distributing their sender keys to Device B
  — that requires a separate trigger/notification round and belongs to a later
  slice or #43
- periodic sender-key rotation and redistribution (#43)
- async prekey bundle publication infrastructure: this branch uses the same
  two-step manual out-of-band exchange pattern as the invitation flow, not a
  full Hub-mediated async prekey service
- NoteToSelf cross-device sync and team discovery (#48)

But it **does** need to leave those later branches possible without rework.

## Scope Decisions Already Made

### 1. Historical access: join-time forward only

Device B starts its sender-key chain fresh. It does not receive historical
message keys for messages encrypted before the bootstrap completed.

This is the correct default: it is the honest policy, it requires no
"current-baseline snapshot" export, and it does not accidentally grant catch-up
access that the broader team did not explicitly authorize.

Later policy decisions (e.g. controlled historical export) belong to a dedicated
branch when there is an identified product need.

### 2. Same-member case only for this branch

This branch proves the bootstrap mechanism between two devices of the same
member. Cross-member sender-key redistribution (Alice's Device B needs Bob's
sender-key distribution too) is a follow-up. It requires a different trigger:
Bob must notice Device B exists and resend. That is closer to #43 than to #69.

### 3. Two-step manual out-of-band exchange

This branch uses the same pattern as the invitation flow: two serialized token
payloads exchanged out-of-band. No Hub-mediated async prekey service is needed.

The two rounds:

1. Device B → Device A: **join request bundle** (X3DH prekey material, device-link
   cert reference, target team ID)
2. Device A → Device B: **bootstrap bundle** (X3DH initial message + ratchet
   message carrying Alice's sender-key distribution)

After round 2, Device B sends its own sender-key distribution back to Device A
as a third payload (or Device A stores it from the bootstrap response directly
if the bundle is symmetric — this is a detail to settle during implementation).

### 4. Separate X3DH identity keys per team, per bootstrap session

`cuttlefish.prekeys.IdentityKeyPair` uses X25519 (DH) + Ed25519 (signing).
These are distinct from the team-device Ed25519 signing key that appears in
wrasse_trust cert history. The X3DH identity keys are ephemeral bootstrap
session keys; the team-device key remains the durable trust-side identity.

Device B generates a fresh X3DH identity key pair for each team bootstrap
session. The private keys are stored in device-local storage and discarded after
the session is established.

## In Scope

### 1. Device-local prekey storage schema

Add tables to `device_local_schema.sql` (and corresponding migration) for:

- Device B's own X3DH identity key pair (per team, per bootstrap session)
- Device B's one-time prekey private keys (consumed on use)
- Ratchet session state for the bootstrap channel between Device A and Device B

These are device-local secrets; they must not appear in the shared NoteToSelf
sync DB.

### 2. `prepare_linked_device_team_join`

New Manager function. Device B calls this before initiating bootstrap.

Steps:
- generate a fresh X3DH identity key pair and a batch of one-time prekeys
- store private keys in device-local DB
- return a serialized join-request bundle containing the prekey bundle plus
  identifying context (team ID, Device B's team-device public key)

### 3. `create_linked_device_bootstrap`

New Manager function. Device A calls this with Device B's join-request bundle.

Steps:
- deserialize the join request
- look up Device B's team-device public key in the trusted device keys for the
  member; reject if not found
- initiate X3DH using Device B's prekey bundle → get shared secret
- initialize a Double Ratchet sender session
- encrypt Alice's `SenderKeyDistributionMessage` (from Device A's local sender
  record) inside a ratchet message
- also include Device B's fresh sender-key initialization data so Device A can
  immediately store a receiver record for Device B
- serialize and return the bootstrap bundle

### 4. `finalize_linked_device_bootstrap`

New Manager function. Device B calls this with Device A's bootstrap bundle.

Steps:
- receive X3DH and ratchet state; derive shared secret
- decrypt the ratchet message; extract Alice's sender-key distribution
- store the distribution as a peer receiver record in device-local DB
- generate Device B's own sender key for the team
- return Device B's `SenderKeyDistributionMessage` (for Device A to store)

Device A's caller stores this distribution as a peer receiver record for
Device B.

### 5. Schema migration

Bump `LOCAL_SCHEMA_VERSION` and add the new prekey/ratchet session tables. Add a
migration path in `_migrate_device_local_db`.

### 6. Micro tests

Minimum expected coverage:

- full same-member round-trip: Device B ends up able to decrypt a group message
  from Device A; Device A ends up able to decrypt a group message from Device B
- rejection: `create_linked_device_bootstrap` raises if the requesting device
  key is not in the trusted device keys for the member
- historical access boundary: a message encrypted by Device A before the
  bootstrap is not decryptable by Device B after it (Device B's receiver record
  starts at the current chain iteration, not at 0)
- idempotent finalization: finalizing the same bootstrap bundle twice does not
  corrupt local state

## Out Of Scope

- cross-member sender-key redistribution to a newly linked device (#43 / #59
  second slice)
- async Hub-mediated prekey bundle publication and retrieval
- periodic sender-key rotation and redistribution (#43)
- NoteToSelf cross-device sync and team discovery (#48)
- device-aware peer routing or watch behavior (#59)
- revocation of a linked device and its sender-key material

## Concrete Change Areas

### 1. `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`

New tables for X3DH session state and one-time prekey private keys.

### 2. `packages/small-sea-note-to-self/small_sea_note_to_self/db.py`

Schema version bump and migration.

### 3. `packages/small-sea-manager/small_sea_manager/provisioning.py`

Three new functions:
- `prepare_linked_device_team_join`
- `create_linked_device_bootstrap`
- `finalize_linked_device_bootstrap`

`make_device_link_invitation` is currently a stub (`pass`); this branch may or
may not fill it in depending on whether the manual token approach makes it
redundant for the immediate use case.

### 4. Micro tests

New test file `packages/small-sea-manager/tests/test_linked_device_bootstrap.py`
covering the four cases listed above.

## Open Questions

### 1. Does `create_linked_device_bootstrap` need to consume a one-time prekey?

In strict X3DH the sender should consume one of the recipient's one-time
prekeys. If this branch uses a single-use prekey bundle model (generate one
batch per bootstrap session, not a persistent upload), the exhaustion concern
is minor. But the `PrekeyExhaustionPolicy.STRICT` default in `cuttlefish.x3dh`
will raise if no one-time prekeys are supplied. Worth confirming whether the
bootstrap bundle always includes at least one.

### 2. Should Device B's sender-key distribution be included in the bootstrap bundle, or returned as a separate payload?

If it is part of the bootstrap response, the full exchange is two token transfers
and Device A immediately has a receiver record for Device B. If it is separate,
the flow requires a third transfer but each direction is cleaner. Settle before
implementation.

### 3. What is the right storage key for the ratchet session state?

The ratchet session between Device A and Device B is ephemeral for the bootstrap
purpose, but its state (advancing chain for message delivery) needs to survive
process restarts until both sides have confirmed receipt. Key candidates:
`(team_id, device_b_key_id)` or `(team_id, member_id, device_b_key_id)`.

### 4. `make_device_link_invitation` stub

This function currently returns `pass`. It may overlap with
`prepare_linked_device_team_join`. Decide whether to fill it in, replace it, or
leave it as a stub.

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- a newly linked sibling device can decrypt future encrypted team bundles from
  its already-active counterpart after completing the two-round bootstrap
- the already-active device can decrypt future bundles from the newly bootstrapped
  device
- no message encrypted before the bootstrap is decryptable by the new device
- the bootstrap is rejected if the requesting device key is not in the team's
  trusted device key history for the member
- no fake historical export, no shared synced storage of private sender-key state
- the exchange uses X3DH + Double Ratchet honestly, not a raw key copy

## Outcome

To be filled in at wrap-up.
