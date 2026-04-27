# Branch Plan: Migrate Core sessions onto participant-level app registration

Tracks GitHub issue #122 — follow-up to issue #111.

## Goal

Register `SmallSeaCollectiveCore` through the same participant-level
registration + team-level activation paths used by ordinary apps, and remove
the hardcoded Core exception in Hub berth resolution.

After this branch:

- The Hub's `_resolve_berth` treats Core like any other app.
- Manager provisioning routes Core's app/berth rows through the same helpers
  (`register_app_for_participant`, `activate_app_for_team`) used by Shared
  File Vault and any future Small Sea app.
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

### 1. Route Core's NoteToSelf registration through `register_app_for_participant`

Refactor `_initialize_user_db` so Core's participant-level entry is created by
calling `register_app_for_participant` (or a private shared primitive that
both share). Constraints to respect:

- `register_app_for_participant` currently expects the NoteToSelf `team` row
  to already exist and the `NoteToSelf/Sync` git repo to be initialized so it
  can commit. `_initialize_user_db` runs before that repo's first commit, so
  the simplest path is:
  1. Keep `_initialize_user_db` responsible for creating the NoteToSelf team
     row, device key, and initial git repo.
  2. After the initial NoteToSelf commit, call
     `register_app_for_participant(root_dir, participant_hex, "SmallSeaCollectiveCore")`
     instead of the current inline INSERTs.
- Alternative: factor out a `_register_app_for_participant_in_conn(conn, ...)`
  helper that does only the SQL portion; have `register_app_for_participant`
  open its own connection and commit, and have `_initialize_user_db` reuse the
  conn-level helper inside its existing transaction. Pick whichever produces
  the smaller diff and clearer ownership boundary.

### 2. Route Core's team-creation berth through the activation primitive

In `create_team`, replace the direct `INSERT INTO app` /
`INSERT INTO team_app_berth` / `INSERT INTO berth_role` block with the same
primitive `activate_app_for_team` uses internally.

Caveat: `activate_app_for_team` reads `_core_berth_role(conn, member_id)` to
decide each member's role on the new berth. For Core itself this is
circular — there is no Core berth yet. Resolve by extracting a shared
`_activate_app_for_team_in_conn(conn, app_name, role_for_member)` helper
that takes an explicit role-resolver callable:

- `activate_app_for_team` passes `_core_berth_role` (existing behavior).
- `create_team` passes a constant resolver returning `"read-write"` for the
  team creator.

This keeps the SQL shape identical between Core's first activation and any
other app's activation, while letting the role decision differ.

### 3. Cover the invitation path

Decide whether `accept_invitation` should also call
`register_app_for_participant` for Core when joining an existing team. Two
options:

- **No-op (preferred for this branch):** rely on the existing global
  NoteToSelf/Core participant-level entry created at user-db-init.
  `_resolve_berth`'s participant-berth check already finds it because the
  lookup is keyed on `app_id` without `team_id`.
- **Per-team participant entry:** call `register_app_for_participant` after
  accept to create a `NoteToSelf/SmallSeaCollectiveCore/{team}` directory.
  Reject this if it duplicates state without changing observable behavior.

Document the decision in this plan once made; if "no-op," explicitly note in
the `accept_invitation` body why no extra call is needed.

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

- Add a micro test in `packages/small-sea-hub/tests/test_app_bootstrap.py`
  (or sibling) asserting that, after `create_new_participant`, the Core entry
  in NoteToSelf was created by the same code path that `register_app_for_participant`
  uses — verified by matching the side effect: `NoteToSelf/SmallSeaCollectiveCore/`
  directory exists and the `app` + `team_app_berth` rows are present. (If the
  shared primitive is the only writer of those rows, that side effect uniquely
  proves the path.)
- Add a micro test that, after `create_team`, the Core berth + creator's
  read-write `berth_role` row were created by the shared activation primitive
  by asserting the same row shape `activate_app_for_team` produces for a
  non-Core app. The role resolver difference is the only intentional variation.
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
- Add a regression micro test: with a hand-crafted participant that has *no*
  participant-level Core entry (simulate by deleting the row after
  participant creation), a Core session request must now produce the same
  `participant_berth_missing` rejection as Vault would. This proves the
  branch removed the bypass rather than merely relocating it.

### Goal 3 — End-to-end manager + hub micro tests still pass

Run the broader manager/hub suites to catch any coupling we missed:

- `pytest packages/small-sea-manager/tests`
- `pytest packages/small-sea-hub/tests`
- `pytest packages/shared-file-vault/tests`

## Integrity Checks

To convince a skeptical reviewer that repo integrity is maintained or improved:

- **Coupling decreases:** removing the Core exception eliminates one
  app-name-aware branch from the Hub; the Hub becomes truly app-agnostic in
  its session path. This is a net reduction in coupling between Hub and the
  set of known apps.
- **Single source of truth for "register an app" / "activate an app":** all
  callers — user-db-init, create_team, accept_invitation (if updated), and
  Manager UX flows — go through one helper each. Future apps inherit this for
  free.
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

When the branch is ready to land:

1. Update this plan with the actual decisions made (especially the
   `accept_invitation` choice in step 3 and which factoring approach was
   used in step 1).
2. Move it to `Archive/branch-plan-issue-122-core-participant-registration.md`
   per the AGENTS.md workflow.
