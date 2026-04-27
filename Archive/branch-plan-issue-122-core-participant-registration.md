# Branch Plan: Migrate Core sessions onto participant-level app registration

Tracks GitHub issue #122 — follow-up to issue #111.

**Branch:** `issue-122-core-participant-registration`
**Archived:** Branch wrapped after implementation, review feedback, and
validation.
**Final validation:**
- `uv run pytest packages/small-sea-manager/tests/test_create_team.py packages/small-sea-hub/tests/test_app_bootstrap.py` -> 20 passed
- `uv run pytest packages/small-sea-manager/tests/test_invitation.py` -> 4 passed
- `uv run pytest packages/small-sea-hub/tests` -> 78 passed
- `uv run pytest packages/shared-file-vault/tests` -> 59 passed, 3 skipped
- `uv run pytest packages/small-sea-manager/tests` -> 60 passed
- `git diff --check` -> clean
- `rg -n "if app_name != \"SmallSeaCollectiveCore\"|issue-111-follow-up" packages/small-sea-hub packages/small-sea-manager` -> no hits

## Goal

Register `SmallSeaCollectiveCore` through the same participant-level
registration + team-level activation primitives used by ordinary apps, and
remove the hardcoded Core exception in Hub berth resolution.

After this branch:

- The Hub's `_resolve_berth` treats Core like any other app.
- Manager provisioning routes Core's app/berth rows through the same private
  primitives that back `register_app_for_participant` and
  `activate_app_for_team`, so Core no longer has a duplicate SQL writer.
- The Hub no longer carries an `if app_name != "SmallSeaCollectiveCore":`
  branch in its session path.

## Current State (verified on this branch)

Hub-side compatibility exception:
- `packages/small-sea-hub/small_sea_hub/backend.py:406-420` — `_resolve_berth`
  skips the participant-berth presence check for Core with a TODO referencing
  this issue.

Manager-side direct Core inserts that bypass the registration helpers:
- `packages/small-sea-manager/small_sea_manager/provisioning.py:1670-1676` —
  `_initialize_user_db` inserts the Core `app` row and the
  `NoteToSelf`/Core `team_app_berth` row directly during participant creation.
- `packages/small-sea-manager/small_sea_manager/provisioning.py:4145-4159` —
  `create_team` inserts Core's `app` row, team `team_app_berth` row, and the
  creator's `berth_role` directly into the new team's DB.
- `accept_invitation` (provisioning.py:4434+) clones a team repo whose DB
  already contains Core; the invitee's NoteToSelf gets a `team` row but no
  per-team participant-level Core registration. (The existing global
  NoteToSelf/Core berth from `_initialize_user_db` is what currently lets the
  Hub's lookup succeed.)

The generic helpers already exist:
- `register_app_for_participant` (provisioning.py:4202).
- `activate_app_for_team` (provisioning.py:4253) — note this depends on
  `_core_berth_role` to mirror each member's Core role onto the new app's
  berth, so it cannot bootstrap Core itself without care (see caveat below).

## Implementation

### 1. Route Core's NoteToSelf registration through the participant primitive

Refactor the participant-registration SQL out of
`register_app_for_participant` into a private helper that can run inside an
existing NoteToSelf transaction, for example:

`_ensure_participant_app_registration(conn, team_id, app_name) -> (app_id, changed)`

That helper should:

- Find or insert the `app` row by friendly name, preserving the existing
  duplicate-friendly-name guard.
- Find or insert the `team_app_berth` row for the NoteToSelf team and app.
- Return whether it changed SQL state, but not commit, touch git, or create
  directories.

Then:

- `register_app_for_participant` keeps its public behavior: open the
  NoteToSelf connection, call the helper, create
  `NoteToSelf/{app_name}/` if needed, commit, and make the existing
  `Registered app {app_name}` git commit when anything changed.
- `_initialize_user_db` creates the NoteToSelf `team` row, calls the same SQL
  helper for `SmallSeaCollectiveCore` inside the initial transaction, creates
  `NoteToSelf/SmallSeaCollectiveCore/`, and includes the resulting `core.db`
  rows in the initial "Welcome to Small Sea Collective" commit.

This keeps initial identity creation atomic enough for current pre-alpha
expectations without adding a second post-welcome commit, while still making
the SQL writer shared with ordinary participant app registration.

Observable behavior change to flag in the PR description: after this step,
`create_new_participant` newly creates an empty
`Participants/{hex}/NoteToSelf/SmallSeaCollectiveCore/` directory, matching
the layout `register_app_for_participant` already produces for ordinary apps.
This is intentional and is the visible side effect that proves Core now uses
the same writer.

### 2. Route Core's team-creation berth through the activation primitive

In `create_team`, replace the direct `INSERT INTO app` /
`INSERT INTO team_app_berth` / `INSERT INTO berth_role` block with the same
in-connection primitive `activate_app_for_team` uses internally, for example:

`_ensure_team_app_activation(conn, app_name, role_for_member) -> (app_id, berth_id, changed)`

This helper should:

- Find or insert the team DB `app` row by friendly name, preserving the
  existing duplicate-friendly-name guard.
- Find or insert the `team_app_berth` row for that app.
- For each current member, find or insert the `berth_role` row using the
  provided role resolver.
- Return the app ID, berth ID, and whether SQL state changed, but not commit
  or touch git.

Caveat: the public `activate_app_for_team` flow reads
`_core_berth_role(conn, member_id)` to decide each member's role on the new
berth. For Core itself this is circular because there is no Core berth yet.
The shared helper resolves that by taking an explicit role-resolver callable:

- `activate_app_for_team` passes `_core_berth_role` (existing behavior).
- `create_team` passes a resolver that returns `"read-write"` for the creator
  member present during team creation.

This keeps the SQL shape identical between Core's first activation and any
other app's activation, while letting the role decision differ. It also avoids
calling the public `activate_app_for_team` before the new team repo has been
initialized.

### 3. Cover the invitation path

Decision for this branch: `accept_invitation` remains a no-op for Core
participant registration.

Reason: participant-level app registration is global to the participant's
NoteToSelf scope, not per external team. Once `_initialize_user_db` registers
Core for the participant, `_resolve_berth` can satisfy the participant-side
check for any team by finding the NoteToSelf Core `team_app_berth`. Adding a
per-team `NoteToSelf/SmallSeaCollectiveCore/{team}` entry would duplicate
state without changing observable session behavior.

Load-bearing assumption to record explicitly: this no-op is only safe because
`_resolve_berth`'s participant-side check uses
`_single_berth_id_for_app(..., team_id=None)`, i.e. it accepts any NoteToSelf
berth for the app regardless of which team the session is for. If that lookup
is ever tightened to a per-`(team, app)` shape, this step has to be revisited
and `accept_invitation` must then write a per-team participant Core entry. A
short code comment in `accept_invitation` should call this out so a future
change to the lookup does not silently break the invitee's first Core
session.

### 4. Remove the Hub Core exception

Delete the `if app_name != "SmallSeaCollectiveCore":` branch in
`backend.py:_resolve_berth` plus its TODO. Keep the participant-berth presence
check unconditional. Remove the now-stale comment at backend.py:406-408.

### 5. Tidy adjacent references

Audit for any other `SmallSeaCollectiveCore` literals that exist *only*
because of the bypass, and remove or simplify them. (Manager-internal
references like `_CORE_APP` in `manager.py:12` remain valid; they identify
Core for legitimate reasons such as core-role lookups.)

## Validation

To convince a skeptical reviewer that the goals of the branch have been met:

### Goal 1 — Core uses the same registration path as ordinary apps

- Add a micro test in `packages/small-sea-manager/tests` (or the existing Hub
  app-bootstrap tests if that gives cleaner fixtures) asserting that, after
  `create_new_participant`, NoteToSelf has exactly one Core `app` row, exactly
  one NoteToSelf/Core `team_app_berth` row, and a
  `NoteToSelf/SmallSeaCollectiveCore/` directory. Because the direct inline
  Core insert is removed, these side effects prove the shared participant
  primitive is the only writer.
- Add a micro test that calling `register_app_for_participant(...,
  "SmallSeaCollectiveCore")` after participant creation is idempotent: no
  duplicate `app` or `team_app_berth` rows and no failure. This catches drift
  between the initialization path and the public registration path.
- Add a micro test that, after `create_team`, the Core berth + creator's
  read-write `berth_role` row have the same row shape the shared activation
  primitive produces for a non-Core app. The role resolver difference is the
  only intentional variation.
- Re-run the existing `test_vault_bootstrap_loop_rejects_then_registers_then_activates`
  test; Vault behavior must remain unchanged.

### Goal 2 — Hub Core exception is gone and behavior is preserved

- Grep proof: `rg "SmallSeaCollectiveCore" packages/small-sea-hub/small_sea_hub/`
  shows no references in the session/berth-resolution code paths after the
  change. (Test fixtures and config defaults like
  `config.py: app_name = "SmallSeaCollectiveCore"` are not exceptions and may
  remain.)
- Run all existing Hub session tests:
  - `tests/test_session_flow.py` (all variants).
  - `tests/test_app_bootstrap.py` (all participant/team berth scenarios).
  - `tests/test_runtime_watch.py`, `tests/test_cloud_api.py`,
    `tests/test_notifications.py`, `tests/test_note_to_self_self_signal.py`.
- Add a regression micro test: after participant + team creation, delete the
  NoteToSelf `team_app_berth` row for Core while leaving the NoteToSelf Core
  `app` row and the team DB Core activation intact. A Core team-session
  request must now produce `participant_berth_missing` instead of succeeding.
  This proves the branch removed the bypass rather than merely relocating it.
- Add an invitation micro test or extend an existing one: an invitee who
  accepts a team invitation can open a Core session for that team using the
  participant-level Core registration created during identity initialization,
  with no per-team participant registration write during `accept_invitation`.

### Goal 3 — End-to-end manager + hub micro tests still pass

Run the broader manager/hub suites to catch any coupling we missed:

- `uv run pytest packages/small-sea-manager/tests`
- `uv run pytest packages/small-sea-hub/tests`
- `uv run pytest packages/shared-file-vault/tests`

## Integrity Checks

To convince a skeptical reviewer that repo integrity is maintained or improved:

- **Coupling decreases:** removing the Core exception eliminates one
  app-name-aware branch from the Hub; the Hub becomes truly app-agnostic in
  its session path. This is a net reduction in coupling between Hub and the
  set of known apps.
- **Single source of truth for "register an app" / "activate an app":** all
  writers go through one participant-registration primitive or one
  team-activation primitive. Public Manager operations keep owning git commits;
  bootstrap flows reuse the same SQL semantics inside their existing
  transactions.
- **No backward-compat shims:** AGENTS.md states pre-alpha so we are free to
  change the call shape rather than preserve it. We will not introduce
  migration code; existing on-disk DBs in dev environments can be recreated.
- **Schema unchanged:** this branch is a refactor of write paths, not a
  schema change. The same `app`, `team_app_berth`, and `berth_role` rows are
  produced; only the function that produces them changes.
- **Test surface grows, not shrinks:** the regression test in Goal 2
  exercises a path that was previously unreachable for Core and is one of the
  main reasons the cleanup is worth the disruption.

## Wrap-up

Actual implementation matched the plan:

- `_ensure_participant_app_registration` is the shared NoteToSelf-side SQL
  primitive behind Core initialization and public participant app registration.
- `_ensure_team_app_activation` is the shared team-side SQLAlchemy primitive
  behind Core team creation and public team app activation.
- `_initialize_user_db` now registers Core through the participant primitive
  and creates `NoteToSelf/SmallSeaCollectiveCore/` using the same directory
  convention as `register_app_for_participant`.
- `create_team` now activates Core through the team primitive. The role
  resolver returns `"read-write"` for every current member because the creator
  is the only member present while bootstrapping a new team's Core berth.
- `accept_invitation` remains a no-op for Core participant registration, with
  an in-code comment recording the load-bearing assumption that Hub
  participant-berth lookup is identity-wide in NoteToSelf rather than scoped
  per external team.
- `backend.py:_resolve_berth` now runs the participant-berth check
  unconditionally, so Core no longer has a Hub bypass.

Review feedback was incorporated before wrap-up:

- Aligned Core app directory creation with `register_app_for_participant`.
- Unpacked ignored helper return values for readability.
- Clarified the `create_team` role-resolver assumption.
- Added symmetric Core team-activation idempotency coverage.
- Added a positive Core team-session assertion next to the negative
  missing-participant-berth regression.

This plan has been moved to
`Archive/branch-plan-issue-122-core-participant-registration.md` per the
AGENTS.md workflow.
