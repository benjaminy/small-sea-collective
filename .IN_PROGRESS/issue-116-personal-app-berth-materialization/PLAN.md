# Branch Plan: Real Materialization of Personal (NoteToSelf) App Berths

**Branch:** `issue-116-personal-app-berth-materialization`
**Base:** `main`
**Primary issue:** #116 "Design real NoteToSelf app berth materialization"
**Kind:** Architectural cleanup + small implementation slice.
Likely to spawn at least one follow-up.

**Predecessor context:**
This is a direct follow-up from #111.
That branch shipped two-level app registration (participant + team) and resolved D1/D2 toward local-ID-plus-`app_unification`.
On the participant side, `register_app_for_participant` currently writes the `app` and `team_app_berth` rows to `NoteToSelf/Sync/core.db` and creates an **empty** directory at `NoteToSelf/{AppName}/`.
That empty directory is the stub this branch removes and replaces with a clearer rule:
Small Sea provisions app access, but each app owns its own local materialization tree.

**Related code of interest:**
- `packages/small-sea-manager/small_sea_manager/provisioning.py` — `register_app_for_participant` (creates the stub) and `activate_app_for_team` (does *not* create any per-berth directory on the team side).
- `packages/small-sea-hub/small_sea_hub/backend.py` — `_resolve_berth`, which today only ever opens `{team}/Sync/core.db`, never a per-app-berth file.
- `packages/small-sea-note-to-self/small_sea_note_to_self/db.py` — owner of `NoteToSelf/Sync/` and `NoteToSelf/Local/` layout.
- `packages/shared-file-vault/shared_file_vault/sync.py` — current Vault sync, whose local app storage should remain Vault-owned rather than Manager-owned.
- `architecture.md` Berth definition (§9), Manager spec §App Management, Hub spec §Berth resolution.

## Purpose

After #111, an app registered for a participant has a row in NoteToSelf and an empty directory on disk.
Nothing in the framework writes to that directory.
No app reads from it.
No sync mechanism touches it.

This branch's purpose is to decide what that directory **is for**, and to land the smallest concrete thing that proves the answer.

The Phase 0 conclusion is that the directory is a mistake of ownership, not an unfinished framework feature.
`NoteToSelf x App` is a real berth, but in the context of a specific app the app coordinate is already fixed, so the berth projects to a team scope.
The framework should expose stable participant/team/app/berth identity through Hub sessions and Manager-controlled registration state; it should not create app working trees inside Manager-owned NoteToSelf storage.
Today the Hub's internal `SmallSeaSession` records `participant_id`, `team_id`, `app_id`, and `berth_id`.
The public `/session/info` endpoint exposes `participant_hex` and `berth_id`, plus friendly names; this is enough to avoid friendly-name-only paths if an app keys by berth, but a fuller app-home helper may want `team_id` and `app_id` exposed explicitly.

A typical app-owned layout may look like:

```text
{AppHome}/
  ... app-global files ...
  SmallSeaParticipants/
    {participant_id}/
      ... participant-scoped app files ...
      Teams/
        {team_id}/
          Sync/
          Local/
```

Those path components should use stable, opaque IDs rather than display names.
Friendly names remain UI labels and routing hints, not durable filesystem identity.

Specifically we want to answer:

1. What is the ownership boundary between Manager-controlled registration state and app-owned local materialization?
2. What stable session metadata does an app need to map a Small Sea berth to its own local filesystem tree?
3. What code should stop creating Manager-owned app directories under `NoteToSelf/`?

Whichever answer we land, the framework should stop shipping an empty stub directory whose meaning is undefined.

## Why This Plan Needs To Be Strict

Empty stubs are the easiest place to accidentally invent durable semantics.
If we leave the directory as-is, the next app that needs personal state will pick a convention by accident — write a SQLite file there, push it through NoteToSelf's git repo, and now we have a de-facto schema that nobody designed.

The branch should be strict about three things:

1. The decision about filesystem ownership is made before any app writes under the old `NoteToSelf/{AppName}/` stub.
2. Stable IDs, not display names, are the path basis for app-owned materialization guidance.
3. The Manager's historical overreach is cleaned up without replacing it with a new framework-owned app-data tree.

## Branch Contract (v1 slice)

The branch is successful if all of the following are true:

1. The repo has one written answer to "who owns app berth materialization?" recorded in the Manager spec and `architecture.md`.
2. `register_app_for_participant` no longer creates a stub whose meaning is undefined.
   It records registration state only; app data trees live under app-owned homes.
3. The team-side question is answered symmetrically: Manager does not create `{Team}/{AppName}/` either.
4. At least one micro test proves participant registration writes the expected DB rows and creates no `NoteToSelf/{AppName}/` artifact.
5. Hub/session metadata is audited and documented: session rows already carry stable IDs, while `/session/info` currently exposes `participant_hex` and `berth_id` plus friendly names.
6. Vault is the canary that nothing in-tree relies on the old stub.
7. The AppHome layout guidance is explicitly documented as a normative convention, not as code exercised by an in-tree consumer on this branch.

Everything else justifies itself by serving that loop.

## Phase 0 Decisions To Freeze Before Coding

### D1. Who owns app berth materialization?

Rejected earlier answers:

- **D1.A — Framework-owned personal sync area.**
  `NoteToSelf/{App}/Sync/` is a real git repo paralleling `NoteToSelf/Sync/`.
  The framework guarantees the repo exists, is initialized, and is included in NoteToSelf-style transport so the app's personal state syncs across the participant's devices.
  The app owns the contents (its own DB or files) but the framework owns the directory and the transport.
- **D1.B — Logical-only berth.**
  The participant-app `team_app_berth` row exists in NoteToSelf DB, but the framework creates no on-disk artifact under `NoteToSelf/{App}/`.
  Apps that want per-participant personal storage choose their own location and transport, just as Vault does today with its vault root.
  The framework's job is to record "this app is registered for this participant" and nothing more.
- **D1.C — Hybrid: directory only, contents app-owned.**
  Framework creates `NoteToSelf/{App}/` as a stable per-app working directory but does not create `Sync/` or any transport.
  Apps that want it can use it; apps that don't ignore it.
  This is closest to today's stub, formalized.

Open consideration: D1.A is the most useful long-term, but it introduces a second sync surface inside NoteToSelf with implications for cloud storage, conflict resolution, and the Hub's berth resolution path.
D1.B is honest but means we should delete the empty directory creation.
D1.C is the path of least change but risks becoming the de-facto schema by accident.

Chosen answer:

- **D1.D — App-owned materialization.**
  The participant-app berth is real, but the framework does not materialize it inside `NoteToSelf/`.
  Each app owns its app home and maps Small Sea session identity into its own tree.
  In that context, "berth" projects to "team" because the app coordinate is already fixed.
  The framework owns registration, activation, Hub authorization, and stable session metadata; the app owns local `Sync/` and `Local/` folders for its own participant/team scopes.

Phase 0 resolution: **D1.D**.

This is an axis shift, not a fourth version of the old directory-placement question.
D1.A/D1.B/D1.C asked where Manager should put an app directory.
D1.D says Manager should not put an app directory anywhere; apps materialize their own local trees.

Rationale:

- `NoteToSelf x App` is an ordinary berth whose team happens to be the participant's personal team.
- Teams remain prior to apps for authority and registration decisions, but local app storage is viewed from inside an app, where the app coordinate is fixed.
- Manager is itself just an app with a privileged provisioning role. Its `NoteToSelf/Sync/core.db` tree is Manager/Core storage, not the universal place all apps must materialize their data.
- The Hub is allowed to read Manager/Core's `NoteToSelf/Sync/core.db` and team `Sync/core.db` files as part of the framework contract. That does not generalize to arbitrary app homes; apps should not expect the Hub to discover app data by filesystem snooping.
- Friendly app and team names are not stable durable identity; app-owned paths should use opaque participant/team IDs, with friendly names reserved for labels.
- This deletes the undefined stub without giving up future per-participant cross-device app state. Apps can put that state under their own `Teams/{note_to_self_team_id}/Sync/` or equivalent.

### D2. Team-side symmetry

Earlier framing asked whether `activate_app_for_team` should create `{team}/{App}/` if participant registration creates `NoteToSelf/{App}/`.
Under D1.D, that is the wrong question: neither path is Manager-owned app storage.

Rejected earlier answers:

- **D2.A — Symmetric.** Both sides materialize the same shape.
- **D2.B — Asymmetric, intentional.** Team-side berth contents are already in cloud storage (per-berth bucket), so no local directory is needed; only personal-side gets a local directory.
- **D2.C — Defer team side to a follow-up.** This branch covers only the participant side; team-side gets an explicit issue.

Phase 0 resolution: **D2.A in ownership terms, not in Manager-created paths.**

Manager creates no app-owned filesystem artifact for either participant-level registration or team-level activation.
The same app-owned convention covers both:
inside `{AppHome}/SmallSeaParticipants/{participant_id}/Teams/{team_id}/`, `NoteToSelf` is simply the participant's personal team.
The team-side storage surface is therefore not a separate Manager question.

### D3. Sync transport for app-owned berths

- **D3.A — App-owned local tree, Hub-mediated transport.**
  Apps keep local `Sync/`/`Local/` storage under their own app home and use Hub sessions to move berth data.
  The exact helper API can evolve separately from the deletion of the Manager-owned stub.
- **D3.B — Framework-created per-app repos under NoteToSelf.**
  Rejected with D1.A because it makes NoteToSelf/Core storage the app-data parent.

Phase 0 resolution: **D3.A as the architectural direction; no new transport implementation in this branch.**

This branch should not invent a cross-device app-sync helper just to justify deleting the stub.
The metadata audit is mostly complete: Hub sessions already carry stable participant/team/app/berth IDs internally, and `/session/info` already exposes `participant_hex` and `berth_id`.
This branch should file a focused follow-up only if we decide apps need a convenience helper or explicit `team_id`/`app_id` fields in `/session/info`.

### D4. Framework-managed schema?

Does the framework write any file for an app at registration or activation time?

- **D4.A — No app data files.** Manager writes registration/activation DB rows only.
- **D4.B — Framework creates a minimal manifest for apps.** Rejected for this branch; a helper library can offer this inside the app's own home later.

Phase 0 resolution: **D4.A**.

### D5. What does Vault actually need from this?

Vault is the only real app in-tree and is the natural canary.
We should answer: does Vault have personal state that wants to sync across the user's devices?

Working hypothesis: Vault's current personal state is mostly its CLI/web config plus the local vault root path.
That state is *device-local*, not cross-device-personal.
Future Vault features such as per-participant niche subscriptions, conflict-resolution preferences, or cross-device app UX state can live under Vault's own app home, keyed by participant and team IDs.
Vault's current single-root model predates this convention and does not yet have multi-participant app-home ergonomics.
When Vault grows that shape, it should adopt the AppHome layout instead of putting personal state under `NoteToSelf/SharedFileVault/`.

Vault should not use `NoteToSelf/SharedFileVault/`.
If this branch adds any Vault check, it should prove Vault continues to operate without that directory.

## Working Direction

The resolved direction is:

- **D1.D**: app-owned materialization. Small Sea registration creates berths; apps decide their own app-home filesystem trees.
- **D2 ownership symmetry**: Manager creates no app-owned folder for either participant registration or team activation.
- **D3.A**: app-owned local `Sync`/`Local` conventions use stable session IDs and Hub-mediated transport; helper work can follow separately.
- **D4.A**: no framework-created app files or manifests in this branch.
- **D5**: Vault remains app-owned and does not touch `NoteToSelf/SharedFileVault/`.

This reframes the branch from "define a personal NoteToSelf app directory" to "remove Manager-owned app materialization and document app-owned materialization."
The implementation remains intentionally small: delete the stub creation, update tests, and record the storage boundary clearly.

## Branch Cut Line

**Must land on this branch**

- A written decision (with rationale) for D1.D, D2 ownership symmetry, D3.A, and D4.A in `architecture.md` and `packages/small-sea-manager/spec.md`.
- A deliberate architecture-doc reframe: globally a berth remains `Team x App`; inside a specific app it projects to participant/team-local materialization.
- The AppHome layout guidance as normative documentation, with an explicit note that no in-tree consumer implements it yet.
- The code change that implements the deletion side of the decision in `register_app_for_participant` and first-participant bootstrap.
- One or two micro tests proving the new shape.
- Removal of the empty-directory creation, and a regression test that the directory is *not* created.
- A session-metadata note: internal sessions already have stable IDs; `/session/info` exposes `participant_hex` and `berth_id`; adding explicit `team_id`/`app_id` or an app-home helper is follow-up unless it proves tiny and necessary.
- Spec/doc sweep that lands the decision in prose.

**First things to cut**

- Any generic app-home helper API beyond a tiny session-metadata audit.
- Any Vault-side migration into a new app-home tree.
- Any cross-device propagation tests for app-owned storage.

## Phasing (draft)

**Phase 0 — Resolve D1/D2/D3/D4/D5 in this document.**
Exit gate: the plan names D1.D, D2 ownership symmetry, D3.A, and D4.A as the chosen direction, and the working direction is consistent across the spec sweep, code change, and tests.

**Phase 0.5 — Failing micro test skeleton.**
Add the test(s) the branch will turn green before writing implementation.
The test asserts that `register_app_for_participant` leaves `NoteToSelf/{App}/` absent and the participant DB rows present.
It should also update the existing Core registration test so first-participant bootstrap does not create `NoteToSelf/SmallSeaCollectiveCore/`.
Exit gate: the new tests fail on `main` and the plan references them by name.

**Phase 1 — Implement the chosen direction.**
- Remove `app_dir.mkdir(...)` from `register_app_for_participant`.
- Remove the `SmallSeaCollectiveCore` directory creation from `create_new_participant`.
- Before removing the Core directory creation, run a grep audit and record the result in the branch wrap-up:
  `SmallSeaCollectiveCore` appears as an app name in DB queries and tests, but no runtime code opens `NoteToSelf/SmallSeaCollectiveCore/` as a filesystem path.
- Audit callers/tests for incidental reliance on `NoteToSelf/{AppName}/` directories.
- Leave the participant DB write path and NoteToSelf `Sync/core.db` repo commits alone.
Exit gate: implementation is a small mechanical change scoped to the creation sites and tests.

**Phase 2 — Spec sweep.**
- `architecture.md`: update the App/Berth and App Bootstrap sections to distinguish registration/authorization from app-owned materialization.
- `packages/small-sea-manager/spec.md` §App Management: record that participant registration writes DB rows only and does not create app data directories.
- `packages/small-sea-hub/spec.md`: record the stable-ID metadata boundary if we add `/session/info` fields, or note in the plan wrap-up that the current public boundary is `participant_hex` + `berth_id`.
Exit gate: a skeptical reader can read the spec and answer "where should an app put local participant/team data?" without reading code.

**Phase 3 — Follow-ups filed.**
- File a focused follow-up for explicit `team_id`/`app_id` in `/session/info` or an app-home/session helper only if the spec sweep decides `participant_hex` + `berth_id` is too thin for app-owned materialization ergonomics.
- File no team-side materialization issue unless the spec sweep reveals a real asymmetry.

## Validation Strategy (smart-skeptic test)

A skeptical reviewer should be able to convince themselves of all the following without leaving the repo:

**The undefined stub is gone.**
- `rg` over the repo for `NoteToSelf/{App}` style paths shows no framework code creating a per-app directory.
- `rg` for `NoteToSelf/SmallSeaCollectiveCore` and `NoteToSelf/SharedFileVault` finds no runtime path creation.
- The Manager spec answers "what does participant registration create on disk?" in prose.
- A reviewer reading the new micro test sees the expected shape asserted explicitly, not inferred.

**App-owned materialization is explicit.**
- `architecture.md` says a berth is still `Team x App` globally, but from inside an app it projects to a participant/team scope.
- Docs recommend stable opaque IDs for app-owned path components, not friendly names.
- The Manager's own `NoteToSelf/Sync/core.db` is described as Core/Manager storage, not as the parent for every app's data.
- No in-tree consumer exercises the AppHome layout in this branch. The layout is normative guidance; the first real consumer or helper branch should provide the executable proof.
- Docs acknowledge that Hub reads of Manager/Core DB files are framework-specific and do not imply that arbitrary app homes are Hub-readable.

**No app is silently relying on the old stub.**
- Vault tests still pass without depending on `NoteToSelf/{App}/` existing.
- A grep over `packages/` for reads from `NoteToSelf/[A-Z]` paths finds no app data consumers.

**Hub berth resolution is unaffected.**
- The Hub's `_resolve_berth` reads only `{team}/Sync/core.db`.
- Changing the personal-berth on-disk shape must not change which file the Hub opens for `NoteToSelf` sessions (`NoteToSelf/Sync/core.db`).
- A micro test or spec note records this invariant.

**Pre-alpha guardrails.**
- No migration shims are introduced for the empty-directory change; pre-alpha rules apply (AGENTS.md).
- No new framework app-data schema is introduced on this branch.

## Non-Negotiable Invariants

1. The Hub continues to never write to participant or team DBs.
   This branch does not change Hub write paths.
2. Manager remains the sole writer of `core.db` in NoteToSelf and team DBs.
   The branch removes Manager writes under `NoteToSelf/{App}/`.
3. App data ownership: app-owned local materialization lives under the app's own home, not under Manager/Core's NoteToSelf tree.
   The framework provides registration, authorization, and stable IDs; it does not introduce an app-data schema.
4. No backward-compat shims for the existing empty-directory behavior.
   Pre-alpha freedom (AGENTS.md) lets us delete the `mkdir` outright.

## Risks and Open Questions

- **Hidden consumer of the empty directory.**
  If any test or app fixture relies on `NoteToSelf/{App}/` existing as a directory, deleting it will surface that reliance.
  Mitigation: Phase 1 audit step.
- **Thin public session metadata.**
  Internal sessions have stable team/app/berth IDs, but `/session/info` currently exposes only `participant_hex` and `berth_id` as opaque stable IDs.
  Mitigation: keep this branch to the ownership cleanup, and file a focused follow-up if explicit `team_id`/`app_id` or an app-home helper is needed.
- **Spec drift between Manager spec and architecture.md.**
  Touching the Berth concept means two docs must agree.
  Mitigation: Phase 2 explicit step, reviewer can diff both sections.
- **Overcorrecting into app isolation.**
  App-owned storage must not mean apps bypass the Hub for Small Sea internet traffic.
  Mitigation: docs say local app trees are local materialization only; sync and cloud access remain Hub-mediated.

## Sub-Issues To Spawn (placeholder)

To be populated as Phase 0 resolves:

1. Explicit `team_id`/`app_id` in `/session/info` or an app-home/session helper, if `participant_hex` + `berth_id` proves too thin for app ergonomics.
2. App-owned sync ergonomics for per-participant personal state, when Vault or another app has a concrete first use.
