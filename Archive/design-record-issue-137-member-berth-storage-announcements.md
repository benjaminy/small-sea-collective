# Design Record: Member-Berth Storage Announcements (Slice B)

**Branch:** `issue-137-member-berth-storage-announcements`
**Primary issue:** #137
**Predecessors:** #134 (vocabulary), #136 (Slice A — own-storage allocation)
**Slice:** B of the planned A→E sequence.
C = legacy `team_device(protocol, url, bucket)` and `member_transport_announcement` cleanup;
D = Manager web UI for announcements;
E = orphaned-object cleanup across cross-device first-use races.

## What this slice accomplished

Slice A made the Hub stop synthesizing storage names from `berth_id` on the
own-storage path; Slice B does the same for peer reads.
The branch introduces `member_berth_storage_announcement` scoped to
`(member_id, berth_id)`, makes the Manager publish a signed row after
materialization, gates `/cloud_file` operations until a valid announcement
exists, and routes peer reads through the announcement path instead of the
legacy formula.

After this branch, no own-storage or app-berth peer code path computes
`ss-{berth_id[:16]}`.
The only formula-like site left is a notification topic in `server.py`,
which is unrelated to storage.

## Interesting choices

### Publication trigger: Manager-mediated with a Hub 409 gate

Three options were considered:

- **A.** Hub returns `409 / cloud_storage_required / announcement_missing`
  on own-storage **file** ops; Manager publishes on prompt; app retries.
- **B.** Hub publishes announcements itself (extending Slice A's
  locator-writeback exception to "Hub records provider reality").
- **C.** Hub records a "needs publish" item in a local table; Manager
  drains it asynchronously.

The branch picked A. Reasoning:

- Keeps the Hub/Manager boundary intact. The Hub does not sign or write
  team-DB rows.
- Reuses the existing `cloud_storage_required` 409 family from Slice A
  rather than inventing a parallel signalling mechanism.
- Cost is one-time-per-berth-per-device user prompt, not an ongoing
  friction.
- Future UX shortcuts (e.g. Manager bulk-prepares known-needed berths at
  team-creation time) compose on top without changing the rule.

The architectural shift in B may still be the right long-term move, but
this slice deliberately avoided it.

### `/cloud/setup` carved out of the gate

Gating `/cloud/setup` on an announcement would deadlock first-time setup:
an announcement is only valid after materialization, and `/cloud/setup`
*is* the materialization entrypoint.

The gate therefore applies to `POST /cloud_file` and `GET /cloud_file`
only.
`/cloud/setup` materializes without an announcement, and Manager publishes
based on the resolved allocation as a second step.

The test `test_cloud_setup_is_not_blocked_by_missing_announcement` exists
specifically as a regression guard for this trap.

### Local-writer bootstrap allowance

`_require_own_storage_announcement` first looks up the trusted selection.
If that comes back missing, it falls back to
`_has_current_device_storage_announcement`, which accepts an announcement
that:

1. matches the durable allocation's `(protocol, url, location)`;
2. is signed by **this device's** current team-device key;
3. has a valid signature.

This lets an invitee push an accepted-but-not-finalized team repo before
the finalization commit (which adds the invitee's signing key to the
trusted set) has been adopted in the local clone.
Without this allowance, Slice B would re-create a different deadlock at
invitation time.

The allowance is deliberately narrow:

- It applies to own-storage only.
  Peer reads in `_download_peer_file` never consult it.
- It requires a signature by the current device's key; a stale or sibling
  device's signature does not qualify.
- It still requires the allocation to match.
  A configuration drift does not get a free pass.

Both the positive case and the negative-for-peer-reads case have explicit
micro tests
(`test_team_cloud_file_allows_current_device_bootstrap_announcement` and
`test_peer_read_does_not_use_current_device_bootstrap_allowance`).

### Legacy fallback narrowed to Core berths

`team_device(protocol, url, bucket)` has only ever been a Core-scoped
value.
The previous code path "Slice A peer-read exception" combined the legacy
endpoint with a formula bucket for app berths, which silently worked only
because Slice A test fixtures pre-created buckets at exactly that formula
name.

Slice B removes the formula entirely.
App-berth peer reads without a valid announcement return a clean
`SmallSeaNotFoundExn` (404), not a wrong-bucket silent route.
Legacy `team_device` fallback survives for Core peer reads only, until
Slice C removes the columns.

### Publication idempotency: match the allocation, not just the berth

`publish_member_berth_storage_announcement` returns `wrote: True/False` and
suppresses the insert when the newest valid announcement for
`(self_member_id, berth_id)` already has matching `(protocol, url,
location)`.

Republishing happens only when the locator changes — for example after a
`materialized_with_locator` writeback rewrites the allocation.
The micro test `test_member_berth_storage_publish_is_deduped_by_current_location`
walks through first publish → no-op repeat → republish-after-change.

### No SQLite foreign keys on the announcement table

The table intentionally omits FKs on `member_id` and `berth_id`.
Invitation and linked-device bootstrap flows can know a signed
announcement before the local clone has adopted the matching member, berth,
or trust rows.
A FK would reject the row at write time and force out-of-order coordination
for no real safety benefit — signature + trust verification at read time
gives a stronger property than referential integrity here.

This is documented in the Manager spec alongside the schema.

### NoteToSelf exempted this slice

The gate is skipped for NoteToSelf sessions, and no NoteToSelf-scoped
announcement table is added.
NoteToSelf has no `member` table — its sibling-device sync story would
need a `device_berth_storage_announcement` shape, with different selection
rules.
Including that here would have doubled the schema and forced a "what's a
NoteToSelf member?" decision that isn't on the critical path.
Out of scope; bundled with the broader multi-device NoteToSelf sync story.

### Co-existence with `member_transport_announcement`

The member-only `member_transport_announcement` table, dataclass, and
helpers stay in place this slice.
Peer-storage routing no longer consults them, but
`_runtime_peers_for_session` and any UI/status surfaces still read the old
table.
Actual removal (table, helpers, runtime-peer rewrite) moves to Slice C
alongside the `team_device` column cleanup.

### Pattern reuse from `wrasse-trust`

`MemberBerthStorageAnnouncement`, `canonical_member_berth_storage_announcement_bytes`,
`verify_member_berth_storage_announcement_signature`, and
`select_effective_member_berth_storage` mirror the existing
`MemberTransportAnnouncement` quintet exactly.
Canonical bytes use the project-standard
`json.dumps(..., sort_keys=True, separators=(",", ":"))` convention and
explicitly exclude the signature field.

## Notable structural artifacts

- `packages/small-sea-hub/small_sea_hub/cloud_errors.py` —
  `CloudAnnouncementMissingExn` added to the `cloud_storage_required`
  family. Reason string: `announcement_missing`.
- `_require_own_storage_announcement` and `_has_current_device_storage_announcement`
  in `backend.py` are the two-tier check for the own-storage gate.
- `publish_member_berth_storage_announcement` in `provisioning.py` is the
  single Manager entry point; the `TeamManager` wrapper in `manager.py`
  delegates to it.
- Schema migration `_migrate_team_db` bumped to version 59 with a
  `from_version < 59` hook creating the table and its
  `(member_id, berth_id, announcement_id)` index.

## What this slice does NOT do

- Remove `member_transport_announcement` or `team_device(protocol, url,
  bucket)` columns — Slice C.
- Rewrite `_runtime_peers_for_session` to consume the new table —
  Slice C.
- Add device-scoped NoteToSelf storage announcements — bundled with
  multi-device NoteToSelf sync, separate slice.
- Build a Manager web UI for inspecting announcements — Slice D.
- Clean up orphaned provider objects from cross-device first-use races —
  Slice E.
- Cover a team-device-rotation hardening test (a prior key signing a
  storage announcement should not satisfy the bootstrap allowance once
  rotated) — captured in FOLLOW-UP.md.
- Extract the duplicated `_publish_storage_announcement_for_session`
  fixture helper into a shared test utility — captured in FOLLOW-UP.md.

## Validation summary

- `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests
  packages/shared-file-vault/tests` is green.
- `grep -rn 'ss-{.*berth_id' packages/small-sea-hub/small_sea_hub/`
  returns only an unrelated notification topic in `server.py`. No
  own-storage or peer-read formula synthesis remains.
- Every reason in the `cloud_storage_required` family
  (including the new `announcement_missing`) is exercised by at least
  one micro test.
- Bootstrap-allowance positive and peer-read-negative paths are both
  covered by targeted micro tests.
- Publication idempotency (no-op repeat, republish on locator change) is
  covered by a Manager micro test.
- App-berth peer read without an announcement asserts `404`, not a
  formula-derived bucket route.
