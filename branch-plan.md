# Branch Plan: Simplify Manager Session Cache Key (Issue #46)

**Branch:** `manager-session-key-cleanup`  
**Base:** `main`  
**Issue:** #46 — Drop unused `app` dimension from Manager session cache

## Context

The Manager's session cache (`TeamManager._sessions` and `_pending`) is currently
keyed by `(app, team, mode)`. Every call site always passes
`"SmallSeaCollectiveCore"` as the app name — it never varies. The `app` dimension
in the cache key is dead weight.

The `app` string is still needed at the **protocol level** (passed to
`client.open_session` and `client.start_session`), so this branch keeps a
single `_CORE_APP = "SmallSeaCollectiveCore"` constant as the source of truth.
We only remove `app` from the cache key and from method signatures that exist
solely to manage cache state.

## Changes

### 1. `manager.py` — Cache and method signatures

**Remove:**
- `_session_key(app, team, mode)` helper — use `(team, mode)` tuple directly
- `active_sessions()` method — confirmed dead code (no external callers)
- `app` parameter from: `set_session`, `clear_session`, `set_pending`,
  `clear_pending`, `session_state`, `get_pending_id`, `_get_or_open_session`

**Update:**
- Add a module-level `_CORE_APP = "SmallSeaCollectiveCore"` constant so Manager
  internals and `web.py` can share one source of truth for protocol calls
- `_sessions` type annotation: `dict[tuple[str, str], SmallSeaSession]`
  (keyed by `(team, mode)`)
- `_pending` type annotation: `dict[tuple[str, str], str]`
- `_get_or_open_session(team, mode)` — still passes `_CORE_APP` to
  `client.open_session` internally
- `connect(team, ...)` — use `_CORE_APP` for its `request_session` protocol call
- `accept_invitation` call to `_get_or_open_session` — drop app arg
- `push_team` call to `_get_or_open_session` — drop app arg

### 2. `web.py` — Call sites

**Remove:**
- `_NTS` tuple `("SmallSeaCollectiveCore", "NoteToSelf")` — replace with a
  plain string constant `_NTS_TEAM = "NoteToSelf"`

**Update every call site** (approx 13 sites) to drop the app argument:
- `_hub_connection_ctx`: `session_state("NoteToSelf", _PASSTHROUGH)`
- `_team_session_ctx`: `session_state(team_name, _ENCRYPTED)`
- `_teams_with_status`: `session_state(t["name"], _ENCRYPTED)`
- `index`: `session_state("NoteToSelf", _PASSTHROUGH)`
- `session_request`: `set_session` / `set_pending` — drop app arg.
  NOTE: `client.start_session` still needs the app string; import `_CORE_APP`
  from `manager.py` and keep using it for protocol calls
- `session_confirm`: `get_pending_id` / `set_session` — drop app arg
- `session_resend_notification`: `get_pending_id` — drop app arg
- `session_close`: `clear_session("NoteToSelf", mode=_PASSTHROUGH)`
- `team_detail`: `session_state(team_name, _ENCRYPTED)`
- `team_session_request`: `set_session` / `set_pending` — drop app arg.
  NOTE: `client.start_session` still needs `_CORE_APP`
- `team_session_confirm`: `get_pending_id` / `set_session` — drop app arg
- `team_session_resend`: `get_pending_id` — drop app arg
- `team_session_close`: `clear_session(team_name, mode=_ENCRYPTED)`

Do **not** add an adapter layer above the client lib just to hide the core app
name. This branch should centralize the constant, not invent a new abstraction.

### 3. Remove legacy `self.session` / `connect()` / `push()` / `pull()`

The singular `self.session` attribute is redundant with the `_sessions` dict.
It is set only by `connect()` and consumed only by the legacy `push()` and
`pull()` methods. No production code calls any of these three methods; the web
UI uses `_sessions` exclusively, and `push_team()` already replaced `push()`.

**Remove from `manager.py`:**
- `connect(team, pin_provider, mode)` method (~15 lines) — sets `self.session`
  via `request_session` + `confirm_session`. The same flow is handled by the
  web PIN path (`session_request` / `session_confirm` in `web.py`) which stores
  results in `_sessions`.
- `push(repo_dir)` method (~20 lines) — uses `self.session`. Superseded by
  `push_team(team_name)` which uses `_get_or_open_session`.
- `pull(repo_dir, from_member_id)` method (~15 lines) — uses `self.session`.
  No replacement exists yet, but no code calls it either; when peer-pull is
  needed it can be built on top of `_sessions` directly.
- All references to `self.session` (the attribute is never initialized in
  `__init__`, only assigned in `connect()`).

**Update tests (`test_cloud_roundtrip.py`):**
- `test_connect_pin_flow` and `test_connect_requires_pin_provider` are the only
  callers of `connect()`. Remove or rewrite them:
  - The PIN-flow semantics will be covered by the new web PIN-flow micro test
    added in step 4 (request → pending → confirm → active).
  - The "requires pin_provider" guard disappears with `connect()`.

**Why now:** The rest of this branch already touches every session-cache method
signature. Carrying the dead `self.session` path forward means updating it for
the new key shape only to delete it later. Removing it in the same branch keeps
the diff self-contained.

### 4. Validation and tests

Add focused micro tests so this branch proves the cache shape changed safely,
rather than relying only on broader integration coverage.

**New/updated micro tests:**
- `TeamManager` cache state is keyed by `(team, mode)`, not by app:
  setting `"ProjectX"` encrypted does not affect `"ProjectX"` passthrough or
  `"NoteToSelf"` passthrough
- `set_session(...)` clears only the matching pending entry
- A small `small_sea_manager.web` PIN-flow test:
  request session -> pending state -> confirm session -> active state
  This should exercise `create_app(...)` route wiring so broken call sites in
  `session_state`, `set_session`, `set_pending`, and `get_pending_id` are caught

**Test location consideration:**
- These micro tests could be added to `packages/small-sea-manager/tests/test_cloud_roundtrip.py`
  or a new `packages/small-sea-manager/tests/test_manager.py`.

The existing `_open_session` helpers in tests call the **Hub** directly, not the
Manager cache, so they should still keep passing the app string at the protocol
layer.

**Regression suite to run after the focused micro tests:**
- `tests/test_hub_invitation_flow.py`
- `tests/test_invitation.py`
- `tests/test_cloud_roundtrip.py`
- `tests/test_signed_bundles.py`
- `tests/test_create_team.py`

Success criteria:
- No Manager cache method or cache key includes `app`
- All remaining protocol-level Hub calls still pass `_CORE_APP`
- No `self.session` attribute; no `connect()`, `push()`, or `pull()` methods
- No other package is made to depend on Manager-only session-cache internals
- All focused micro tests and regression tests pass

## Non-goals

- Do **not** remove `app` from the Hub/client protocol layer
  (`open_session`, `start_session`, etc.) — that's a bigger change
- Do **not** move Manager-specific session-cache behavior into
  `small-sea-client` as part of this branch
- Do **not** fold in any `opt-in-opt-out-crypto` (#42) changes — this should
  land on `main` independently
- ~~Do **not** address the redundancy between `self.session` and `_sessions` yet;
  flag for future cleanup.~~ → Included as step 5 below.

## Order of operations

1. Update `manager.py` (shared `_CORE_APP`, cache key, method signatures, remove dead code)
2. Update `web.py` (import `_CORE_APP`, fix all cache-method call sites)
3. Remove legacy `self.session` / `connect()` / `push()` / `pull()` from `manager.py`
4. Update `test_cloud_roundtrip.py` (remove/rewrite `connect()`-dependent tests)
5. Add focused micro tests for cache semantics and the web PIN flow
6. Run focused micro tests, then the regression suite, and fix any breakage
