# Branch Plan

## Goal

Follow up on GitHub issues `#4` and `#14` by reaching a first demo-grade
crypto milestone:

- applications can open a normal team session through the Hub
- data in that session is actually encrypted/decrypted with `cuttlefish`
- the Manager persists enough key material that this is a real feature, not a
  toy in-memory demo

This branch is about establishing one real encrypted app path end to end. It
is not about finishing the full Small Sea identity story.

## Concrete Milestone

At the end of this branch, a normal team-app berth should support real
sender-key encryption through the Hub.

Concretely:

- the Manager provisions and stores the sender-key state needed for a team
- the Hub has an explicit encrypted vs passthrough session mode
- the Hub uses `cuttlefish.group` for encrypted team-app traffic
- an integration test proves that one participant can publish encrypted team
  data and another can pull and decrypt it

## Foundation Principle

This branch should introduce only the seams we are likely to keep later:

- durable sender-key storage
- a clean Hub session-mode boundary
- a working encrypted app path

This branch should avoid inventing extra crypto infrastructure that is likely
to be deleted once pairwise channels and `wrasse-trust` are integrated.

## Scope For This Branch

Implement now:

- encrypted **team-app berth** sessions
- persisted sender-key state for "my sender key" and "peer sender keys"
- invitation-time bootstrap of those sender keys
- a minimal Hub crypto adapter around `cuttlefish.group`

Defer for later:

- full X3DH / Double Ratchet integration with Manager workflows
- encrypted sender-key rotation over cloud channels
- full `wrasse-trust` web-of-trust integration
- multi-device key transfer and revocation flows
- post-quantum variants
- notification encryption

## Resolved Design Decisions

- **What gets encrypted in this branch**: normal team-app berth payloads that
  flow through the Hub. NoteToSelf remains passthrough. Notifications are left
  plaintext in this branch. Future pairwise/key-management channels are out of
  scope here.
- **Group granularity**: one sender-key group per team. The team ID is used as
  the `group_id`. Apps sharing a team share the same sender-key infrastructure.
- **Sender-key storage**: the minimal sender-key state for this branch lives in
  the participant's own NoteToSelf `core.db`.
  - own sender key: one row per team
  - peer sender key: one row per peer per team
  - this is a branch-level storage choice for the first encrypted path, not a
    final multi-device architecture decision
- **Hub key access**: the Hub reads sender-key state directly from NoteToSelf
  SQLite, because the Manager ↔ Hub contract is already "Manager writes DBs,
  Hub reads DBs."
- **Session modes**: the Hub should gain an explicit concept of encrypted vs
  passthrough handling now, even if the initial rule is simple.
  - NoteToSelf berth: passthrough
  - normal team-app berth: encrypted
- **Bootstrap**: initial sender-key distribution happens directly in the
  invitation / acceptance tokens. This branch does **not** add separate static
  X25519 member-encryption infrastructure yet.
- **Existing bundle signing**: keep the current `team_signing_key` /
  signed-bundle behavior in place for now. This branch adds encrypted berth
  payloads; it does not redesign the whole signing story at the same time.

## Implementation Steps

### 1. Minimal Schema Changes

In NoteToSelf `core.db` (`core_note_to_self_schema.sql`), add:

- `team_sender_key`
  Stores the full `cuttlefish.group.SenderKeyRecord` shape for my own sender
  key for one team. The natural key is `team_id`.
- `peer_sender_key`
  Stores the full `cuttlefish.group.SenderKeyRecord` shape for one peer's
  sender key for one team, including skipped-message state.

Do **not** add team-wide sender-key secret storage to the team DB.
Do **not** add static X25519 encryption-key tables in this branch.
Bump the NoteToSelf schema version accordingly.

### 2. Provisioning and Invitation Bootstrap

In `provisioning.py`:

- `create_team`
  Generate an initial sender key for the creator and store it in
  `team_sender_key`.
- `accept_invitation`
  Read the inviter's sender-key distribution from the invitation token and
  store it as a `peer_sender_key`, then generate the acceptor's own sender key.
- `complete_invitation_acceptance`
  Read the acceptor's sender-key distribution from the acceptance token and
  store it as a `peer_sender_key`.

This gives both participants each other's sender keys immediately after the
out-of-band invitation flow, without pretending that we have pairwise ratchets
yet.

### 3. Hub Session Mode

Teach the Hub session layer to distinguish encrypted vs passthrough sessions.

For this branch:

- NoteToSelf berth sessions are passthrough
- normal team-app berth sessions are encrypted

Even if the initial routing rule is simple, the Hub should make this a real
session-mode concept now so future pairwise channels have a clean home.

### 4. Hub Crypto Adapter

Add a small Hub-side adapter around `cuttlefish.group`:

- choose one concrete `GroupMessage` serialization format and keep it local to
  the Hub crypto seam; it does not need to be human-readable

- on upload for encrypted sessions:
  - read my `team_sender_key`
  - encrypt plaintext into a serialized `GroupMessage`
  - persist the advanced sender-key state
- on download for encrypted sessions:
  - deserialize the `GroupMessage`
  - read the matching `peer_sender_key`
  - decrypt to plaintext
  - persist the advanced peer state

NoteToSelf sessions bypass this logic.

The expected integration points in `backend.py` are:

- `upload_to_cloud`
- `download_from_cloud`
- `download_from_peer`

### 5. End-to-End Proof

Use one normal app berth as the guinea pig, ideally Shared File Vault.

The test should prove:

1. Alice and Bob complete the invitation flow and exchange initial sender keys.
2. Alice writes berth data through the Hub.
3. The raw cloud bytes are encrypted, not plaintext.
4. Bob pulls through the Hub and gets the original plaintext back.
5. The test would fail if Hub encryption/decryption were bypassed.

### 6. Create Follow-Up Issues

At the end of this branch, file GitHub issues for the deferred work that this
milestone makes concrete:

- encrypted sender-key rotation over cloud channels
- full X3DH / Double Ratchet integration with Manager workflows
- full `wrasse-trust` web-of-trust integration
- multi-device key transfer and revocation flows
- post-quantum variants
- notification encryption
- revisit sender-key storage location once multi-device story is clearer

Also file issues for anything that comes up during implementation that we
intentionally punt on.

## Validation

The branch is successful if all of the following are true:

- `cuttlefish` micro tests still pass
- existing signed-bundle tests still pass unchanged, or only change for
  clearly justified schema plumbing
- invitation tests cover sender-key bootstrap in the tokens
- a new integration test demonstrates encrypted Hub roundtrip for a normal
  team-app berth workflow
- that integration test proves the cloud payload is ciphertext, not plaintext

## Risks To Watch

- storing peer sender-key state in NoteToSelf may turn out to be the wrong
  long-term multi-device choice; treat it as a minimal milestone decision, not
  settled architecture
- if the Hub session-mode seam is hacked in instead of added cleanly now,
  future pairwise channels will require repainting this work
- this branch should not expand into redesigning bundle signing, trust policy,
  or pairwise ratchet transport
