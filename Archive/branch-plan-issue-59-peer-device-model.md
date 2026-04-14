# Branch Plan: Collapse Peer Into Member and Add Team Device Model

**Branch:** `issue-59-peer-device-model`  
**Base:** `main`  
**Status:** Implemented and ready to archive  
**Primary issue:** #59 "Make linked devices first-class for sender keys and peer routing"  
**Related issues:** #69, #43, #48, #73  
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`,
`packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-issue-59-peer-routing-watches.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`,
`Archive/branch-plan-issue-43-sender-key-rotation.md`

## Outcome

This branch settled the shared team schema around one member-facing model and
one device-facing model.

What landed:

- fresh team DBs now keep member-facing display metadata on `member` and
  device-facing runtime identity plus endpoint metadata on `team_device`
- `peer` is gone from fresh team DBs
- `member.device_public_key` is no longer the shared home for device identity
  in fresh team DBs
- `team_device.device_key_id` is now the shared owner for device identity, and
  `device_prekey_bundle.device_key_id` references it
- create-team, invitation acceptance/completion, linked-device bootstrap, and
  member removal all write or clean up `team_device` rows directly
- Hub read paths now list peers from `member` and resolve member-keyed endpoint
  lookups through `team_device`
- Manager and Hub specs were updated to describe the new shared model

Extra hardening also landed before wrap-up:

- SQLite foreign-key enforcement is now enabled on Manager SQLAlchemy
  connections, so the schema-level `ON DELETE CASCADE` behavior is real rather
  than aspirational
- the sync roundtrip fixture was updated to use the new `member` plus
  `team_device` model instead of hand-writing `peer`
- micro tests now assert both cascade cleanup on member removal and the schema
  shape for same-member linked devices

## Context

Earlier issue-59 runtime branches made sender-key identity and runtime
reconciliation device-aware, but the shared team DB still encoded too much
one-device-era structure:

1. `member` and `peer` split member-facing and device-facing concerns
   awkwardly.
2. runtime logic increasingly needed device-scoped endpoints and device-owned
   prekey bundles.
3. linked-device support made the old "one member, one endpoint" shape harder
   to defend every branch.

This branch resolved that schema ambiguity so later routing and runtime work can
build on a clearer shared model instead of layering more exceptions onto
`peer`.

## Implemented Change Areas

### 1. Shared team DB schema

The shared schema now targets this shape for fresh DBs:

- `member` carries member-facing fields including `display_name`
- `team_device` carries:
  - `device_key_id` as primary key
  - `member_id`
  - `public_key`
  - endpoint fields `protocol`, `url`, `bucket`
  - `created_at`
- `team_device.member_id -> member.id` uses `ON DELETE CASCADE`
- `device_prekey_bundle.device_key_id -> team_device.device_key_id` uses
  `ON DELETE CASCADE`
- invitation acceptance state includes `acceptor_device_key_id`
- `device_key_id` is computed from device public key using the repo's existing
  key-id convention

### 2. Manager write paths

Manager now maintains the shared member/device split in the flows that already
know device ownership:

- create-team creates the founding `member` row and the founding `team_device`
  row
- invitation acceptance/completion records `acceptor_device_key_id` and writes
  the new member plus first device row directly
- linked-device bootstrap adds a second `team_device` row for the same member
- member removal deletes the member and relies on enforced FK cascades to clean
  `team_device` and `device_prekey_bundle` rows, while trust removal still
  comes from the existing membership/device-link flow rather than endpoint-row
  deletion

### 3. Hub read seams

Hub reads were updated to match the new model without over-solving later
device-keyed APIs:

- peer listing now comes from `member`, excluding `self_in_team`
- member-keyed endpoint lookups now resolve through `team_device`
- the interim deterministic rule is:
  - readable means `url IS NOT NULL`
  - choose the readable row with the lowest `created_at`
  - tie-break on `device_key_id`

### 4. Specs and micro tests

The branch updated the Manager and Hub specs and added skeptical micro-test
coverage for the new shape and cleanup behavior.

## Validation Evidence

This branch should convince a skeptical reviewer because the following are now
true for fresh DBs and exercised by the branch-relevant micro tests:

- `peer` is no longer part of the fresh shared team DB schema
- `member` contains `display_name`
- `team_device.device_key_id` is the device-identity primary key
- `device_prekey_bundle.device_key_id` references `team_device.device_key_id`
- removing a member removes that member's `team_device` rows and the linked
  `device_prekey_bundle` rows
- linked-device bootstrap adds a second `team_device` row without creating a
  second `member` row
- invitation acceptance writes endpoint-shaped invitation data into
  `team_device`
- Hub peer reads now resolve endpoint metadata through `team_device` instead of
  `peer`
- the repo's trust model still comes from cert history rather than from
  endpoint rows

Verification run during implementation:

```sh
uv run pytest \
  packages/small-sea-manager/tests/test_create_team.py \
  packages/small-sea-manager/tests/test_invitation.py \
  packages/small-sea-manager/tests/test_sender_key_rotation.py \
  packages/small-sea-manager/tests/test_hub_invitation_flow.py \
  packages/small-sea-manager/tests/test_signed_bundles.py \
  packages/small-sea-manager/tests/test_linked_device_bootstrap.py \
  packages/small-sea-manager/tests/test_device_link.py \
  packages/small-sea-hub/tests/test_session_flow.py \
  packages/small-sea-hub/tests/test_runtime_watch.py \
  packages/small-sea-hub/tests/test_notifications.py \
  tests/test_sync_roundtrip.py -q
```

Result: `44 passed`, `1 warning`

Additional hardening checks:

```sh
uv run pytest \
  packages/small-sea-manager/tests/test_sender_key_rotation.py::test_remove_member_purges_local_receiver_state_and_subject_side_certs \
  packages/small-sea-manager/tests/test_device_link.py::test_issue_device_link_for_member_updates_trusted_device_lookup \
  tests/test_sync_roundtrip.py -q
```

Result: `3 passed`

## Intentional Limits

The branch intentionally stopped short of a few follow-ups:

- pre-alpha migration cleanup remains partial and low-priority; fresh DBs get
  the clean model, while older DBs are only backfilled enough for current code
  paths
- Hub caller APIs remain member-keyed even though endpoint resolution now goes
  through `team_device`
- `member.identity_public_key` still has no writer and remains separate cleanup
  work
- this branch does not redesign sender-key crypto, revocation semantics, or
  periodic rotation policy
