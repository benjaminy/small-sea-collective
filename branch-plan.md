# Branch Plan: Member Transport Configuration (B7)

**Branch:** `issue-102-member-transport-configuration`
**Base:** `main`
**Primary issue:** #102 "member transport configuration"
**Kind:** Implementation branch. Code + micro tests.
**Related issues:** #97 (accepted trust-domain reframe), #100 (spec/doc sweep, B1), #99 (admission-event visibility, B2)
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md`
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`
**Related code of interest:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`, `packages/small-sea-manager/small_sea_manager/web.py`, `packages/small-sea-manager/small_sea_manager/manager.py`

## Purpose

Allow a member to announce or update their own incoming cloud endpoint independently of admission. This is the B7 capability from the issue-97 meta-plan.

Transport metadata is intentionally absent from the immutable admission transcript (the transcript binds device keys and the allocated `member_id`, not cloud endpoint details). That means a freshly admitted member has no registered endpoint until they run this flow; and any existing member who changes cloud providers has no path to update peers' routing tables. This branch provides that path.

The two use cases are:
1. **Post-admission setup:** A newly admitted invitee finishes the B5 flow cryptographically admitted but transport-inert. They use this flow to stand up their incoming cloud endpoint and announce it to the team.
2. **Provider change:** An existing member switches cloud providers or recovers a lost account and needs to push a new endpoint to all peers.

Both cases use the same signed announce-endpoint mutation.

## Scope (Intentionally Narrow)

**In scope (from issue #102):**
- Signed `announce_endpoint` mutation written into team DB
- Signature verification rule: the mutation must be signed by one of the announcing member's currently-linked device keys
- Outgoing-routing update on the receiving side when peers sync a new announcement

**Explicitly out of scope:**
- Cloud-provider onboarding UX
- Billing or account-recovery flows
- Migration of in-flight messages from an old endpoint
- Per-device vs. per-member endpoint semantics beyond what the mutation naturally provides

## Current State

The existing `team_device` table has `protocol`, `url`, `bucket` columns set at admission/bootstrap time. There is currently no mechanism for a member to update these post-admission via a signed mutation. Peers' outgoing routing is therefore fixed to whatever was written at admission, with no update path short of re-admission.

The `key_certificate` table and `issue_device_link_cert` / `verify_device_link_cert` infrastructure already provide the member→device mapping needed to verify that a given signer is an authorized device for the announcing member.

## Design Direction

### Mutation shape: a new `endpoint_announcement` table

Rather than in-place updates to `team_device`, introduce a new `endpoint_announcement` table in the synced team DB. Each row is an immutable, signed record of one endpoint publication:

```
endpoint_announcement (
    announcement_id  BLOB PRIMARY KEY,   -- UUIDv7
    device_key_id    BLOB NOT NULL,       -- the device whose endpoint is being set
    protocol         TEXT NOT NULL,
    url              TEXT NOT NULL,
    bucket           TEXT NOT NULL,
    announced_at     TEXT NOT NULL,       -- ISO-8601 UTC
    signer_key_id    BLOB NOT NULL,       -- device key that signed this row
    signature        BLOB NOT NULL        -- sig over canonical payload (below)
    FOREIGN KEY (device_key_id) REFERENCES team_device(device_key_id)
)
```

The signed payload covers: `announcement_id`, `device_key_id`, `protocol`, `url`, `bucket`, `announced_at`. The `signer_key_id` must be a device key currently linked to the same member as `device_key_id` (verified via `key_certificate` chain).

Rationale for a separate table rather than mutating `team_device`:
- The team DB is a synced git repo; signed, append-only rows are simpler to reason about than in-place updates.
- Multiple announcements (e.g. during a provider transition) can coexist; the latest by `announced_at` wins for routing.
- The `team_device` rows continue to reflect what was written at admission and need not be touched.

### Verification rule

When a peer syncs a new `endpoint_announcement` row, it verifies:
1. `signer_key_id` appears in a valid `device_link` cert in `key_certificate` linking it to a `member_id`.
2. `device_key_id` is also linked (via `key_certificate`) to the same `member_id`.
3. The `signature` over the canonical payload is valid under `signer_key_id`'s public key.
4. `announced_at` is not in the future (within a small clock-skew tolerance).

Announcements that fail verification are silently dropped; they do not corrupt the peer's routing table.

### Outgoing-routing update

Peers derive the current endpoint for a given device by taking the latest valid `endpoint_announcement` row for that `device_key_id` (by `announced_at`). If no announcement exists, fall back to whatever `protocol/url/bucket` was written at admission into `team_device` (preserving backward compatibility).

The Manager function that looks up a peer's outgoing cloud coordinates should become a small helper that applies this "latest announcement wins, fall back to team_device" rule, rather than querying `team_device` directly. This centralizes the routing derivation in one place.

### Manager-side flow for self-announcement

A member announcing their own endpoint will:
1. Gather `protocol`, `url`, `bucket` from their own cloud configuration.
2. Construct the canonical payload.
3. Sign it with their current device signing key.
4. Write the row to their own copy of the team DB.
5. On next sync, peers receive the new row and update their routing.

The Manager UI should expose a way to trigger this for the post-admission case and for provider-change cases. The exact UX is lightweight for now: a form or a button in the team member detail view that invokes the announce-endpoint path. No billing/provisioning UX required.

## Expected Change Areas

### DB schema

- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
  - Add `endpoint_announcement` table.

### Provisioning / data model

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
  - `announce_endpoint(...)`: builds canonical payload, signs, writes row to team DB.
  - `verify_endpoint_announcement(...)`: verifies a synced row against the member→device mapping.
  - Routing-lookup helper: "latest valid announcement or fall back to team_device."
  - Wire verification into the sync path so incoming announcement rows are checked on receipt.

### Web / Manager

- `packages/small-sea-manager/small_sea_manager/web.py`
  - Handler for the announce-endpoint action.
- `packages/small-sea-manager/small_sea_manager/manager.py`
  - Use the routing-lookup helper wherever outgoing cloud coordinates are currently read directly from `team_device`.
- Templates (likely `templates/fragments/members.html` or a new fragment)
  - UI affordance to trigger self-announcement.

### Docs

- `packages/small-sea-manager/spec.md`
  - Add a section describing the announce-endpoint mutation shape, verification rule, and routing derivation.
- Update the B7 reference in `Architecture.md` if that doc was updated in B1/issue-100.

## Implementation Approach

### Phase 1: Schema and core mutation

Add the `endpoint_announcement` table. Implement `announce_endpoint(...)` (write path) and `verify_endpoint_announcement(...)` (read/verify path). Write micro tests against a minimal in-memory DB showing:
- A valid announcement can be written and read back.
- A tampered signature is rejected.
- A signer not linked to the device's member is rejected.

### Phase 2: Routing-lookup helper and sync integration

Implement the routing-lookup helper ("latest announcement wins, fall back to team_device"). Wire `verify_endpoint_announcement(...)` into the sync path so newly received rows are validated before being used for routing. Confirm backward compatibility: existing teams with no announcement rows still route correctly via the `team_device` fallback.

### Phase 3: Manager UI and web handler

Expose announce-endpoint in the Manager UI. The minimal surface is a form in the team member view that collects protocol/url/bucket and fires the announcement. Wire through the web handler to `announce_endpoint(...)`. Confirm the UI shows the current effective endpoint (from announcement or fallback) for each peer.

### Phase 4: Docs and micro tests

Update `spec.md`. Add end-to-end micro tests covering:
- Member announces endpoint → peer syncs → peer's routing lookup returns the new coordinates.
- Member announces a second time (provider change) → peer routing updates to the newer announcement.
- Invalid announcement (bad signature, wrong signer member) does not corrupt peer routing.
- Backward-compatible path: peer with no announcement rows routes via `team_device`.

## Provisional Decisions

1. **Append-only table, not in-place update.** Rationale above. Revisit only if the append history creates operational pain (e.g. unbounded growth); that is not a concern at pre-alpha scale.
2. **Latest-by-`announced_at` wins.** A monotonic sequence ID (UUIDv7 for `announcement_id`) is an alternative tiebreaker but `announced_at` is human-readable and sufficient for now.
3. **Silent drop for invalid announcements.** Noisy errors on sync would surface configuration mistakes as crashes; dropped rows are safely ignorable and the routing fallback keeps things working.
4. **Signer must be same-member, not necessarily same-device.** A member with multiple devices should be able to announce an endpoint for device A by signing with device B, provided both are currently linked to the same member. This matches the meta-plan's "signed by one of their own currently-linked device keys."
5. **No revocation record for old announcements.** Old rows are simply superseded by newer ones. If a member wants to "unset" an endpoint they announce a tombstone or rely on the `team_device` fallback — exact tombstone design is deferred unless it surfaces as a need during implementation.

## Validation

Done when a skeptical reviewer can verify all groups below.

### Goal: a member can announce and update their endpoint

1. A freshly admitted member (no endpoint row in `team_device`) can call `announce_endpoint(...)`, producing a row that peers can read and verify.
2. An existing member can call `announce_endpoint(...)` a second time with new coordinates, and after sync, peers' routing reflects the newer announcement.
3. The Manager UI exposes a working announce-endpoint affordance in the team member view.
4. The UI shows the current effective endpoint (latest announcement, or admission-time fallback) for each peer.

### Goal: verification is correctly enforced

5. An announcement signed by a key not linked (via `key_certificate`) to the same member as `device_key_id` is rejected.
6. A tampered payload (signature does not match) is rejected.
7. A rejected announcement does not modify the peer's routing table.

### Goal: backward compatibility

8. Teams with no `endpoint_announcement` rows route correctly via the `team_device` fallback.
9. No existing provisioning or invitation flow breaks.

### Goal: repo integrity

10. Micro tests cover all four cases from Phase 4.
11. The routing-lookup logic is in one place, not scattered.
12. No non-Manager package begins reading `team_device` or `endpoint_announcement` directly.

## Out Of Scope

- Cloud-provider onboarding or provisioning UX.
- Billing or account-recovery integration.
- Migration of messages in-flight to a retiring endpoint.
- Multi-endpoint routing (one device, multiple provider fallbacks).
- Endpoint revocation / tombstone design unless it surfaces during implementation.
- The B5 invitation-flow rework that depends on this capability; B5 remains a separate branch.

## Wrap-Up Notes

When this branch is complete:

1. Update this plan with what actually landed and any deltas from the initial approach.
2. Archive it as `Archive/branch-plan-issue-102-member-transport-configuration.md`.
3. Note any B5 dependencies or gaps that remain so the B5 branch plan can pick them up cleanly.
