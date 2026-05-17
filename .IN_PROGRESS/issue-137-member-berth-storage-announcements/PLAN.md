# Branch Plan: Member-Berth Storage Announcements (Slice B)

**Branch:** `issue-137-member-berth-storage-announcements`
**Base:** `main`
**Primary issue:** #137 "Implement member-berth storage announcements"
**Predecessors:** #134 (vocabulary), #136 (Slice A — own-storage allocation)
**Related:** #123 (S3 bucket semantics), #57.
**Kind:** Implementation branch. Schema, dataclass, canonical bytes, publish path, peer-read routing, sibling-device read path, micro tests.
**Reference docs:**
- `Archive/design-record-issue-134-berth-cloud-location-semantics.md`
- `packages/small-sea-hub/spec.md` (§Peer Storage Routing)
- `packages/small-sea-manager/spec.md` (§Member berth storage announcements — both UI and Service-Subscriptions sections)

## Purpose

Slice B of the #134 design.
Today peer reads route by member-only `member_transport_announcement` (or by
legacy `team_device(protocol, url, bucket)`).
Slice A removed name synthesis on the own-storage path but left peer reads on
the legacy formula except for the Core bootstrap exception.
This slice introduces `member_berth_storage_announcement` scoped to
`(member_id, berth_id)`, publishes it after materialization, and reroutes
peer reads through the announcement path.

## Branch Contract

When this branch is done, all of the following are true:

1. Team Core DB has a `member_berth_storage_announcement` table with the
   shape documented in the Manager spec (announcement_id PK, member_id,
   berth_id, protocol, url, location, announced_at, signer_key_id,
   signature) and an index on `(member_id, berth_id, announcement_id)` for
   the newest-valid scan.
2. `wrasse-trust` exposes a `MemberBerthStorageAnnouncement` dataclass and a
   `canonical_member_berth_storage_announcement_bytes` function modeled on
   the existing `MemberTransportAnnouncement` pair.
3. The Manager publishes an announcement signed by the publisher's current
   team-device key. Publication is dedupe-guarded: a new row is written only
   when the newest valid announcement for
   `(self_member_id, berth_id)` does not already match the durable
   allocation's `(protocol, url, location)`. Republication happens only when
   the locator changes.
4. Hub own-storage operations refuse to proceed when no valid announcement
   exists for `(self_member_id, session.berth_id)`. The Hub returns a `409`
   with `{ "error": "cloud_storage_required", "reason": "announcement_missing" }`,
   joining the Slice A `cloud_storage_required` family. This is the trigger
   that causes Manager-mediated publication to run; once the Manager
   publishes, the app retries and the storage op proceeds.
5. Peer reads in `_download_peer_file` select the newest valid announcement
   for `(member_id, session.berth_id)`, descending by `announcement_id`
   (UUIDv7), before falling back to legacy `team_device` transport.
6. Validity is structural: signature verifies under `signer_key_id` and that
   key is currently trusted for `member_id` via the team DB's
   `key_certificate` history. No max-age policy in v1.
7. Same-member sibling-device peer reads use the announcement path, not the
   local `berth_cloud_allocation`. The local allocation describes where
   *this* device writes; a sibling device may have written the same berth to
   a different location.
8. The Slice A peer-read exception in `_download_peer_file`
   (`SmallSeaCollectiveCore` + `legacy_transport.bucket`) is removed in
   favor of the announcement path.
9. Legacy `team_device(protocol, url, bucket)` fallback remains for any
   `(member_id, berth_id)` with no valid announcement. Removing the columns
   themselves stays in Slice C.
10. `member_transport_announcement` (member-only) is no longer consulted by
    peer-storage routing in this slice. The table, dataclass, and helpers
    stay in place; actual removal moves to Slice C alongside the
    `team_device` column cleanup.

## Settled Decisions

These were open at draft time and have been resolved:

- **Publication trigger:** Manager-mediated with a Hub-side gate.
  The Hub refuses own-storage operations and returns
  `cloud_storage_required / announcement_missing` (a new reason in the
  Slice A 409 family) when no valid announcement exists for the session's
  berth. The Manager publishes on prompt; the app retries; the storage op
  proceeds.
  Rationale: keeps the Hub/Manager boundary intact (the Hub does not sign
  or write team-DB rows), leverages the existing `cloud_storage_required`
  family, and incurs only a one-time-per-berth-per-device user prompt.
  Future UX shortcuts (e.g. Manager bulk-prepares known-needed berths at
  team-creation time) compose on top of this without changing the rule.

- **Coexistence with `member_transport_announcement`:** coexist this
  slice, remove in Slice C.
  Peer-storage routing stops consulting it here. The table, dataclass,
  helpers, and any unrelated callers remain in place. Slice C bundles its
  removal with `team_device(protocol, url, bucket)` column cleanup.

- **Publication idempotency:** dedupe-guarded.
  Publish only when the newest valid announcement for
  `(self_member_id, berth_id)` does not already match the durable
  allocation's `(protocol, url, location)`. Republish only when the
  locator changes. This makes `/cloud/setup` and the announcement_missing
  retry path safe to call repeatedly without spamming rows.

## Open Questions

These remain to settle during implementation, recorded back into this
plan:

- **App berth fixture publication.**
  Slice A's Vault test fixtures pre-create an allocation against the legacy
  formula bucket. With announcements, those fixtures should publish an
  announcement too. The fixture helpers in `shared-file-vault/tests/` need
  updating; magnitude looks small but worth flagging.

- **Same-member sibling-device announcement publishing.**
  Each device of the same member writes to its own allocation. After
  bootstrap, the new device's allocation is fresh
  (`ss-{uuid7_hex}` per Slice A), so it must publish its own announcement
  for that berth. Two coexisting announcements for `(member_id, berth_id)`
  are expected during sibling-write windows; the newest-valid rule resolves
  selection. This is documented in the Manager spec already, but we should
  verify the bootstrap flow actually publishes (rather than leaving the new
  device announcement-less until the user manually triggers setup again).

## Scope

### In scope

- **Schema:** `member_berth_storage_announcement` table + index in
  `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`.
  Pre-alpha: no migration shim; test fixtures get fresh DBs.
- **wrasse-trust:**
  - `MemberBerthStorageAnnouncement` dataclass in
    `packages/wrasse-trust/wrasse_trust/transport.py` (or sibling module).
  - `canonical_member_berth_storage_announcement_bytes()` using the same
    `json.dumps(..., sort_keys=True, separators=(",", ":"))` convention.
- **Manager:**
  - `publish_member_berth_storage_announcement(root_dir, participant_hex,
    team_name, member_id, berth_id, allocation_record, signer_key)` —
    runs the dedupe check, builds the announcement, signs it, writes the
    row, commits. Returns whether a row was written (False = no-op
    because newest valid announcement already matches the allocation).
  - `load_member_berth_storage_announcements(conn, member_id, berth_id)`
    returning rows sorted descending by `announcement_id`.
  - `selected_member_berth_storage_announcement(conn, member_id, berth_id,
    key_certificate_view)` returning the newest valid row or `None`.
  - Validity check reuses the existing `key_certificate` trust-derivation
    helpers already used by `member_transport_announcement` selection.
- **Hub backend:**
  - `cloud_errors.py`: add `CloudAnnouncementMissingExn` to the
    `cloud_storage_required` family.
    Reason string: `"announcement_missing"`.
  - Own-storage `_resolve_berth_cloud_or_raise` (or its caller) gains a
    final check: after the allocation/credential resolution and before
    handing off to the adapter, verify that a valid
    `member_berth_storage_announcement` row exists for
    `(self_member_id, berth_id)` whose `(protocol, url, location)` matches
    the resolved allocation. If not, raise
    `CloudAnnouncementMissingExn`.
  - `POST /cloud/setup`, `POST /cloud_file`, and `GET /cloud_file` map
    `CloudAnnouncementMissingExn` to `409` + `{ "error":
    "cloud_storage_required", "reason": "announcement_missing" }`.
  - Rework `_download_peer_file` and `_effective_peer_transport_selection`
    so the announcement table is the primary source for
    `(member_id, session.berth_id)` routing.
  - Remove the Slice A SmallSeaCollectiveCore peer-read exception
    (`backend.py:1582-1590`).
  - Same-member sibling-device path: route through announcements instead of
    reading the local `berth_cloud_allocation` for peer reads.
- **Tests:** see Micro Tests section below.

### Out of scope

- Removing legacy `team_device(protocol, url, bucket)` columns — Slice C.
- Removing the now-unused `member_transport_announcement` table — Slice C
  (settled above).
- Manager web UI for inspecting announcements — Slice D.
- Provider cleanup of orphaned objects from cross-device first-use races —
  Slice E.
- Real cloud calls in tests. MinIO for S3; mocks elsewhere.
- Manager-side "bulk-pre-publish berths the user knows they'll need" UX
  shortcut. The dedupe + 409 contract makes this composable later without
  touching Slice B.

## Implementation Passes

Each pass should be reviewable and leave the test suite green.

### Pass 1 — Schema and canonical bytes

- Add `member_berth_storage_announcement` table to `core_other_team.sql`
  with the documented columns and the
  `(member_id, berth_id, announcement_id)` index.
- Add `MemberBerthStorageAnnouncement` dataclass and
  `canonical_member_berth_storage_announcement_bytes` to wrasse-trust.
- Add a golden-bytes micro test pinning the canonical representation.

Exit: tests green; no behavior change yet.

### Pass 2 — Manager publish and load helpers

- `publish_member_berth_storage_announcement(...)`.
- `load_member_berth_storage_announcements(conn, member_id, berth_id)`.
- `selected_member_berth_storage_announcement(...)` with trust check.
- Signer-key derivation reuses whatever `member_transport_announcement`
  already does (recon target during this pass).

Exit: helpers exist with their own micro tests; nothing else changed.

### Pass 3 — Hub gate + Manager publish wiring

- Add `CloudAnnouncementMissingExn` to `cloud_errors.py`.
- Add the announcement-presence check to the own-storage resolution path,
  raising the new exception when the newest valid announcement for
  `(self_member_id, berth_id)` does not match the resolved allocation.
- Map the exception to `409` + `"announcement_missing"` on
  `/cloud/setup`, `POST /cloud_file`, and `GET /cloud_file`.
- Wire `publish_member_berth_storage_announcement` into whatever
  component drives `/cloud/setup` today (Manager CLI/UI), so the natural
  user flow is: app gets 409 → user sees Manager prompt → Manager
  publishes → app retries → storage op succeeds.
- Publication is dedupe-guarded: repeated `/cloud/setup` calls or repeated
  retries on the same unchanged locator are no-ops at the row level.

Exit: a fresh team creation + cloud setup produces exactly one
announcement row; repeated calls produce no additional rows; an
own-storage op on a berth with no announcement returns the new 409 and
succeeds after the Manager publishes. Existing Slice A micro tests still
pass.

### Pass 4 — Reroute Hub peer reads through announcements

- `_effective_peer_transport_selection` takes the announcement table as
  primary input, falls back to legacy `team_device` only when no valid
  announcement exists.
- Remove the `SmallSeaCollectiveCore + legacy_transport.bucket` branch
  in `_download_peer_file`.
- Sibling-device same-member peer reads use the same announcement path.

Exit: peer-read tests pass against announcement-driven routing; legacy
fallback path is exercised by an explicit fixture that omits announcements.

### Pass 5 — Fixture sweep

- Update Vault and other app-berth test fixtures that pre-create
  allocations to also publish announcements, except for legacy-fallback
  fixtures which intentionally omit them.
- Verify the linked-device bootstrap flow publishes a fresh announcement
  for the new device's allocation (see open question on sibling-device
  publishing).

Exit: full suite green.

### Pass 6 — Spec sync and wrap-up

- Hub spec §Peer Storage Routing already describes target behavior. Verify
  current wording matches and trim any "target behavior:" qualifiers that
  are now current behavior.
- Manager spec §Member berth storage announcements: same pass — sections
  already describe the design; verify and tighten.
- Run the full test suite.
- Produce `design-record-issue-137-member-berth-storage-announcements.md`
  and `review-note.md`.

## Concrete File Changes

- **Modified:** `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql` — add table + index.
- **Modified:** `packages/wrasse-trust/wrasse_trust/transport.py` — dataclass + canonical-bytes function.
- **Modified:** `packages/small-sea-manager/small_sea_manager/provisioning.py` — publish/load/select helpers with dedupe guard. `member_transport_announcement` helpers stay in place (Slice C removes them).
- **Modified:** `packages/small-sea-hub/small_sea_hub/cloud_errors.py` — add `CloudAnnouncementMissingExn` to the family.
- **Modified:** `packages/small-sea-hub/small_sea_hub/backend.py` — announcement-presence check on the own-storage path; `_download_peer_file`, `_effective_peer_transport_selection`, sibling-device read path; remove the Slice A peer-read exception.
- **Modified:** `packages/small-sea-hub/small_sea_hub/server.py` — map `CloudAnnouncementMissingExn` to `409` on the three own-storage endpoints.
- **Modified test fixtures:**
  - `packages/shared-file-vault/tests/test_hub_sync.py`
  - `packages/shared-file-vault/tests/test_web_sync.py`
  - `packages/small-sea-hub/tests/test_peer_transport.py`
  - `packages/small-sea-hub/tests/test_cloud_api.py` (new 409 reason coverage)
  - `packages/small-sea-manager/tests/test_invitation.py`, `test_hub_invitation_flow.py` if they construct peer-read paths

## Micro Tests

**Canonical bytes:**

- Golden-bytes pin for a fixed announcement input.
- Round-trip: dataclass → canonical bytes → signature → verify.

**Selection:**

- Two announcements for same `(member_id, berth_id)`; newest UUIDv7
  `announcement_id` wins. Confirm `announced_at` ordering is *not* used.
- Same `member_id`, two different `berth_id`s, two different locations:
  routing returns the right one per berth (does not collapse to
  member-only).

**Validity:**

- Bad signature → row treated as inert, fallback considered.
- Signer key not currently trusted (revoked or not yet linked) → inert.
- Old announcement with old signer that is still trusted → still valid
  (no max-age policy).

**Routing:**

- With a valid announcement, peer read uses the announcement's location;
  legacy `team_device.bucket` is ignored.
- Without any announcement, legacy fallback is used and the
  legacy-fallback path is named/observable.
- Same-member sibling read: device A wrote at `loc-A`, device B at
  `loc-B`. Reading from device A through device B's Hub uses A's
  announcement, not B's local allocation.

**Publication and idempotency:**

- First Manager publish for `(self_member_id, berth_id)` writes exactly one
  announcement row.
- Second publish call against an unchanged allocation is a no-op (no new
  row); helper returns `False`.
- After a `materialized_with_locator` writeback changes the allocation
  location, the next Manager publish writes a fresh row carrying the final
  locator; the previous row remains in place and the selection rule picks
  the newer UUIDv7.

**Own-storage gate (the 409 path):**

- Session whose berth has a valid allocation but no announcement →
  `POST /cloud_file` returns `409` with `reason: "announcement_missing"`.
- Manager publishes; the same `POST /cloud_file` retried succeeds.
- `POST /cloud/setup` returns the same `409` reason if called when no
  announcement has been published yet (allocation alone is not enough to
  signal readiness to peers).

**Integration (split per reviewer feedback):**

- Bootstrap leg: Bob accepts Alice's invitation. Bootstrap fetches Alice's
  team bundle chain through `/cloud_proxy` using the inviter-descriptor
  capability — Bob has no team DB and no announcements yet. This path is
  Slice A behavior and stays unchanged.
- Post-bootstrap leg: After Bob has the team DB synced (including Alice's
  announcement), Bob reads further data from Alice's berth through
  announcement-routed `/peer_cloud_file`. This is what Slice B adds.
- A single end-to-end test exercises both legs in sequence and asserts
  that the second leg's read uses the announcement, not the legacy
  formula.

## Validation

A skeptical reviewer should be able to confirm:

1. `grep -rn 'ss-{.*berth_id' packages/small-sea-hub/small_sea_hub/` returns
   only the unrelated notification topic in `server.py`. The peer-read
   formula site in `_download_peer_file` is gone.
2. Every validity rule (signature, trusted signer, no max-age) is exercised
   by at least one micro test.
3. `announcement_id` (UUIDv7) is the selection key — verified by a test
   that flips `announced_at` order and confirms it doesn't change
   selection.
4. Sibling-device same-member peer reads provably route through
   announcements (write to two different buckets from two devices; reading
   each from the other goes through that peer's announcement).
5. Legacy fallback is observable: a fixture that omits announcements
   continues to read through `team_device` and the path is logged or
   tagged "legacy-fallback".
6. The own-storage gate is observable: with no announcement, an own-storage
   op returns `409 / announcement_missing`; with an announcement, it
   proceeds. The 409 round-trip-after-publish path is covered by a micro
   test.
7. Publication is idempotent: a test exercises two `/cloud/setup` calls and
   asserts exactly one announcement row; a third call after a locator
   writeback asserts two rows, with the newer one selected.
8. `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests packages/shared-file-vault/tests` is green.

## Non-Negotiable Invariants

1. `(member_id, berth_id)` is the routing key. Peer reads must not
   collapse to member-only or berth-only resolution.
2. Selection is by descending UUIDv7 `announcement_id`. `announced_at` is
   display-only.
3. An announcement is published only after the corresponding location is
   materialized (and the final locator, if any, is durably recorded).
4. Publication is dedupe-guarded: a new row is written only when the newest
   valid announcement for `(self_member_id, berth_id)` does not already
   match the durable allocation's `(protocol, url, location)`.
5. Own-storage operations refuse to proceed without a matching valid
   announcement; the Hub raises `CloudAnnouncementMissingExn` mapped to
   `409 / cloud_storage_required / announcement_missing`. The Hub never
   signs or writes team-DB announcement rows itself.
6. Validity is structural — signature plus current signer-key trust.
   No max-age policy in v1.
7. Legacy `team_device(protocol, url, bucket)` is fallback only; valid
   announcements always win.
8. No real cloud calls in tests. MinIO for S3; mocks elsewhere.
9. Use "micro tests" terminology in code comments and docstrings.
