# Review note — issue #120 Hub sighting cleanup

## TL;DR

Hub app-bootstrap sightings are now active local observations rather than
durable forever-rows. Manager refresh clears resolved rows after
re-evaluation and prunes stale rows (>30 days) on each refresh.

## What changed

- Hub backend: canonical UTC timestamp helper (`isoformat(timespec="microseconds")`,
  `+00:00`) used for every sighting write and prune cutoff. Constructor
  takes `now_fn=` and `sighting_stale_window=` for tests.
- Hub server: shared `_require_manager_session` dependency, plus
  `POST /sightings/clear` (tuple + `last_seen_at` precondition, idempotent)
  and `POST /sightings/prune-stale` (no body, participant-scoped).
- Client: `Session.clear_app_sighting(...)` and
  `Session.prune_stale_app_sightings()`, parallel to the existing
  `Session.app_sightings()`.
- Manager: `TeamManager.refresh_app_sightings()` now evaluates the prompt
  before applying disposition, clears resolved rows (even when dismissed),
  prunes once after the snapshot, and returns prompts plus an optional
  `cleanup_warning`. Web layer renders the warning in the existing
  `notice-err` slot.
- Specs: Hub spec has a sighting-lifecycle section + endpoint reference;
  Manager spec describes the evaluate-before-disposition refresh and
  non-fatal cleanup failures.

## Where to look first

1. `packages/small-sea-hub/small_sea_hub/backend.py` — `_format_sighting_timestamp`,
   `delete_unknown_app_sighting`, `prune_stale_unknown_app_sightings`.
2. `packages/small-sea-hub/small_sea_hub/server.py` — `_require_manager_session`,
   the two new endpoints.
3. `packages/small-sea-manager/small_sea_manager/manager.py` —
   `AppSightingsRefresh` and the rewritten `refresh_app_sightings()`.
4. `packages/small-sea-hub/tests/test_sightings_cleanup.py` and
   `packages/small-sea-manager/tests/test_app_sightings_cleanup.py` — the
   new micro tests; the boundary, parity, and no-flap cases are the load-bearing ones.

## Risk areas to watch

- **Timestamp format discipline.** Lexicographic SQL comparison only works
  because every write goes through `_format_sighting_timestamp`. A future
  caller using raw `datetime.now().isoformat()` would silently break the
  prune predicate and the clear precondition. The helper rejects naive
  datetimes to make the bypass loud, but it can't catch a different
  `astimezone(...).isoformat()` call.
- **No-flap invariant.** `_resolve_berth(...)` after registration and
  activation must not call `record_unknown_app_sighting(...)`. Pinned by
  `test_resolved_request_does_not_record_sighting`. If you change the
  bootstrap path and that test starts to flake, do not "fix" it —
  investigate first.
- **Parity contract.** Any new rejection reason added to
  `_resolve_berth(...)` must also be added to `current_app_sighting_prompt(...)`
  and to `test_hub_rejection_implies_manager_prompt_parity`.

## Reviewer validation steps

```sh
uv run pytest packages/small-sea-hub/tests/test_app_bootstrap.py \
              packages/small-sea-hub/tests/test_sightings_cleanup.py \
              packages/small-sea-manager/tests/test_app_sightings_ui.py \
              packages/small-sea-manager/tests/test_app_sightings_cleanup.py \
              packages/small-sea-client/tests/test_client.py
git diff --check
```

Plus a one-shot grep that the cleanup path stays inside the Hub-local DB:

```sh
grep -nE "core\.db|NoteToSelf|note_to_self" \
  packages/small-sea-hub/small_sea_hub/backend.py | \
  grep -E "delete_unknown|prune_stale" || echo "clean"
```

Should print `clean`.

## Out of scope

- Background pruning workers and production config knobs for the stale
  window (deferred to whenever real usage suggests they are needed).
- Batch clear endpoint (deferred per plan; v1 accepts one HTTP call per
  resolved row).
- Audit-style sighting history. Sightings are explicitly not durable audit
  log in this branch.
