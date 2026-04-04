> Migrated to GitHub issue #24.

---
id: 0002
title: Complete invitation acceptance round-trip
type: task
priority: high
status: closed
---

## Context

The invitation flow is half-built. Alice can send an invitation, but the full round-trip — Bob accepting, his repo being set up, Alice's bucket registration updating — is not yet implemented. This is a core user-facing flow and a blocker for any real multi-user scenario.

## Resolution

The spec work on small-sea-manager clarified the architecture: the full round-trip
lives entirely at the provisioning layer (direct DB and filesystem ops, no Hub
involvement in invitation data). Delivery is intentionally out-of-band (token
paste/QR code). The open questions in the issue are now answered:

- **Delivery back to Alice:** Alice calls `complete_invitation_acceptance` with
  the acceptance token Bob returns. Out-of-band exchange, same as the invitation
  token. No automated delivery mechanism is planned.
- **Bob's local repo setup:** `accept_invitation` clones Alice's team repo via
  Cod Sync, inserts Bob as a member, adds Alice as a peer, installs the merge
  driver, commits, and pushes to Bob's cloud.
- **Inviter bucket registration:** The acceptance token carries Bob's cloud info.
  `complete_invitation_acceptance` writes a `peer` row for Bob in Alice's team DB.
- **Repo divergence:** Handled naturally — Bob clones Alice's current cloud state
  (including post-invitation commits) and builds on top of it.
- **Delivery mechanism:** Out-of-band token exchange; no polling or push
  notifications needed for the invitation flow itself.

All four provisioning functions (`create_invitation`, `accept_invitation`,
`complete_invitation_acceptance`, `revoke_invitation`) are now implemented and
wired into `TeamManager` and the CLI. The 4-test suite covers the full flow
including double-accept rejection.

## What was added

- `provisioning.get_cloud_storage()` — reads primary cloud config from NoteToSelf DB
- `provisioning.revoke_invitation()` — sets invitation status to 'revoked', commits
- `TeamManager.create_invitation()`, `.accept_invitation()`, `.complete_invitation_acceptance()`, `.revoke_invitation()`
- CLI commands: `invite` (prints token), `accept` (prints acceptance token), `complete-acceptance`, `revoke`

## Remaining known limitation

The `accept` CLI and `TeamManager.accept_invitation` construct `S3Remote` directly
from the raw credentials in the token. This is intentional for now (the token
intentionally carries raw credentials per the known security issue). The Hub-mediated
path will be addressed when the credential/token security issue is fixed (see 0015).
