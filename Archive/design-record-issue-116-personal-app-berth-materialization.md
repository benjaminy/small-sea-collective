# Design Record: Personal App Berth Materialization

**Issue:** #116 — Design real NoteToSelf app berth materialization
**Branch:** `issue-116-personal-app-berth-materialization`
**Predecessor:** #111 (two-level app registration shipped; left an empty `NoteToSelf/{AppName}/` stub)
**Spawned follow-up:** #130 (first app-owned materialization consumer integration)

## What this branch decided

The empty `NoteToSelf/{AppName}/` directory created by `register_app_for_participant` after #111 was a mistake of ownership, not an unfinished framework feature.
This branch deletes the stub, declines to replace it with a framework-managed app data tree, and records the ownership boundary in docs.

The architectural framing that resolved it:

- Globally, a berth is `Team x App`.
- A participant is **not** a third berth coordinate.
  It is the local holder of access to berths through identity and team membership.
- From inside a specific app, the app coordinate is already fixed, so local materialization usually projects to a participant context containing team scopes.
- The framework provides registration, authorization, and stable IDs.
  The app owns its local materialized tree.

Manager is itself just an app with a privileged provisioning role.
Its `NoteToSelf/Sync/core.db` tree is Manager/Core storage, not the universal place all apps must materialize their data.
The Hub is allowed to read Manager/Core's `NoteToSelf/Sync/core.db` and team `Sync/core.db` files by framework contract; that exception does not generalize to arbitrary app homes.

## Interesting choices a future developer might revisit

### Why we rejected a framework-managed personal sync area (D1.A)

The most useful long-term answer would arguably be: `NoteToSelf/{App}/Sync/` is a real git repo paralleling `NoteToSelf/Sync/`, framework-initialized, framework-synced across the participant's devices.
This is genuinely more useful than what we shipped — it gives apps a free cross-device personal-state surface.

We didn't ship it because:

- No in-tree app needs it yet.
  Vault's current personal state is device-local (CLI config, vault root path).
- Once shipped, it becomes load-bearing and hard to remove if the schema decisions were wrong.
- The burden of proof should be on adding framework surface, not on keeping a stub.

Revisit when a concrete app demonstrates cross-device personal state that cannot be cleanly handled with app-owned storage plus Hub-mediated transport.

### Why the AppHome layout is illustrative, not normative

PLAN.md shows an example `{AppHome}/SmallSeaParticipants/{participant_id}/Teams/{team_id}/Sync|Local/` layout but explicitly does **not** make it a framework contract.

The principle is normative:
any Small Sea-derived path component should use stable opaque IDs (hex strings from `/session/info`), not friendly names, and participant contexts must be isolated.

The exact naming is not normative because:

- Apps should be free to use OS-standard app data locations (`~/Library/Application Support`, `%LOCALAPPDATA%`, `~/.local/share`).
- We have no in-tree consumer to road-test the specific names.
- Standardizing names without a tested consumer would ossify a convention by accident.

If a future helper library wants to standardize, it should be a deliberate decision driven by a real consumer.

### Why team-side symmetry resolved to "no Manager action needed"

Earlier framing asked whether `activate_app_for_team` should create `{team}/{App}/` to match the participant-side stub.

`activate_app_for_team` already creates no per-app directory — it writes only `app`, `team_app_berth`, and `berth_role` rows, then commits the team's `Sync/core.db`.
Under app-owned materialization, that's the correct shape on both sides.
The symmetry exists in ownership terms; it does not require new Manager-created paths.

### Hub session metadata audit

Internal `SmallSeaSession` rows carry `participant_id`, `team_id`, `app_id`, and `berth_id` as opaque bytes.
Public `/session/info` exposes `participant_hex` and `berth_id` as hex strings, plus friendly `team_name` and `app_name` for UI display.

This is enough for an app to key its local materialization off Hub session info today without inventing a new metadata channel.
Explicit `team_id`/`app_id` in `/session/info` was deferred (conditional follow-up not filed; revisit when a real consumer hits friction).

### Pre-Phase-1 grep audit (recorded here per Phase 1 contract)

Before removing the `NoteToSelf/SmallSeaCollectiveCore/` directory creation in `_initialize_user_db`, audited the repo for runtime path reads:

- The string `SmallSeaCollectiveCore` is widespread — it appears as an **app name** in DB queries (`WHERE a.name = 'SmallSeaCollectiveCore'`) and in test fixtures (`"app": "SmallSeaCollectiveCore"`).
- No runtime code opens `NoteToSelf/SmallSeaCollectiveCore/` as a **filesystem path**.
  The only path-construction site was the deleted `mkdir` call itself.
- Hub berth resolution for NoteToSelf sessions opens `NoteToSelf/Sync/core.db`, not `NoteToSelf/SmallSeaCollectiveCore/`.

Deletion is safe.
The same audit shape would apply to `SharedFileVault`: app name appears as data, no filesystem path reads.

## What this branch deliberately did not do

- No framework-managed cross-device personal sync surface.
- No new app-home helper API or app-bootstrap convenience library.
- No `/session/info` schema extension (the metadata audit found existing fields sufficient).
- No Vault migration into a new app-home tree.
- No normative AppHome directory naming.

## Process notes for future archaeologists

- **Red-test-first discipline collapsed into a single implementation commit.**
  Phase 0.5 specified that deletion-regression tests should land red on the branch before implementation.
  In practice, tests and implementation landed together in `ca42cd9`.
  The end state is correct (tests verify the absence of the deleted directories paired with DB-state assertions), but git history does not show the red phase.
- **Conditional sub-issues deliberately not filed.**
  Plan's sub-issues 1 (explicit `team_id`/`app_id` in `/session/info`) and 3 (cross-device personal sync ergonomics) were both conditional on a concrete need.
  Spec sweep landed a metadata-boundary paragraph without proposing new fields; no in-tree consumer needs cross-device personal sync.
  Both sub-issues remain in the plan as conditional placeholders; #130 is the only filed follow-up.

## Validation summary

- Full affected suites: 248 passed, 3 skipped (pre-existing) across `small-sea-manager`, `small-sea-hub`, `shared-file-vault` tests.
- Code grep for `NoteToSelf/SmallSeaCollectiveCore` or `NoteToSelf/SharedFileVault` filesystem paths in `packages/`: no hits.
- Code grep for `NoteToSelf.*mkdir` in `packages/`: only legitimate `NoteToSelf/Sync` and `NoteToSelf/Local` framework directories.
- Doc grep across `architecture.md`, `packages/*/spec.md`: only updated mentions describing the removal or the new ownership boundary.
- Hub `_resolve_berth` continues to open only `{team}/Sync/core.db`, unchanged by this branch.

## Files touched

| File | Change |
| --- | --- |
| `packages/small-sea-manager/small_sea_manager/provisioning.py` | Removed two `mkdir` calls (one in `_initialize_user_db`, one in `register_app_for_participant`). DB writes and `NoteToSelf/Sync/core.db` git commits left intact. |
| `packages/small-sea-manager/tests/test_create_team.py` | Flipped existing Core test from `is_dir()` to `not exists()`; added new test for `register_app_for_participant`. |
| `architecture.md` | Added concept clarification paragraph (Core Concepts) and App Bootstrap section paragraph distinguishing registration/authorization from app-data materialization. |
| `packages/small-sea-hub/spec.md` | Added paragraph documenting `/session/info` public metadata boundary. |
| `packages/small-sea-manager/spec.md` | Updated §App Management bullet, added clarifier paragraph, reframed open-questions row from "NoteToSelf/{App} berths" to "App-owned materialization". |
