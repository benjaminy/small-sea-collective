# Branch Plan: Member Transport Configuration (B7)

**Branch:** `issue-102-member-transport-configuration`
**Base:** `main`
**Primary issue:** #102 "member transport configuration"
**Kind:** Implementation branch. Code + micro tests.
**Related issues:** #97 (accepted trust-domain reframe), #100 (spec/doc sweep, B1), #99 (admission-event visibility, B2)
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md`
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`
**Related code of interest:** `packages/small-sea-manager/small_sea_manager/provisioning.py`, `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`, `packages/small-sea-manager/small_sea_manager/manager.py`, `packages/small-sea-manager/small_sea_manager/web.py`, `packages/small-sea-hub/small_sea_hub/backend.py`

## Purpose

Implement the B7 capability from the issue-97 meta-plan: a team member can publish or update their own incoming transport coordinates after admission, and peers will use the latest valid publication when routing outgoing sync traffic to that member.

This capability is needed for two concrete cases:

1. A newly admitted member finishes the current admission flow with a valid membership/device identity but later needs to publish their real incoming endpoint.
2. An existing member changes providers, rotates buckets, or recovers from cloud-account loss and needs teammates to route to the new endpoint.

The branch should land a clean, explicit model for this transport update flow rather than deepening the accidental coupling between admission-time `team_device` rows and current peer-routing behavior.

## Branch Goals

When this branch is done, the repo should have all of the following properties:

1. A member can publish a signed transport announcement after admission without re-admission.
2. A later signed publication from the same member supersedes the earlier one for routing.
3. Routing decisions are derived in one place from "latest valid member transport announcement by `announcement_id`, otherwise temporary legacy fallback."
4. Manager and Hub use the same effective-transport rule.
5. Invalid announcements do not affect routing.
6. The implementation reduces accidental coupling by separating stable device identity (`team_device`) from mutable member transport state.

## Scope

**In scope:**

- New signed, append-only team-DB record for member transport announcements
- Verification rule: signer must be one of the announcing member's currently trusted device keys
- Effective-transport derivation helper used by both Manager and Hub
- Minimal Manager UI/web flow for self-announcement
- Spec/doc updates
- Micro tests that prove routing changes and invalid-announcement safety

**Out of scope:**

- Cloud-provider onboarding UX
- Billing, recovery workflows, or token management
- Multi-endpoint load balancing or provider failover
- Automatic migration of in-flight messages from an old endpoint
- Invitation-flow rework (B5)
- Revocation/tombstone semantics unless implementation proves they are immediately necessary

## Current State

Today the synced team DB stores transport-like fields on `team_device`, and those fields are populated at admission/bootstrap time. The relevant evidence is:

- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql` defines `team_device(protocol, url, bucket, ...)`
- `packages/small-sea-manager/spec.md` describes `team_device` as carrying cloud endpoint metadata used for Cod Sync pull
- invitation micro tests such as `packages/small-sea-manager/tests/test_invitation.py` assert that newly admitted members arrive with `protocol` and `url` already written onto `team_device`
- `packages/small-sea-hub/small_sea_hub/backend.py` resolves a peer endpoint by querying `team_device` by `member_id` and picking one row

In other words:

- the schema stores transport on device rows
- the runtime already behaves as if transport is member-scoped
- there is no explicit post-admission signed publication flow

That is exactly the kind of accidental model drift B7 should clean up.

## Design Decision

### 1. Transport is member-scoped state, published by a trusted device

The announcing subject is a `member_id`, not a `device_key_id`.

The signer is still device-scoped, because signatures are made by concrete device keys. But the payload being asserted is "member M wants teammates to route to endpoint E," not "device D has endpoint E."

That matches the B7 meta-plan wording, the current Hub lookup behavior, and the real use cases (provider switch, account recovery, post-admission setup).

### 2. Use a new append-only `member_transport_announcement` table

Do not mutate `team_device` in place. Do not add more mutable semantics to those columns.

Introduce an immutable signed table in the team DB:

```sql
member_transport_announcement (
    announcement_id BLOB PRIMARY KEY,   -- UUIDv7
    member_id       BLOB NOT NULL,
    protocol        TEXT NOT NULL,
    url             TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    announced_at    TEXT NOT NULL,      -- ISO-8601 UTC
    signer_key_id   BLOB NOT NULL,
    signature       BLOB NOT NULL,
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
)
```

Signed payload fields:

- `announcement_id`
- `member_id`
- `protocol`
- `url`
- `bucket`
- `announced_at`
- `signer_key_id`

The signature does **not** need to name a target device, but it **does** need to bind the claimed signer. The authorization rule is that `signer_key_id` must resolve, via `key_certificate`, to a trusted device of `member_id` under the policy below.

### 3. Ordering is by `announcement_id`, not by `announced_at`

The effective transport for a member is chosen by sorting candidate rows by `announcement_id` UUIDv7 ordering, newest first.

`announced_at` remains in the row, but only as a display/audit field. It is not the conflict-resolution authority.

This avoids the obvious "sign one row with a far-future timestamp and freeze routing forever" problem that comes from treating a client-supplied signed timestamp as the primary ordering key.

### 4. Verification is on derivation/use, not on merge

The trusted behavior is:

1. Team DB sync may bring in any syntactically valid row.
2. Effective transport for a member is derived by scanning announcements newest-first.
3. The first row whose signature and signer authorization validate is used.
4. Invalid rows remain inert data; they do not become effective routing state.

This is more realistic than pretending sync has a row-level reject hook that does not currently exist.

### 5. Signer trust is evaluated at derivation time

For this branch, "trusted signer" means trusted **at derivation time**, not trusted at original announcement time.

That means:

- if a signer device is later revoked or otherwise ceases to be trusted for `member_id`, its old transport announcements become inert
- effective routing then falls through to the next newest valid announcement, or to the temporary legacy fallback if no valid announcement remains

This is the simplest rule that matches the current codebase. It avoids introducing a heavier governance-state anchor model just for B7. The UX consequence is real and should be documented explicitly: revoking a signer device can also withdraw transport announcements that only that signer authenticated.

### 6. Shared rule means a shared module, not a Manager-owned helper

The effective-transport rule cannot live only in `small_sea_manager/provisioning.py`. Hub is a separate package and should not import Manager internals.

This branch should therefore introduce a small dependency-light shared module for transport-announcement canonicalization, verification, ordering, and selection. The best fit is a narrow module under `packages/wrasse-trust`, because the core logic is trust derivation plus signature verification over synced team-DB rows.

Concretely:

- Manager and Hub each remain responsible for loading raw SQLite rows from the local team DB
- the shared module is responsible for:
  - canonical payload construction/parsing
  - signature verification
  - signer-trust evaluation from `key_certificate` rows
  - ordering by UUIDv7 `announcement_id`
  - choosing the effective transport from candidate rows

This keeps one real implementation of the rule without introducing a Manager API dependency or duplicating logic in two packages.

### 7. Temporary legacy fallback stays only as a bridge to current main

Because current admission flow still writes `protocol/url/bucket` into `team_device`, and because B5 has not yet removed transport from the admission transcript, B7 should include a narrow transitional fallback:

- if a member has no valid `member_transport_announcement`, derive transport from existing `team_device` rows using the current behavior

This is a bridge for the current repo state, not a forever-model commitment. The spec and code comments should say that plainly.

### 8. Bucket semantics shift from derivation to lookup for peer routing

Today some code paths can derive a peer bucket algorithmically. Under B7, the effective peer bucket becomes transport metadata that must be looked up from the member's valid announcement.

That means this branch must explicitly audit peer-routing call sites and separate two cases:

- **still derivable:** local/self bucket creation and invitation/bootstrap artifacts that are intentionally tied to current pre-B5 behavior
- **no longer derivable:** runtime routing to another member's current incoming endpoint

Any peer-routing path that keeps deriving another member's bucket instead of looking it up will be wrong once announcements are live.

### 9. B5 handoff should attach to an explicit "transport not configured" hook

B7 should leave a concrete integration seam for B5 rather than a vague future dependency.

The branch should therefore add a Manager-visible state such as `transport_configured` / `needs_transport_announcement` for the local member in a team. The same team-detail surface that supports self-announcement should be able to render a "transport not yet configured" state.

That gives B5 a precise attachment point:

- after a newly admitted member observes finalization and has local cloud configuration available, B5 directs them into the existing B7 self-announcement path
- until that happens, the UI can truthfully show that team membership exists but incoming transport is not yet configured

### 10. One effective-transport helper, shared semantics everywhere

The branch should define one canonical rule for:

- Manager UI display of a member's current transport
- any manager-side peer-routing decisions
- Hub peer download routing

The anti-goal is duplicating "latest valid announcement else fallback" logic across multiple ad hoc queries.

## Expected Change Areas

### Schema

- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`
  - Add `member_transport_announcement`

### Provisioning / core logic

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
  - Manager-side write path for transport announcements
  - Member-list/query helpers extended to expose effective transport for UI/tests

### Shared trust/selection module

- `packages/wrasse-trust/...`
  - Canonical payload builder/parser for transport announcements
  - Verification helper for announcement rows
  - Effective-transport selection logic over candidate rows + cert rows
  - UUIDv7 ordering helper for announcement selection

### Manager session/business layer

- `packages/small-sea-manager/small_sea_manager/manager.py`
  - Public method for self-announcement
  - Team/member read paths updated to surface effective transport and `needs_transport_announcement` for the local member

### Web UI

- `packages/small-sea-manager/small_sea_manager/web.py`
  - POST route for self-announcement
  - Team detail context updated to show effective transport

- `packages/small-sea-manager/small_sea_manager/templates/fragments/members.html`
  - Minimal self-service form on the viewer's own member row, or immediately adjacent to it
  - Read-only display of effective transport for members

### Hub runtime

- `packages/small-sea-hub/small_sea_hub/backend.py`
  - Peer download path switched from direct `team_device` lookup to the shared effective-transport rule
  - Audit and fix any peer-routing path that still derives another member's bucket instead of looking it up

This is a must-have, not a stretch goal. Without it, B7 would update metadata that the runtime does not actually use.

### Docs

- `packages/small-sea-manager/spec.md`
  - Add B7 transport-announcement section
  - Clarify that `team_device` is device identity plus legacy bootstrap-era transport, while current mutable transport publication is member-scoped

- `architecture.md`
  - Small update if needed to keep B7 aligned with the accepted issue-97 model

### Micro tests

- `packages/small-sea-manager/tests/`
  - New focused micro tests for announcement signing/verification and effective lookup
  - Existing invitation/admission tests updated only as needed for the new helper-driven behavior

- `packages/small-sea-hub/tests/`
  - Focused micro test for peer-download routing using the new lookup rule

## Implementation Approach

### Phase 1: Nail the data model and helper boundary

Add the new table and implement three small, explicit primitives:

1. `announce_member_transport(...)`
2. `verify_member_transport_announcement(...)`
3. `get_effective_member_transport(...)`

Before finishing this phase, place verification/selection logic in the shared module rather than in Manager-only code.

Success criteria for this phase:

- we can create a signed row locally
- we can verify a good row
- we can reject a tampered or unauthorized row
- we can compute effective transport for a member without touching UI or Hub yet
- ordering is by UUIDv7 `announcement_id`, not by `announced_at`

### Phase 2: Integrate the helper into Manager and Hub reads

Update the read side first so both Manager and Hub consume the same effective-transport rule before the write UI is added.

Success criteria:

- manager-side team/member reads can surface effective transport
- manager-side team/member reads can surface `needs_transport_announcement` for the local member
- the Hub peer-download path resolves peers through the effective-transport helper rather than directly from `team_device`
- if an announcement exists, runtime routing uses it
- if no announcement exists, runtime routing still uses the temporary legacy fallback
- peer-routing code paths no longer derive another member's bucket algorithmically

### Phase 3: Add the Manager UI write path

Expose a low-complexity self-service path in the Manager UI using the same member display surface that now shows effective transport:

- enter `protocol`, `url`, `bucket`
- submit
- write announcement
- refresh detail view showing the newly effective transport

This branch does not need polished provider onboarding. It needs a trustworthy control surface for the signed publication.

Success criteria:

- the current member can publish transport from the team detail page
- the same template refresh shows the newly effective transport after submission
- the local member surface can also show "transport not yet configured," which B5 can reuse after admission finalization

### Phase 4: Documentation and validation pass

Update `spec.md`, tighten any affected comments, and finish the micro tests. If the implementation teaches us a better naming split than "announcement" vs "effective transport," fold that back into the plan before archiving it.

## Provisional Decisions

1. **Member-scoped target, device-scoped signer.** This is the cleanest match to the issue and the runtime.
2. **Append-only signed rows.** Better fit for git-synced team DBs than in-place mutation.
3. **Newest valid `announcement_id` wins.** `announcement_id` UUIDv7 is the ordering key; `announced_at` is display/audit metadata only.
4. **`signer_key_id` is part of the signed payload.** Claimed signer identity must be authenticity-protected.
5. **Signer trust is evaluated at derivation time.** Revoked-signer announcements become inert.
6. **Invalid rows are ignored, not deleted.** This matches the actual sync model.
7. **Shared rule lives in a shared module, not in Manager.** Hub and Manager both call the same transport-selection implementation.
8. **Peer bucket for current routing is looked up, not derived.** Algorithmic derivation remains only where still semantically valid.
9. **Temporary `team_device` fallback stays only until B5 removes admission-time transport coupling.**
10. **No tombstone in this branch unless implementation demands it.** A replacement announcement is enough for the stated scope.

## Risks And How This Plan Contains Them

### Risk: we accidentally deepen the wrong abstraction

If we keep hanging mutable transport state off `team_device`, later B5 cleanup gets harder. The plan avoids that by introducing a member-scoped table now.

### Risk: Manager and Hub drift again

If Manager displays one thing and Hub routes another, B7 is not really done. The plan requires one effective-transport rule and names the Hub file up front.

### Risk: verification becomes performative instead of real

If we only test "happy path row exists," we will not know whether signer authorization is actually enforced. The validation matrix below includes explicit wrong-member and tampered-signature cases.

### Risk: signer revocation changes routing unexpectedly

Because signer trust is evaluated at derivation time, revoking a signer can deactivate its transport announcement. The plan contains this by making the policy explicit in spec, tests, and validation rather than leaving it as accidental behavior.

### Risk: future-dated timestamps freeze routing

If `announced_at` were the ordering authority, one far-future timestamp could dominate forever. The plan avoids that by making UUIDv7 `announcement_id` the only ordering key.

### Risk: "shared helper" collapses into duplication

If the helper stays in Manager, Hub will either duplicate it or grow a bad dependency. The plan resolves this up front by putting verification/selection logic in a small shared module.

### Risk: bucket derivation silently misroutes peers

Once peer bucket becomes looked-up transport metadata, any remaining derivation-based peer-routing path is a latent bug. The plan contains that by requiring an explicit audit of those call sites in the implementation phase.

### Risk: fallback logic becomes permanent mud

The branch should label fallback as temporary in both plan and spec so later cleanup work has a clear target.

## Validation

Done when a skeptical reviewer can verify every item below with code, micro tests, or direct inspection.

### Goal: a member can publish and later replace transport

1. There is a concrete write path that creates a signed `member_transport_announcement` row for the current member.
2. A second announcement from the same member with a newer UUIDv7 `announcement_id` supersedes the first for effective transport.
3. The Manager UI exposes a working self-announcement action for the current member.
4. The team detail view shows each member's effective transport, not raw `team_device` columns.

### Goal: authorization and integrity are real

5. An announcement whose `signer_key_id` is not currently trusted for `member_id` is ignored by effective-transport lookup.
6. A tampered signature is ignored by effective-transport lookup.
7. A bad announcement does not change Manager-visible effective transport.
8. A bad announcement does not break Hub peer download when a valid announcement or fallback transport exists.
9. An announcement whose signer was once trusted but is no longer trusted becomes inert under the documented derivation-time policy.
10. Changing only `announced_at` does not let an older announcement outrank a newer one; ordering is driven by `announcement_id`.

### Goal: runtime actually uses the new model

11. The Hub peer-download path no longer resolves peers by directly selecting `team_device.protocol/url/bucket` as the primary source of truth.
12. A peer-download micro test demonstrates that the Hub uses the newer valid announcement when one exists.
13. Peer-routing code paths no longer compute another member's bucket algorithmically when current transport announcements are in play.

### Goal: transition from current main remains intact

14. If no valid transport announcement exists, routing still works through the temporary legacy `team_device` fallback.
15. Existing invitation/admission flows still populate enough state for the fallback path to work until B5 lands.
16. The Manager exposes a "transport not yet configured" state that B5 can reuse after finalization.

### Goal: repo integrity is improved, not just feature count

17. Effective transport is derived in one shared-module boundary rather than duplicated queries.
18. `team_device` remains device identity/trust data; the new mutable transport behavior lives in its own schema object.
19. The spec explains the UUIDv7 ordering rule, derivation-time trust rule, temporary fallback, and intended future cleanup, so the repo is easier to reason about after the branch than before it.

## Concrete Micro Tests To Expect

At minimum, the branch should land tests equivalent to these:

1. Valid self-announcement creates a row whose signature verifies.
2. Latest valid UUIDv7 `announcement_id` wins over an older valid announcement from the same member.
3. Announcement signed by a device linked to a different member is ignored.
4. Tampered payload/signature is ignored.
5. Changing only `signer_key_id` invalidates verification because the signer identity is inside the signed payload.
6. A once-valid announcement becomes inert after signer revocation under the documented derivation-time policy.
7. A far-future `announced_at` on an older `announcement_id` does not outrank a newer announcement.
8. No announcement present -> effective transport falls back to legacy `team_device`.
9. Hub peer download chooses announced transport over fallback transport.
10. Hub peer download still succeeds via fallback when only invalid announcements are present.
11. Peer-routing lookup uses announced bucket metadata rather than an algorithmically derived peer bucket.

If the implementation needs one extra helper-level micro test to prove deterministic tie-breaking, add it.

## Out Of Scope

- Provider-specific setup wizards
- Background health checks for published endpoints
- Multi-device transport specialization
- Automatic cleanup/removal of superseded announcement rows
- Transport revocation/tombstones
- B5 invitation transcript cleanup

## Wrap-Up Notes

When the branch is complete:

1. Update this plan to reflect what actually landed, especially whether fallback remained purely transitional.
2. Archive it as `Archive/branch-plan-issue-102-member-transport-configuration.md`.
3. Note any cleanup that B5 or a follow-on branch should do, especially removal of residual `team_device` transport coupling.
