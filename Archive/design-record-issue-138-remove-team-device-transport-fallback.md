# Design Record — Issue #138: Remove Legacy `team_device` Transport Fallback

GitHub issue: https://github.com/benjaminy/small-sea-collective/issues/138
Branch: `issue-138-remove-team-device-transport-fallback`

## Goal recap

Stop treating `team_device(protocol, url, bucket)` as peer storage routing data.
After this branch, peer-readable storage for a berth is discovered **only** through
signed `member_berth_storage_announcement` rows scoped to `(member_id, berth_id)`.
`team_device` carries device identity only.

## What changed

### Replacement publishing wired into production (the hard part)

Before this branch, `member_berth_storage_announcement` was published **only from
tests**; the three production flows that allocate Core berth cloud storage all
mishandled it. They now publish a signed announcement for the berth they allocate:

- `create_team` — publishes in-transaction with the creator's own team device key.
- `accept_invitation` — previously *discarded* the allocation and published
  nothing (the invitee was discoverable through no channel at all). Now captures
  the allocation, publishes the acceptor's own signed announcement, and commits
  it so the acceptor's next push carries it.
- device-link/bootstrap (`finalize_linked_device_bootstrap`) — see decision below.

### Transaction-safe announcement core (Step 4.1a)

`publish_member_berth_storage_announcement` opened its own engine + transaction.
`create_team` already holds an open write transaction on the same `core.db`, so
calling the public helper there would risk `database is locked`. Factored out
`_insert_member_berth_storage_announcement(conn, …)` — sign + dedup-check + INSERT
against a caller-supplied live `conn`. The public helper now opens the engine and
delegates to that core. `create_team` calls the core directly with its open conn;
`accept_invitation` uses the public helper (no open `core.db` transaction there).

### Fallback removal

- Hub (`backend.py`): deleted `_legacy_transport_for_member`,
  `_load_member_transport_announcements`, the dead `_effective_peer_transport` and
  `_effective_peer_transport_selection` (the member-level/non-berth path was
  unreachable — `_download_peer_file` always passes a `berth_id`). `_download_peer_file`
  now calls `_select_member_berth_storage` directly. Removed orphaned imports
  (`MemberTransportAnnouncement`, `TransportEndpoint`, `select_effective_member_transport`).
- `wrasse_trust.transport`: removed the `legacy_fallback` parameter and the
  `legacy-fallback` status branch from both `select_effective_member_transport`
  and `select_effective_member_berth_storage`. A member with no valid signed
  announcement now resolves to `missing`.
- Manager (`provisioning.py`): deleted `_legacy_transport_by_member`; stopped
  passing `legacy_fallback` from `_effective_transports_by_member`.

### Schema: clean drop (not "tolerate")

**Decision:** Drop `protocol`, `url`, `bucket` from `team_device` entirely, rather
than leaving them tolerated. Rationale: the project is pre-alpha (the plan's stated
preference is the clean schema), and a dropped column is the strongest possible
guarantee that legacy fields cannot affect routing — no reader *can* read them.
Updated three schema surfaces in lockstep with `_upsert_team_device_row`:
- `sql/core_other_team.sql` `team_device` DDL,
- the inline `CREATE TABLE IF NOT EXISTS team_device` in the pre-alpha
  `_migrate_team_db_to_member_and_team_device` migration,
- the raw `INSERT OR REPLACE INTO team_device …` in that same migration (and the
  now-unused `peer.protocol/url/bucket` extraction it fed).

A pleasant consequence: the plan's proposed negative test ("insert only
`team_device(protocol,url,bucket)` and prove the Hub ignores it") is now
*impossible to write* — the columns don't exist. The decisive creator test asserts
the columns are absent via `PRAGMA table_info` instead.

#### Schema version bump + migration of existing DBs (committee feedback)

Initial revision changed the DDL but left `USER_SCHEMA_VERSION = 59` unchanged.
`ensure_team_db_schema` short-circuits when a DB is already at the current version,
and the prior `main` commit was *also* version 59 (with the columns present). So a
DB written by old code would stay at 59 and silently keep `protocol/url/bucket`
forever — contradicting the "columns are gone everywhere" claim. The DDL drop only
helps *freshly created* DBs.

**Decision (answers the committee's open question):** the support boundary is **all
DBs**, not "fresh DBs only." Bumped `USER_SCHEMA_VERSION` to **60** and added an
incremental `if from_version < 60` step in `_migrate_team_db` that drops the three
columns via `ALTER TABLE team_device DROP COLUMN` (SQLite 3.35+; runtime is 3.47.1).
The drop is guarded by `_table_columns(...)` so it is a no-op for `team_device`
tables already created without the columns (fresh in-branch DBs, or DBs migrated up
from `< 56` whose inline `CREATE` now omits the columns). The shared version bump is
safe for the user/NoteToSelf DB path too: `_migrate_user_db` already only has steps
through `< 55` and has been relying on a no-op restamp for 55→59, so 59→60 is one
more no-op restamp there.

Covered by `test_migration_drops_legacy_team_device_transport_columns` (reconstructs
the legacy v59 shape, then proves `ensure_team_db_schema` drops the columns and
stamps version 60) and `test_create_team_produces_team_device_without_transport_columns`.

### `member_transport_announcement` kept

`member_transport_announcement` and `select_effective_member_transport` back a live
Manager web feature (`announce_transport` endpoint + members listing). Per #138
scope, they were **not** removed. The only behavior change: a member with no signed
member-transport announcement now reports `transport_status: "missing"` (and, if
self, `needs_transport_announcement: True`) instead of `"legacy-fallback"`.

## Decisions worth flagging

1. **Device-link/bootstrap publishing was deferred, not implemented.**
   `finalize_linked_device_bootstrap` *derived* a bucket name via
   `_bucket_name_for_protocol` rather than calling `_auto_allocate_berth_cloud_if_available`,
   so it is not an allocation-producing flow in the same sense as the other two.
   A linked device joins an **existing** member who already announced their berth
   storage (when they created/joined the team); that announcement already covers
   the member. So this branch only **stops the transport-field write** here and
   defers per-linked-device storage announcements to #139. This avoids guessing
   at a signer identity and a bucket-name scheme that may not match real allocations.

2. **`member_berth_storage_announcement` has no `member_id` FK** (confirmed in the
   schema; only `member_transport_announcement` has one). This is load-bearing: it
   lets `accept_invitation` publish the invitee's announcement *before* the invitee
   is admitted (their `member` row hasn't synced yet). The spec already documented
   this intentional FK-free design; this branch relies on it.

3. **Cross-member sync delivery is out of scope.** Each member publishes and commits
   their own announcement to their own `core.db`/bucket. A reader resolving a *peer's*
   storage needs that peer's announcement merged into the reader's clone, which is the
   existing sync layer's job (tracked by #150), not #138.

## Validation

See `review-note.md` for the full test matrix. The decisive end-to-end coverage:
- **Creator:** `test_create_team_publishes_creator_storage_for_peer_download`
  (`test_peer_transport.py`) — `create_team` alone, no manual publish, a peer
  download resolves the creator's announced storage; asserts `team_device` has no
  transport columns.
- **Invitee:** `test_full_invitation_flow` (`test_invitation.py`) — the real
  invite/accept flow with no manual/helper publish for Bob proves `accept_invitation`
  auto-published a valid, Bob-signed announcement matching Bob's allocation.
