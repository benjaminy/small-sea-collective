# Branch Plan: Encrypted Sender-Key Rotation and Redistribution

**Branch:** `issue-43-sender-key-rotation`  
**Base:** `main`  
**Primary issue:** #43 "Add encrypted sender-key rotation and redistribution flow"  
**Related issues:** #59, #69, #48, #73  
**Related archive plans:** `Archive/branch-plan-issue-44-sender-key-runtime.md`,
`Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`

## Context

Current sender-key bootstrap works through two paths:

1. **Invitation acceptance** (`provisioning.py`): inviter and acceptor exchange
   `SenderKeyDistributionMessage` payloads inside invitation/acceptance tokens.
   These are serialized as cleartext JSON fields — not encrypted over a pairwise
   channel.
2. **Linked-device bootstrap** (`provisioning.py`): Device A sends its sender-key
   distribution to Device B over X3DH + Double Ratchet. Device B returns its
   distribution signed. This is the honest encrypted path, but it only covers
   same-member bootstrap.

What is missing:

- **No rotation trigger**: `remove_member` is `raise NotImplementedError`.
  There is no code to rotate a sender key after membership changes.
- **No redistribution mechanism**: after a device rotates its sender key, there
  is no way to deliver fresh `SenderKeyDistributionMessage`s to all remaining
  peer devices over encrypted channels.
- **No cross-member redistribution for newly linked devices**: when Device B
  joins via linked-device bootstrap (#69), it only receives Device A's (its
  sibling's) sender-key distribution. Bob's devices still don't know Device B
  exists and haven't sent it their sender keys.
- **Invitation-path sender keys are unencrypted**: the invitation flow embeds
  sender-key material as JSON fields in the token, not over a pairwise encrypted
  channel.

The roadmap plan (P4) established that the Manager serializes control-plane
decisions (rotate, admit, remove) and the actual `SenderKeyDistributionMessage`s
travel over encrypted device-to-device channels, mediated by the Hub.

## Proposed Goal

After this branch lands:

1. A device can rotate its own team sender key, generating a fresh chain and
   distributing it to all known peer devices
2. Membership removal triggers mandatory sender-key rotation for all remaining
   devices (so the removed member's devices cannot decrypt future traffic)
3. A newly linked device that completed same-member bootstrap (#69) can receive
   sender-key distributions from cross-member peer devices (not just its sibling)
4. Redistribution uses encrypted pairwise channels (X3DH + Double Ratchet),
   not cleartext token payloads
5. The branch documents deferred follow-ups clearly enough that they do not
   creep back into this implementation slice

## Why This Slice

This is the next natural implementation branch after #69. The linked-device
bootstrap proved the pairwise encrypted distribution mechanism for one pair of
devices. This branch generalizes that mechanism to N peer devices and adds the
rotation trigger logic.

It does **not** need to solve:

- Hub-mediated async prekey publication/retrieval (can use manual out-of-band
  exchange like prior branches, or a simple Hub relay)
- Device-aware peer routing or watch behavior (#59 second slice)
- NoteToSelf sync and team discovery (#48)
- Upgrading existing invitation-flow sender keys to encrypted channels (can be
  a follow-up; the invitation flow still works for first-device bootstrap)
- Periodic sender-key rotation policy and runtime enforcement (#73)

## Scope Decisions

### S1. Rotation triggers

One trigger is implemented in this branch:

1. **Member removal**: when any member is removed, every remaining sender device
   must rotate its sender key. This is the minimum honest guarantee — the removed
   member's devices must not be able to decrypt future traffic for participants
   who adopt that removal view and rotate accordingly.

Future triggers can reuse the same rotation primitive, but are deferred:

- **Device removal/revocation**: should eventually trigger the same rotation
  behavior, but is deferred until revocation cert design is tracked separately.
- **Manual rotation**: a natural follow-up if it is just "call the same
  rotation function."

### S2. Redistribution is device-to-device over encrypted channels

When a device rotates its sender key, it must deliver a fresh
`SenderKeyDistributionMessage` to every peer device in the team. "Peer device"
means every trusted team-device key that is not this device's own key.

The distribution must travel over an encrypted pairwise channel (X3DH + Double
Ratchet), not as cleartext in a shared location. The linked-device bootstrap
(#69) already proved this mechanism for one pair; this branch generalizes it.

### S3. Ephemeral pairwise sessions per redistribution round

Each rotation triggers fresh X3DH handshakes. At Small Sea's scale (small
teams, few devices), the cost of fresh X3DH per rotation round is negligible.
Persistent Double Ratchet sessions can be added later as an optimization. This
keeps the branch simpler and avoids introducing session lifecycle management
before it's needed.

For this to work, each device needs to publish prekey bundles that other devices
can consume. The linked-device bootstrap already generates prekeys; this branch
needs to generalize that to a per-device prekey publication mechanism.

### S4. Prekey bundle availability via team DB

Each device must make prekey bundles available to its peers. Prekey bundles are
stored in a new `device_prekey_bundle` table in the team DB (they are public
material — no secrets). The Manager writes them as part of team DB commits.

This is the simplest mechanism that respects the architecture: Manager owns team
DB writes, prekey bundles are public, and every device that pulls the team DB
gets access to peer prekey bundles.

### S5. Cross-member redistribution for newly linked devices

The redistribution primitive in this branch must be capable of sending a
current sender key to a newly linked cross-member device once the caller tells
it to do so. Automatic detection during pull/watch flows is deferred to #59 so
this branch stays focused on the crypto path rather than background
orchestration.

### S6. Remove member implementation

`remove_member` is currently `raise NotImplementedError`. This branch should
implement it:

1. Reject self-removal: `remove_member(team_name, self_in_team)` is not a valid
   operation in this branch.
2. Verify the caller has `read-write` permission on the team's Core berth
   (`SmallSeaCollectiveCore`) before mutating membership state.
3. Resolve and capture the removed member's currently trusted team-device
   public keys before mutating the team DB, then derive their
   `sender_device_key_id`s with `key_id_from_public()`. This captured key-id set
   is the source of truth for local cleanup later in the flow.
4. Load team certificates with `_load_team_certificates()`, filter in Python
   for rows where `cert.claims.get("member_id") == removed_member_id.hex()`,
   and delete those rows by `cert_id`. Do this in Python rather than SQL
   because `claims` is stored as JSON text. This explicit deletion removes the
   removed member's subject-side membership and `device_link` cert history.
5. Delete the removed member's `member` row. That cascade cleans `berth_role`,
   `peer`, and any remaining issuer-side certificate rows whose
   `issuer_member_id` is that member.
6. Commit to the team DB.
7. Purge local `peer_sender_key` rows whose `sender_device_key_id` is in the
   captured removed-device set.
8. Rotate this device's sender key.
9. Distribute the new sender key to all remaining peer devices.

## In Scope

### 1. Implement `remove_member` in provisioning

New function in `provisioning.py`. Removes the member from the team DB,
rejects self-removal, enforces Core-berth authorization, commits, purges
captured local receiver state for that member's devices, then triggers
sender-key rotation and redistribution.

### 2. Sender-key rotation function

New function: `rotate_team_sender_key(root_dir, participant_hex, team_name)`

- Creates a fresh sender key via `create_sender_key`
- Replaces the existing `team_sender_key` record
- Returns the new `SenderKeyDistributionMessage` for redistribution

### 3. Redistribution function

New function: `redistribute_sender_key(root_dir, participant_hex, team_name,
target_device_key_ids=None)`

- If `target_device_key_ids` is provided, distributes only to those devices.
  Otherwise defaults to all trusted peer devices in the team (via wrasse-trust
  cert lookups on the team DB). Those trust lookups return public keys, so the
  implementation derives `device_key_id`s with `key_id_from_public()` for
  `device_prekey_bundle` lookup and payload addressing. The caller controls
  when and to whom redistribution happens; the function handles the crypto.
- For each target peer device that has a published prekey bundle:
  - Performs X3DH key agreement
  - Encrypts the `SenderKeyDistributionMessage` via Double Ratchet
  - Produces a serialized distribution payload
- Returns the set of distribution payloads (one per peer device)

The actual delivery of these payloads is out of scope for the core function —
the caller (CLI or Hub) handles transport. But the branch should include at
least a test-level round-trip that proves the payloads are receivable.

### 4. Receive redistributed sender key

New function: `receive_sender_key_distribution(root_dir, participant_hex,
team_name, distribution_payload)`

- Verifies the sender's team-device signature
- Performs X3DH receive (using local prekey private material)
- Decrypts the Double Ratchet message
- Extracts the `SenderKeyDistributionMessage`
- Stores it as a `peer_sender_key` record

### 5. Prekey bundle publication

- When a device creates or joins a team, it generates and publishes a prekey
  bundle to the team DB
- New team DB table: `device_prekey_bundle` (device_key_id, prekey_bundle_json,
  published_at)
- Manager writes prekey bundles as part of team DB commits
- Each published bundle should contain multiple one-time prekeys, not just one
- One-time prekeys are single-use from the receiver's local perspective, but
  concurrent initiators may still consume the same published prekey from stale
  team DB snapshots
- When one-time prekeys are exhausted, redistribution falls back to
  signed-prekey-only X3DH until a refreshed bundle is published
- Prekey bundles are refreshed on rotation or on later team DB commits after
  local consumption

### 6. Schema changes

- Bump `LOCAL_SCHEMA_VERSION` with migration
- Add team-scoped device-local storage for redistribution prekey private
  material:
  - one persistent per-team X3DH identity and current signed prekey used for
    published redistribution bundles
  - one or more consumable one-time prekeys tracked separately from bootstrap
    sessions
  - no bootstrap-id-scoped ratchet/session table for steady-state
    redistribution
- Bump team DB schema version
- Add `device_prekey_bundle` table to team DB schema

### 7. Spec updates

- Update `packages/small-sea-manager/spec.md` to replace "Key rotation
  mechanics are TBD" with the actual rotation trigger policy

### 8. Micro tests

Minimum expected coverage:

- **Rotation round-trip**: Device A rotates, distributes to Device B; Device B
  can decrypt future bundles from A using the new key; Device B cannot use the
  old key for new messages
- **Remove member triggers rotation**: after removing a member, the removing
  device's sender key has changed, captured removed-device ids are purged from
  `peer_sender_key`, and redistribution excludes those removed devices
- **Self-removal guard**: `remove_member(team_name, self_in_team)` is rejected
- **Authorization**: a caller without `read-write` permission on the team's
  Core berth cannot remove a member
- **Cross-member redistribution**: Device A (Alice) distributes its sender key
  to Device C (Bob's new device) via X3DH when explicitly asked to redistribute
  to that device
- **Prekey bundle round-trip**: Device publishes a bundle with multiple
  one-time prekeys; a peer device can consume one for X3DH, and redistribution
  still works via signed-prekey-only fallback when one-time prekeys are
  exhausted
- **Rejection**: redistribution payload with invalid signature is rejected
- **Historical boundary**: after rotation, a device that only has the old
  sender key cannot decrypt messages encrypted with the new key
- **Missing prekey behavior**: redistribution to a peer device without a
  published prekey bundle is skipped or surfaced as pending without breaking
  the whole round
- **Removal cert-selection rule**: removing a member loads certs in Python,
  filters by `claims.member_id`, and deletes by `cert_id`, so subject-side
  membership and `device_link` history no longer makes that member resolve as
  trusted
- **Migration integrity**: local schema migration preserves existing sender-key
  runtime while adding any new fields required by this branch

## Out Of Scope

- Hub-mediated async transport for distribution payloads (manual/CLI exchange
  is acceptable for this branch; later delivery orchestration belongs with
  linked-device runtime follow-up work under #59)
- Persistent pairwise Double Ratchet sessions (ephemeral per redistribution)
- Device-aware peer routing or watch behavior (#59)
- Automatic cross-member redistribution triggered by team DB pull/watch
  discovery (#59)
- NoteToSelf sync and team discovery (#48)
- Upgrading invitation-flow sender keys to encrypted channels
- Periodic sender-key rotation policy, thresholds, and Hub signaling (#73)
- Device revocation and revocation-cert design/implementation (follow-up issue)
- Device revocation UI/UX

## Concrete Change Areas

### 1. `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `remove_member()` implementation
- `rotate_team_sender_key()`
- `redistribute_sender_key()`
- `receive_sender_key_distribution()`
- Prekey bundle publication helpers

### 2. `packages/small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`
- Add any local migration support needed by redistribution state changes in
  this branch

### 3. `packages/small-sea-note-to-self/small_sea_note_to_self/sender_keys.py`
- Update save/load for any redistribution-related local state changes

### 4. `packages/small-sea-hub/small_sea_hub/crypto.py`
- No production transport or rotation-signaling changes are required for this
  branch if manual/test exchange remains sufficient

### 5. Team DB schema
- Add `device_prekey_bundle` table

### 6. `packages/small-sea-manager/spec.md`
- Replace "TBD" rotation language with concrete policy

### 7. Test files
- New `test_sender_key_rotation.py` in Manager tests

## Validation

This branch should convince a skeptical reviewer if:

- a device can rotate its sender key and all peer devices can decrypt future
  traffic using the new key
- removing a member triggers rotation and the removed member's devices cannot
  decrypt post-removal traffic
- self-removal is rejected and Core-berth write permission is enforced before
  membership mutation
- a newly linked device from a different member receives sender-key
  distributions from all active sender devices once redistribution is invoked
- redistribution uses X3DH + Double Ratchet, not cleartext payloads
- prekey bundles are published as public material in the team DB, not synced
  as private state
- the branch is honest about transport boundaries: production internet traffic
  still goes through the Hub, but this implementation slice may stop at payload
  creation/receipt plus explicit test exchange
- micro tests are local-only, no internet services required

## Decisions Confirmed

- **Pairwise sessions**: ephemeral X3DH per redistribution round. No persistent
  Double Ratchet sessions. Persistent sessions can be added later as optimization.
- **Prekey publication**: team DB table (`device_prekey_bundle`). Public material,
  Manager-owned writes, available to all devices on pull.
- **`remove_member`**: implement in this branch as the primary rotation trigger.
- **Device revocation**: deferred until the repo has an honest revocation-cert
  path; this branch should not fake it with local-only shortcuts.
- **Cross-member redistribution**: the crypto primitive is in scope; automatic
  trigger on pull/watch discovery is deferred to #59.

## Remaining Open Questions

### Q1. Prekey bundle refresh after consumption

Because the team DB is eventually consistent, one-time prekey consumption is
not globally atomic: two initiators can race on the same published bundle from
different snapshots. The branch should therefore assume:

- each published bundle carries multiple one-time prekeys
- local receiver state consumes an OTP at most once
- concurrent double-consumption can still happen across devices pulling stale
  snapshots
- redistribution must still work with signed-prekey-only X3DH when OTPs are
  exhausted

The remaining tuning question is how many OTPs to publish per bundle and when
to refresh them on later team DB commits.

### Q2. Concurrent rotation ordering

Each device rotates independently. The removing device rotates its own key and
distributes. Other devices learn about the removal on their next team DB pull
and rotate + redistribute then. No central coordinator needed beyond the team
DB as the source of truth for membership state.

### Q3. Revocation certs (deferred)

`remove_member` in this branch deletes the member's subject-side and issuer-side
team-DB state. This is sufficient for rotation/redistribution mechanics: peer
enumeration no longer resolves that member's devices as trusted, and local
receiver state is purged using the captured device-id set from before mutation.

However, deletion alone does not give peers a cryptographic reason to believe
the removal was authorized — they only observe absence. A proper revocation
cert (a signed certificate that explicitly invalidates a membership or
device_link cert) would let any device verify the removal independently. Git
history is not a substitute for this; it should not be relied on as an
indefinite audit log.

This branch should open a new GitHub issue for revocation cert design as a
tracker action, but should not implement revocation certs itself.
