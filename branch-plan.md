# Branch Plan: Simplify Manager Session Cache Key (Issue #46)

**Branch:** `simplify-manager-cache-key`  
**Base:** `main`  
**Issue:** #46 — Drop unused `app` dimension from Manager session cache

## Context

The Manager's session cache (`TeamManager._sessions` and `_pending`) is currently
keyed by `(app, team, mode)`. Every call site always passes
`"SmallSeaCollectiveCore"` as the app name — it never varies. The `app` dimension
in the cache key is dead weight.

The `app` string is still needed at the **protocol level** (passed to
`client.open_session` and `client.start_session`), so we keep it there as a
hardcoded constant inside `_get_or_open_session`. We only remove it from the
cache key and from method signatures that exist solely to manage cache state.

## Changes

### 1. `manager.py` — Cache and method signatures

**Remove:**
- `_session_key(app, team, mode)` helper — replace with `(team, mode)` tuple
  directly, or a simplified `_session_key(team, mode)`
- `app` parameter from: `set_session`, `clear_session`, `set_pending`,
  `clear_pending`, `session_state`, `get_pending_id`, `_get_or_open_session`

**Update:**
- `_sessions` type annotation: `dict[tuple[str, str], SmallSeaSession]`
  (keyed by `(team, mode)`)
- `_pending` type annotation: `dict[tuple[str, str], str]`
- `active_sessions()` return value: drop `"app"` from the dicts, keep
  `{"team": ..., "mode": ...}`
- `_get_or_open_session(team, mode)` — still passes `"SmallSeaCollectiveCore"`
  to `client.open_session` internally
- `accept_invitation` call to `_get_or_open_session` — drop app arg
- `push_team` call to `_get_or_open_session` — drop app arg

### 2. `web.py` — Call sites

**Remove:**
- `_NTS` tuple `("SmallSeaCollectiveCore", "NoteToSelf")` — replace with a
  plain string constant `_NTS_TEAM = "NoteToSelf"`
- `_CORE_APP` constant — no longer needed by web.py (manager handles it
  internally)

**Update every call site** (approx 13 sites) to drop the app argument:
- `_hub_connection_ctx`: `session_state("NoteToSelf", _PASSTHROUGH)`
- `_team_session_ctx`: `session_state(team_name, _ENCRYPTED)`
- `_teams_with_status`: `session_state(t["name"], _ENCRYPTED)`
- `index`: `session_state("NoteToSelf", _PASSTHROUGH)`
- `session_request`: `set_session` / `set_pending` — drop app arg.
  NOTE: `client.start_session` still needs the app string; use a local constant
  or import from manager
- `session_confirm`: `get_pending_id` / `set_session` — drop app arg
- `session_resend_notification`: `get_pending_id` — drop app arg
- `session_close`: `clear_session("NoteToSelf", mode=_PASSTHROUGH)`
- `team_detail`: `session_state(team_name, _ENCRYPTED)`
- `team_session_request`: `set_session` / `set_pending` — drop app arg.
  NOTE: `client.start_session` still needs the app string
- `team_session_confirm`: `get_pending_id` / `set_session` — drop app arg
- `team_session_resend`: `get_pending_id` — drop app arg
- `team_session_close`: `clear_session(team_name, mode=_ENCRYPTED)`

For `client.start_session` calls that still need the app string, define a
module-level `_CORE_APP = "SmallSeaCollectiveCore"` in web.py (or expose it from
manager). This constant is only for the protocol call, not the cache.

### 3. Templates — `active_sessions` rendering

- `packages/small-sea-manager/small_sea_manager/templates/fragments/hub_connection.html`
- `packages/small-sea-manager/small_sea_manager/templates/fragments/team_session.html`
- Check if templates render the `"app"` field from `active_sessions()`. If so,
  remove it.

### 4. Tests

The Manager tests don't directly call cache methods (they go through
`TeamManager` or the web endpoints). The `_open_session` helpers in tests call
the **Hub** (not the Manager cache), so those still need the app string at the
protocol level and should be unaffected.

Verify that all tests pass after the change. Files to watch:
- `tests/test_hub_invitation_flow.py`
- `tests/test_invitation.py`
- `tests/test_cloud_roundtrip.py`
- `tests/test_signed_bundles.py`
- `tests/test_create_team.py`

## Non-goals

- Do **not** remove `app` from the Hub/client protocol layer
  (`open_session`, `start_session`, etc.) — that's a bigger change
- Do **not** fold in any `opt-in-opt-out-crypto` (#42) changes — this should
  land on `main` independently

## Order of operations

1. Create branch from `main`
2. Update `manager.py` (cache key + method signatures)
3. Update `web.py` (all call sites)
4. Check/update templates
5. Run tests, fix any breakage
