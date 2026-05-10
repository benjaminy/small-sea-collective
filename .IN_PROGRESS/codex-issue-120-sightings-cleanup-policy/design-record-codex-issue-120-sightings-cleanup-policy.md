# Design Record: Hub App-Bootstrap Sighting Cleanup Policy

**Branch:** `codex-issue-120-sightings-cleanup-policy`
**Issue:** #120
**Companion docs:** `PLAN.md`, `review-note.md`

This is the boiled-down version of the plan. It captures the choices a future
maintainer is most likely to revisit — the parts where there were real
alternatives — rather than line-by-line implementation detail.

## Policy

Hub `unknown_app_sighting` rows are **active local observations**, not durable
audit history.

- Resolved sightings (Manager re-evaluation returns no prompt) are deleted by
  Manager via `POST /sightings/clear` after re-evaluation, even when the row
  was previously dismissed in the UI.
- Stale sightings (`last_seen_at` strictly older than `now - 30 days`) are
  pruned by Manager via `POST /sightings/prune-stale`.
- A future retry of the same tuple recreates the row, so cleanup is not a
  durable rejection.

Sightings are never synced to peers and are never exposed to apps. Only
`SmallSeaCollectiveCore` sessions can list, clear, or prune.

## Choices worth revisiting

### 1. String-equality precondition on `last_seen_at`

`POST /sightings/clear` requires the caller to echo `last_seen_at` exactly as
returned by `GET /sightings`. Manager and the client helper must not parse
and reformat it.

This is what makes the list/clear race idempotent: an app retry between
Manager listing a row and clearing it bumps `last_seen_at`, and the bumped
row survives the clear with `deleted_count = 0`.

The mechanism only works if every Hub-written timestamp is byte-stable, which
is enforced by a single helper (`_format_sighting_timestamp`) that uses
`isoformat(timespec="microseconds")` with `+00:00` and rejects naive
datetimes. If a future contributor adds a code path that writes
`last_seen_at` without going through that helper, lexicographic SQL
comparison will break silently and the precondition will start missing rows.
Keep all sighting timestamp writes routed through the helper.

### 2. Strict-less-than for the stale boundary

`prune_stale_unknown_app_sightings` uses `last_seen_at < cutoff`. Rows
exactly at the cutoff survive until the next prune pass. The boundary
behavior is pinned by `test_prune_stale_deletes_only_strictly_older`. If
this is ever changed, that test will name itself.

### 3. Pre-prune snapshot for "shown once"

`TeamManager.refresh_app_sightings()` lists, evaluates, and clears resolved
rows from the snapshot before calling `prune_stale_app_sightings()`.
The prompts returned are computed from the pre-prune snapshot, so a Manager
that has been absent for >30 days sees stale observations once before they
disappear from the next refresh. With multiple Manager installations on the
same participant, whichever installation refreshes first prunes; this is an
accepted v1 limitation.

### 4. Evaluate-before-disposition reordering

`refresh_app_sightings` now evaluates `current_app_sighting_prompt(...)` for
every row and only consults `app_sighting_dismissed(...)` for non-`None`
prompts. Resolved rows are cleared regardless of dismissal — dismissal is a
UI preference and must not pin a resolved row in the Hub. This costs a
local DB read per dismissed-but-still-active row; in v1 that is a small
fixed cost.

### 5. `last_seen_at` precondition over a surrogate id

The clear endpoint uses the existing `(participant_hex, app_name, team_name,
client_name)` unique key plus a `last_seen_at` precondition. Adding a
surrogate id to the schema would have been schema churn for no functional
gain. `DELETE` with a body was rejected because some HTTP stacks handle it
poorly.

### 6. No-flap as a Phase 0 gate

The whole design relies on Hub `_resolve_berth(...)` and `request_session(...)`
not recording a fresh sighting once registration and activation exist. That
property is verified by `test_resolved_request_does_not_record_sighting` and
must stay green; if the bootstrap path ever starts re-recording resolved
tuples, every refresh will oscillate between deleting and recreating the row.

### 7. Cleanup failures are non-fatal

Per-row clear failures and prune failures are logged and surfaced as a
single `cleanup_warning` string on the result. Web layer renders it in the
existing `notice-err` slot alongside the prompts that were already computed.
Resolved rows whose clear call failed are not added to prompts because
their `current_app_sighting_prompt(...)` is `None`; the warning is the
user-visible signal that cleanup is incomplete.

### 8. Stale window is a constructor seam, not a config knob

`SIGHTING_STALE_WINDOW = timedelta(days=30)` is a module-level constant.
`SmallSeaBackend(...)` accepts `sighting_stale_window=` and `now_fn=` for
tests but exposes no environment variable or production config knob.
Changing the window requires editing the constant.

## Architecture invariants the cleanup path preserves

- Hub never reads Manager-owned NoteToSelf or team DBs to decide cleanup.
  Reviewer check: `grep` cleanup paths in `backend.py` for `core.db`,
  `NoteToSelf`, or `note_to_self`; nothing should appear.
- Apps cannot list, clear, or prune sightings — only Manager/Core sessions
  pass `_require_manager_session`.
- `GET /sightings` is read-only; mutation lives only on the two POST
  endpoints.
- Pruning is participant-scoped: a session for participant A cannot delete
  participant B's rows.

## Parity contract

Every case where Hub `_resolve_berth` would reject must produce a non-`None`
prompt from `current_app_sighting_prompt(...)`. The converse is intentionally
not required: Manager may conservatively show a prompt for a tuple a fresh
Hub request would now accept (notably when Manager lacks enough local team
state to prove resolution).

`test_hub_rejection_implies_manager_prompt_parity` enumerates the four
known reject states. If a new rejection reason is added to `_resolve_berth`,
add it there too.
