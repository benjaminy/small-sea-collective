# Branch Plan: App Bootstrap via Hub Sightings and Manager Registration

**Branch:** `issue-111-app-bootstrap-sightings`
**Base:** `main`
**Primary issue:** #111 "Design app bootstrap via Hub unknown-app sightings and Manager registration"
**Kind:** Design + first vertical implementation slice. Expected to spawn several follow-up issues.
**Related issues:** #8 (Hub read-only API for app self-configuration), #6 (identity model for NoteToSelf and multi-device), #5 (SharedFileVault — wire push/pull sync through the Hub)
**Related code of interest:**
`packages/small-sea-hub/small_sea_hub/backend.py` (`_resolve_berth`, `request_session`),
`packages/small-sea-hub/small_sea_hub/server.py` (`/sessions/request`, `/session/info`),
`packages/small-sea-manager/small_sea_manager/provisioning.py` (app and berth creation),
`packages/small-sea-manager/small_sea_manager/sql/`,
`packages/small-sea-note-to-self/small_sea_note_to_self/sql/`,
`packages/shared-file-vault/shared_file_vault/sync.py` (`_HUB_APP_NAME`),
`packages/shared-file-vault/shared_file_vault/web.py` (session opens),
`architecture.md`, `packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`.

## Purpose

Today the Hub treats an unknown app or unconfigured berth as a flat `404 Not Found` from `_resolve_berth`. The app has no language to ask "am I supposed to exist for this identity?", and the human has no path to register a new app other than implicit Manager-side magic. Bundled apps like Shared File Vault have side-stepped the question by impersonating `SmallSeaCollectiveCore`, which collapses the per-app berth model and gives Vault implicit access to the team's Core berth.

This branch establishes the durable shape of app bootstrap:

- Apps may only **request** sessions. They never write registration state.
- The Hub is the **observation point**: it durably records unknown-app sightings and returns structured, distinguishable rejection reasons.
- The Manager is the **provisioning authority**: it consumes Hub sightings, syncs identity and team state, and decides what gets registered or activated.
- App registration is **two-level**: identity-level ("this app exists for this participant on this device") and team-level ("this app may access this team's resources"). They are independent; even if a team uses an app, it's possible for a participant to reject it on a particular device. This distinction is important because a user should be able to control at least at the berth granularity, what data is cloned on any particular device.
- App friendly names are **not global identity**. A request for `SharedFileVault` is a local claim made by a client, not proof that every other `SharedFileVault` in the world is the same app. When independently-created worlds collide, Manager must preserve the distinction until a human or team explicitly unifies them.
- "Bundled" apps are not special at runtime. Vault becomes an ordinary Small Sea app using the friendly name `SharedFileVault` and walks the same path as any third-party app would.

This branch is expected to be larger than recent ones. The goal is to land the design in writing, the schema sketches required to make the design testable, and one vertical implementation slice — Vault as a non-impersonating app — with explicit follow-up github issues for the rest.

## Why This Plan Needs To Be Strict

This branch establishes the trust and discovery boundary between apps, the Hub, and the Manager. A loose implementation would be worse than the current `404` behavior because it could quietly grant apps a new authority surface (sighting injection, registration coercion) without anybody noticing.

So this plan optimizes for three things:

1. The two-level registration model is reflected in concrete schema and in the Hub's rejection language, not just in prose.
2. The Hub-Manager boundary remains clean: the Hub records and reports; the Manager decides and writes. Apps never read Manager DBs directly.
3. Vault's de-impersonation is end-to-end, not surface-level. The micro tests should fail loudly if Vault could still get a session as `SmallSeaCollectiveCore`.

## Branch Contract (v1 slice)

This branch should behave like **one bounded vertical slice**, not like an attempt to finish the whole future app-bootstrap platform in one pass.

The branch is successful if all of the following are true in one end-to-end path:

1. `SharedFileVault` asks the Hub for an ordinary berth session as itself.
2. The Hub refuses with a structured, machine-distinguishable reason and records the sighting durably.
3. The Manager can observe that sighting and provision the missing state in two explicit steps: identity registration, then team activation.
4. Vault retries the same ordinary session flow and succeeds.

Everything else in this branch should justify itself by making that loop implementable, observable, or reviewable. If a task does not clearly serve that loop, it should default to a follow-up issue rather than expanding this branch.

## Phase 0 Decisions To Freeze Before Coding

The opening of this plan is too wide if the branch starts coding before these are locked. Treat these as the Phase 0 outputs that must be written down before implementation begins:

1. **Wire contract for `POST /sessions/request`.**
   Success continues to return `200` with either `{pending_id}` or `{token}` as today.
   Bootstrap/configuration failures stop pretending to be plain "not found"; they return a structured rejection body with a stable machine field for the reason.
   `404` should be reserved for truly unknown participant/team lookups, not "Manager action may fix this."
2. **Stable v1 rejection reasons.**
   The branch should pick one exact vocabulary for the first shipped slice and use it consistently in Hub code, specs, and micro tests.
   At minimum the vocabulary must cover: app unknown, identity registration missing, and team activation missing.
   `transient_sync_may_resolve` only ships if Phase 0 can name one precise Hub-observable predicate for it; otherwise it is cut from v1 rather than becoming a catch-all.
3. **Manager API shape.**
   The implementation should expose two clearly named operations, not one kitchen-sink helper with flags:
   `register_app_for_identity(...)`
   `activate_app_for_team(...)`
4. **Minimum durable sighting record and dedupe key.**
   The plan should freeze the exact v1 fields before migration work starts. Minimum useful shape:
   `participant_hex`, `app_name`, `team_name`, `client_name`, `first_seen_at`, `last_seen_at`, `seen_count`, and machine rejection reason.
   In this table, `app_name` means "requested friendly app name observed from this local client." It is not a global or canonical app identity.
   The table should name its upsert key explicitly so "more or less forever" does not mean one row per retry. Working v1 key: `UNIQUE(participant_hex, app_name, team_name, client_name)`.
5. **Team-level identity model (D2).**
   D2 cannot float into implementation. Before `activate_app_for_team(...)` lands, the branch must choose a generic locally generated app identity shape; Manager provisioning must not contain a Vault-specific or bundled-app-specific identity path.
6. **Discovery scope for this branch.**
   Record whether this branch includes a new Hub read API for app self-configuration, or whether apps only get the structured rejection and the instruction to open Manager.

If any of the six items above remain fuzzy, the branch is still in document-iteration mode, not implementation mode.

### Phase 0 Wire Contract To Freeze

The branch should commit one concrete rejection shape before anyone writes code:

```json
HTTP 409 Conflict
{
  "error": "app_bootstrap_required",
  "reason": "app_unknown",
  "app": "SharedFileVault",
  "team": "ProjectX"
}
```

Where `reason` in v1 is one of:

- `app_unknown`
- `identity_berth_missing`
- `team_berth_missing`

Notes:

- `HTTP 409` is the working choice for bootstrap/configuration failures because the request is well-formed but conflicts with current local provisioning state.
- `404` remains reserved for genuinely unknown participant/team lookups.
- `app` is the friendly app name claimed by the local client in this request. It is not canonical identity and must not be used to silently merge two independently-created apps.
- `team` may be `null` if a future caller triggers the same mechanism outside a team-scoped request, but the shipped Vault slice should always send a concrete team name.
- Manager-owned rejection dispositions are intentionally **not** represented as a separate Hub reason in v1. The Hub keeps reporting the same observation; Manager decides whether the human sees it again.
- `transient_sync_may_resolve` is intentionally **not** in the frozen v1 wire shape unless Phase 0 can define one exact emitting condition in terms of current Hub-observable state. Default if not: do not ship it.

## Red-Test Rule

Before any implementation phase claims progress, Phase 4's end-to-end micro tests should exist as a failing skeleton on the branch.

- Write the main positive-path test and the production-mode negative test first, with stub assertions if necessary.
- Keep them red on the branch until later phases turn assertions green; they do not need to be mergeable in that state, but they must exist early enough to shape the contract.
- Do not land a Hub-only rejection-contract change without the Manager-side wiring that consumes it. At least one red assertion should go green in the same review arc, so "Phase 1 done" cannot mean "new strings nobody reads."

## Non-Negotiable Invariants

The implementation is only acceptable if all of these remain true:

1. Apps never write to NoteToSelf or team DBs. Their only Hub-mutating action remains opening a session and using it.
2. Apps never read Manager DBs directly. Anything an app needs to discover during bootstrap is exposed through a Hub HTTP endpoint, or returned in the structured rejection.
3. The Hub's unknown-app sightings table is local Hub state only. It is never synced to peers and never read by apps.
4. Identity-level registration and team-level activation are separately authorized decisions. The Manager UI/CLI may bundle them in a single flow for convenience, but the underlying data model treats them as distinct.
5. Bundled apps (Vault) get no special path through the Hub or Manager provisioning. They walk the same rejection-and-registration loop as any third-party app. The only acceptable special case is sandbox-mode developer setup that calls the same generic Manager operations a human would approve (see §Sandbox dev escape hatch).
6. Vault stops opening sessions as `SmallSeaCollectiveCore` everywhere — `sync.py`, `web.py`, CLI, and all test fixtures.
7. The shape of the structured rejection is stable enough that an app written today can keep using the same response codes after later branches add finer-grained reasons.
8. Rejection dispositions are Manager-owned in v1. The Hub records observations; it does not read Manager-local rejection tables to mute or reinterpret future sightings.
9. The public Manager API stays split. `register_app_for_identity(...)` and `activate_app_for_team(...)` may share private helpers, but there is no single public entry point with a `level=` or equivalent flag.
10. New Hub write paths in this branch write only to `small_sea_collective_local.db`. Any write to NoteToSelf or a team DB from new Hub bootstrap code is a hard no.
11. Manager sightings refresh is explicit and user-triggered in v1. No background sightings poller ships on this branch.
12. Friendly-name collisions are normal local-first events. If two distinct app identities both present the same friendly name, Manager must preserve both identities and surface a choice; the Hub must not collapse them by string equality.

## Branch Goals

When this branch is done, the repo should provide all of the following:

1. A written design of the two-level registration model (identity-level and team-level), recorded in `architecture.md` and the Manager and Hub specs, with the remaining debates and branch resolutions explicitly named (see §Design Debates and Branch Resolutions).
2. A Hub-side schema for durable unknown-app sightings, with enough fields to support Manager UX and dedupe, including an explicit unique key for repeated requests from the same participant/app/team/client tuple.
3. A Hub-side structured rejection vocabulary for `/sessions/request`, frozen in the plan as a concrete wire contract, covering at least: app unknown, app known personally but no personal berth, and app known personally but not activated for the requested team. `transient_sync_may_resolve` only lands if Phase 0 gives it one precise emitting condition.
4. A Hub-side read endpoint that lets the Manager enumerate sightings as observations. In v1 it does not apply Manager-owned rejection/disposition filtering.
5. Manager-side identity-level registration: writes the new `app` row and `team_app_berth` for the participant's NoteToSelf team, and creates the `NoteToSelf/{App}` directory. The framework does nothing further inside that directory; its contents belong to the app.
6. Manager-side team-level activation: writes the `app` and `team_app_berth` rows in the team DB, plus `berth_role` rows for current members.
7. Manager-side disposition handling for "I don't want this app on this device" (identity-level, in `NoteToSelf/Local/device_local.db`) and "I don't want this app on team T" (team-level, in the per-team Manager-local sidecar DB used today for admission-prompt dismissals). The Hub does not read these tables in v1; Manager uses them when deciding whether to surface a sighting again.
8. Schema sketches for app unification (`app_unification`) as the explicit recovery path for typo, rebrand, and duplicate-friendly-name cases. The branch does not need full unification behavior, but it must not write data that makes later unification impossible.
9. `SharedFileVault` is the Vault's friendly app name for this branch. Vault's `sync.py`, `web.py`, CLI, tests, and fixtures all use `SharedFileVault` and never `SmallSeaCollectiveCore`.
10. An end-to-end micro test that walks the full loop: fresh sandbox → Vault requests session → Hub rejects with structured reason and records sighting → Manager observes sighting → Manager registers Vault at identity level → Manager activates Vault for team T → Vault retries successfully.
11. Optional sandbox-mode dev convenience: a sandbox setup endpoint that registers and activates developer-selected apps (the default fixture may include `SharedFileVault`) without walking the human prompt path. Gated behind the same sandbox flag as `/sessions/pending`, and implemented by calling generic Manager operations.
12. Spec/doc updates so `architecture.md`, the Manager spec, and the Hub spec reflect the shipped two-level model and the new rejection vocabulary, and so the open debates are recorded as explicit open questions.

## Design Debates and Branch Resolutions

Some questions remain open for future work; others are resolved here because the branch cannot safely proceed without an answer.

### D1. Identity-level app identity model

- **D1.A — Friendly name as identity.** App identity is a single string. No new tables. Renaming is a destructive operation handled as uninstall + reinstall.
- **D1.B — Local app ID + friendly name + `app_unification`.** Mirrors the existing `participant_unification` shape. Renaming is non-destructive. Provides a real recovery mechanism for duplicate friendly names, squatting, typos, rebrands, and later certificate-based app identity.

Phase 0 resolution for this branch: **D1.B is the architectural direction.**

Rationale:

- Small Sea has no central app registry, so friendly names cannot be authoritative. Two developers may independently create apps with the same name, and the correct local-first behavior is to preserve both identities until a human or team explicitly unifies them.
- Names are claims, labels, and routing hints. They are not proof of sameness.
- The v1 Vault slice still uses `SharedFileVault` as the requested friendly name, but neither Manager nor Vault gets a private registration path because of that name.

Implementation note for this branch: if full `app_unification` would make the branch too large, keep unification as a schema sketch and issue follow-up, but avoid any deterministic name-derived identity writes that would force two unrelated same-name apps to collapse.

### D2. Team-level app identity model

- **D2.A — Deterministic name-derived IDs.** `app.id = uuid5(team_id, friendly_name)` and `team_app_berth.id` derived in turn. Concurrent activation by two admins converges by primary-key collision under splice-sqlite merge. This is simple, but it incorrectly treats a friendly name as authoritative identity and silently collapses unrelated same-name apps.
- **D2.B — Local app ID + `app_unification` + `berth_storage` indirection.** Symmetric with D1.B. Adds a `berth_id -> bucket_name / topic` indirection table that pays for itself independently (credential rotation, provider migration, compaction). Typos, rebrands, and same-name collisions fall under explicit unification rather than implicit string equality.
- **D2.C — Name-derived bundled-app shortcut.** `SharedFileVault` is treated as a predeclared bundled app handle for the purpose of this branch's vertical slice. Rejected because it would make Manager provisioning know about Vault and would create a second app-registration path before the generic model is honest.

Phase 0 resolution for this branch: **D2.B is the architectural direction. D2.A and D2.C are rejected.**

Rationale:

- `activate_app_for_team(...)` must choose a concrete row-shape now; this is not safely deferrable once synced team DB rows start landing in tests.
- Deterministic name-derived IDs give a cheap convergence story only by assuming a global namespace that Small Sea explicitly does not have.
- Manager is the generic provisioning authority, not a registry of blessed bundled apps. It should not know that Vault exists except as data supplied through the same registration/activation operations used for any app.
- Pre-alpha freedom is best spent avoiding the wrong durable writes in the first place. If this branch cannot land the generic local-app-ID shape, it should stay in plan iteration rather than shipping a Vault-specific identity shortcut.

Open implementation question for Phase 0: decide the minimal local-app-ID row shape needed for the Vault slice. Do not proceed with generic `uuid5(team_id, friendly_name)` writes or any Manager-side `SharedFileVault` special case.

### D3. Discovery endpoint scope (#8 interaction)

- **D3.A — No new read endpoint on this branch.** The structured rejection from `/sessions/request` carries enough information that the app's only response is "tell the human to open Manager." `/info` lands in #8 separately.
- **D3.B — Land a minimal `/info` here.** If the app needs to render a useful UI before sending the human to Manager (e.g. "you're registered for these teams, want to use this one?"), the Hub exposes a small read-only endpoint covering team list and per-app status.

Working decision for this branch: **D3.A**.

The first vertical slice should not add `/info` or `/teams` here. That work already has a natural home in #8, and pulling it into this branch would blur the line between "bootstrap failure reporting" and "general self-configuration API."

If implementation later proves that Vault cannot present a minimally sane "open Manager" flow without more Hub-read context, stop and reopen this plan explicitly rather than quietly expanding scope.

## Scope Decisions Already Made

- Vault's friendly app name is `SharedFileVault`. Not set in stone, but final for this branch.
- Unification implementation is **defined now, implemented later** unless Phase 0 decides more of it is small enough to land in the vertical slice. Local app IDs are part of the branch's generic registration shape; the broader writing/reading behavior for unification may land in follow-up issues spawned from the design.
- "Defined now, implemented later" means prose, not live schema. Any unification design written on this branch lives in `branch-plan.md` and the eventual Phase 6 spec prose, not in `.sql` files or migrations that the build could pick up.
- Team-level app identity is not allowed to rely on name-derived deterministic IDs or predeclared bundled-app handles.
- `NoteToSelf/{App}` berths belong to the app. The Manager creates the directory at registration time and does nothing further. No core-framework schema for it.
- Bundled apps get no special Hub session path and no special Manager provisioning path. The sandbox dev button is the only allowed human-flow shortcut, and it lives behind the sandbox flag; it must call the same generic Manager operations that normal approval would call.
- Rejection-reason transport is HTTP-level structured fields, not a strongly-typed enum. The wire shape just needs to let an app distinguish the relevant cases.
- This branch's first shipped slice is the Vault bootstrap loop. Generic app-self-configuration APIs stay in #8 unless the code proves that separation unworkable.
- Rejection dispositions are Manager-owned in v1. The Hub does not consult Manager-local rejection state when deciding whether to return or record a sighting.
- During implementation, `branch-plan.md` is the decision log. `spec.md` gets updated once in Phase 6 against landed behavior, not incrementally as a design memo.

## In Scope

- New Hub-private SQLite table for unknown-app sightings (additive migration on `small_sea_collective_local.db`).
- Restructuring `_resolve_berth` and `request_session` so they distinguish unknown-app, missing-personal-berth, and missing-team-berth cases, plus a narrowly-defined transient case only if Phase 0 keeps it, and so they record sightings on the unknown branches.
- A Hub HTTP endpoint exposing sightings (read-only) for Manager consumption, with no Hub-side filtering based on Manager-owned dispositions.
- Manager-side identity-level registration and team-level activation as separate operations in `provisioning.py`, plus thin web/CLI exposure.
- Manager-side disposition tables: identity-level rejection in `NoteToSelf/Local/device_local.db`; team-level rejection in the existing per-team Manager-local sidecar DB.
- Renaming Vault's app identifier across all of `shared_file_vault/`, `tests/`, and any sandbox fixtures.
- An end-to-end micro test for the full bootstrap loop (see §Validation).
- Optional sandbox-mode dev endpoint for sandbox app setup.
- Spec/doc updates: `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`.

## Out of Scope (Explicitly Deferred)

- Full implementation of `app_unification` behavior or `berth_storage` indirection beyond schema sketches, unless Phase 0 explicitly chooses to land more of them on this branch.
- Any `.sql` file or migration for unification tables (`app_unification`, `berth_storage`) before Phase 0 explicitly chooses to implement them on this branch or a follow-up branch chooses to implement them.
- Real `NoteToSelf/{App}` materialization beyond an empty directory.
- Cross-device coordination of sightings (sightings are local Hub state, not synced).
- Apprise / additional notification adapters in the OS prompt path.
- Any cryptographic identity for apps (certificate-signed app identity).
- Any UX polish in the Manager web UI beyond what is needed for the end-to-end micro test to be observable.
- Any change to how transport metadata flows for newly registered apps. Transport configuration is B7 territory.
- Hub-side enforcement of which clients are allowed to request which apps (today there is none; this branch does not add any).
- Background polling of Hub sightings from Manager.

## Branch Cut Line

If this branch starts to sprawl, the cut line should be explicit rather than implicit.

**Must land on this branch**

- Structured Hub rejection for app-bootstrap/configuration failures.
- Durable Hub sightings plus a Manager-readable sightings endpoint.
- Separate Manager identity-registration and team-activation operations.
- Vault de-impersonation to `SharedFileVault`.
- At least one end-to-end micro test proving the full bootstrap loop from first rejection to successful retry.
- Spec/doc updates that explain the shipped ownership boundary and rejection vocabulary.
- Review discipline around edge cases: if an edge case threatens scope, file the follow-up issue, link it from a code comment if needed, and do not expand the branch.

**First things to cut if needed**

- Sandbox app setup convenience endpoint.
- Rich disposition UX beyond the minimum plumbing needed to keep repeated prompts from flapping.
- Detailed implementation work for full app unification / berth storage beyond the notes needed to preserve the option space.
- `transient_sync_may_resolve`, unless Phase 0 can define a precise emitting condition that is actually useful.

## Phasing (draft)

Phases are sized for reviewability, not for hard sequencing. Several can proceed in parallel once the schema and rejection vocabulary are settled.

**Phase 0 — This document.**
Iterate on `branch-plan.md` to convergence on rejection vocabulary, registration API shape, app identity model, team-level identity model, and the branch cut line. D1 and D2 reject friendly-name-as-identity as the general model. D3 is fixed to D3.A for this branch.
Exit gate: the plan names one concrete response shape, one concrete reason vocabulary, one explicit sighting dedupe key, one resolved D1/D2 direction, one answer on whether `transient_sync_may_resolve` exists at all, and one cut line for what this branch will refuse to absorb.

**Phase 0.5 — Write the failing micro test skeleton first.**
- Add the Phase 4 positive-path and negative-path tests immediately, even if they only fail on stub assertions at first.
- Put the production-mode negative test first in its file so fixture pollution from earlier tests cannot mask regressions.
- Audit shared fixtures before writing that negative test; in particular, verify no helper pre-registers Vault behind the scenes.
Exit gate: the branch contains a red end-to-end test skeleton that names the expected rejection contract, and the negative test starts from a demonstrably fresh environment.

**Phase 1 — Hub sightings table and structured rejection.**
- Add `unknown_app_sighting` schema to Hub local DB (additive migration via `PRAGMA user_version`).
- Give the table an explicit upsert key for repeat sightings from the same participant/app/team/client tuple.
- Implement sighting bumps as one atomic SQLite `INSERT ... ON CONFLICT DO UPDATE` statement. No read-then-write dedupe logic in Python.
- Refactor `_resolve_berth` to return a typed result distinguishing the rejection reasons in §Branch Goal 3.
- Update `/sessions/request` to record a sighting on rejection paths and return a structured rejection body.
- Add a Hub read endpoint (`/sightings` or similar) that returns sightings as observations only.
Exit gate: Hub micro tests cover success plus each shipped rejection reason, the migration is visibly additive-only, retries upsert one sighting row rather than growing the table unboundedly, and the new code writes only to `small_sea_collective_local.db`.

**Phase 2 — Manager registration and activation.**
- Identity-level registration in `provisioning.py`: writes the NoteToSelf `app` row, the NoteToSelf `team_app_berth`, and creates the `NoteToSelf/{App}` directory.
- Team-level activation in `provisioning.py`: writes the team DB `app` row, `team_app_berth`, and `berth_role` for current members without using name-derived deterministic IDs or any Vault-specific/bundled-app-specific branch.
- Sightings consumer: Manager reads sightings from the Hub endpoint when the user explicitly opens the relevant Manager surface, runs the sync-then-re-evaluate loop, and surfaces remaining sightings to the user.
- Disposition handling for both rejection levels stays Manager-local; the Hub is not consulted when deciding whether to re-prompt the human.
- Thin web/CLI surface — only what Phase 4's micro test needs.
Exit gate: the code exposes separate identity/team operations that a micro test can call directly, without any app package writing SQLite rows itself, without a merged public helper taking a `level=` parameter, and without a background sightings poller.

**Phase 3 — Vault de-impersonation.**
- Replace `_HUB_APP_NAME = "SmallSeaCollectiveCore"` with `"SharedFileVault"` in `sync.py` and `web.py`.
- Walk the test suite. Anywhere the test setup pre-creates a Core-app session for Vault, switch it to register Vault explicitly via the new Manager APIs from Phase 2 in the fixture or test setup.
- After the mechanical rename pass, review every file in `packages/shared-file-vault/tests/` by hand. If a file needed no structural change, ask why that test still reflects reality.
- Remove any sandbox fixtures that grant Vault Core access by hand. Do not make Phase 3 depend on the optional sandbox convenience endpoint.
Exit gate: `rg` over `packages/shared-file-vault/` and its tests finds no remaining runtime use of `SmallSeaCollectiveCore`, the updated fixtures succeed via normal Manager APIs rather than a hidden shortcut, and the hand review of `packages/shared-file-vault/tests/` found no mechanically renamed tests with stale structure.

**Phase 4 — End-to-end micro test.**
- Fresh participant + team. No apps registered.
- Vault calls `request_session(participant, "SharedFileVault", team, client)`. Expect structured rejection with reason "app unknown."
- Manager explicitly refreshes Hub sightings. Expects one sighting matching the request.
- Manager performs identity-level registration of `SharedFileVault`. Vault retries; expects rejection with reason "app known personally but not activated for team T."
- Manager performs team-level activation. Vault retries; expects success.
- A second test: rejection-then-disposition path. After the first sighting, Manager records identity-level rejection. Vault retries; expects the same Hub rejection, and Manager suppresses a second human prompt because of its own local disposition.
- A third test: production-mode negative case. `SMALL_SEA_SANDBOX_MODE` unset, fresh participant + team, Vault attempts a session, gets the structured rejection, and the request itself causes no `SharedFileVault` or new non-Core app rows to appear in NoteToSelf or the team DB.
- If local app IDs land in this branch, add a collision micro test: two distinct app identities claim the same friendly name for the same team, Manager preserves both as separate observations/registrations, and no berth or app row is silently merged by name.
Exit gate: a reviewer can read one micro test and see the reason transition from "unknown" to "not activated" to success without depending on hidden fixture magic, plus one negative test proving the request path performs no implicit registration writes.

**Phase 5 — Optional sandbox dev escape hatch.**
- Single sandbox setup endpoint behind `SMALL_SEA_SANDBOX_MODE=1` that calls the generic Manager registration and activation operations for a developer-chosen list of app friendly names. The default sandbox fixture may include `SharedFileVault`, but Manager provisioning must not know or care that it is Vault.
- Gated identically to `/sessions/pending`.
Exit gate: with sandbox off, the endpoint is absent; with sandbox on, it performs no writes outside the normal Manager-owned DBs.

**Phase 6 — Spec/doc sweep.**
- Update `architecture.md` with the two-level registration model and the unknown-app sighting concept.
- Update `packages/small-sea-manager/spec.md` to expand §App Management, document the rejection-disposition tables, and revise the open-questions section.
- Update `packages/small-sea-hub/spec.md` to document the sightings table, the structured rejection vocabulary, and the new endpoint.
- Make the spec pass one-shot and descriptive: update `spec.md` against what actually landed, not as an implementation diary. Keep unification ideas as prose; if DDL examples are helpful, place them in fenced blocks in the spec rather than in live `.sql` files.
- Record D1, D2, D3 outcomes in the open-questions sections of the affected specs.
Exit gate: a skeptical reader can understand the trust boundary, the provisioning boundary, and the bootstrap loop from docs alone without reverse-engineering code.

**Phase 7 — Spawn follow-up issues.** (See §Sub-Issues to Spawn.)

## Validation Strategy (smart-skeptic test)

A skeptical reviewer should be able to convince themselves of all the following without leaving the repo:

**Trust boundary still holds.**
- `grep` Vault for any direct access to `core.db` files outside of Hub session APIs. Should return nothing.
- `grep` for `SmallSeaCollectiveCore` in the Vault package. Should return nothing.
- The Hub sightings table is in `small_sea_collective_local.db`, not in any Manager-owned DB. Verified by file path.
- The negative production-mode micro test verifies that `POST /sessions/request` performs no implicit app-registration writes anywhere.
- Code review can verify that every new Hub write in this branch targets only `small_sea_collective_local.db`.

**Two-level model is real, not cosmetic.**
- The end-to-end test in Phase 4 exercises both rejection levels distinctly. A reviewer can read the test and see the rejection reasons change between retries.
- Manager has separate `register_app_for_identity` and `activate_app_for_team` operations. They are not collapsed into one function with optional flags.
- Manager sightings refresh is tied to an explicit user action in the thin UI/CLI surface, not to a background poll loop.

**Bundled apps get no special path.**
- The Vault de-impersonation test starts from zero registered apps and walks the full Hub sighting + Manager registration path before getting a session.
- The negative production-mode test starts from zero registered apps with `SMALL_SEA_SANDBOX_MODE` unset, attempts a Vault session, receives the structured rejection, and verifies that no `SharedFileVault` app row was created as a side effect.
- The sandbox dev button is gated behind `SMALL_SEA_SANDBOX_MODE` and is the only place in the repo that registers an app without walking the prompt path. It calls generic Manager operations with app data supplied by sandbox setup, not Manager-owned Vault knowledge. Test verifies that turning sandbox mode off makes the endpoint return 404.

**Repo integrity.**
- No new direct DB reads from any app package into Manager DBs (Vault is the canary).
- The Hub remains a read-only consumer of Manager DBs; its writes are only to its own local DB.
- The sightings upsert is atomic SQLite (`ON CONFLICT DO UPDATE`), not Python read-then-write logic that can lose retry counts.
- Existing micro tests still pass. Any test that currently sets up a Vault session via `SmallSeaCollectiveCore` is rewritten to use the new path; none are deleted to make the suite green.

The validation section should be revisited at end-of-branch with concrete file paths, line numbers, and named tests once the implementation lands.

## Sub-Issues to Spawn

Tracked here so they don't get lost during iteration:

If an edge case tempts the branch to expand, add the follow-up issue here, link it from a code comment if the code needs a breadcrumb, and move on.

1. **Implement full `app_unification`** if this branch only lands local app IDs plus schema sketches.
2. **Implement `berth_storage` indirection** if this branch does not land it with local app IDs, or when credential rotation/provider migration needs make it urgent.
3. **Real `NoteToSelf/{App}` materialization** beyond the empty-directory stub.
4. **Hub `/info` discovery endpoint** in follow-up issue #8, since D3 resolves to D3.A on this branch.
5. **Manager web UI for sightings review and registration approval** beyond the bare-bones surface this branch lands.
6. **App-side bootstrap helper in `small-sea-client`** so apps don't each reinvent the rejection-handling and "tell the human to open Manager" message.
7. **Per-sighting cleanup policy.** Issue #111 says "more or less forever for now." A future issue should decide whether to age out resolved sightings.
8. **Cross-device sighting visibility (out of scope here, possibly never).** Sightings are intentionally local. Revisit only if a concrete user need appears.

## Risks and Open Questions

- **Schema migration risk on the Hub local DB.** Pre-alpha, but the Hub local DB now carries session state that survives restarts. The migration should be additive only; reviewers should verify no destructive `ALTER` is introduced.
- **Test setup churn.** Vault de-impersonation will touch many test fixtures. The risk is that tests get partially migrated and pass for the wrong reason. Phase 3 acceptance must include an explicit grep gate (no `SmallSeaCollectiveCore` strings in Vault package or its tests).
- **Rejection vocabulary stability.** Apps will start coding against the rejection shape. Choosing the wrong field names or codes here costs us later. Settle the vocabulary in Phase 0 before writing any of Phase 1.
- **Disposition semantics under unification.** If an identity-level rejection of friendly name `X` later encounters an app identity unified with `Y`, the Manager needs clear rules for whether the rejection follows the local app ID, the pre-unification friendly name, or both. Out of scope for this branch but worth flagging.
- **Same-name app collision UX.** Manager must eventually show two apps with the same friendly name without implying they are the same app. The minimum acceptable behavior is to preserve both identities and require explicit unification; polished naming/renaming UX can follow later.
- **Sandbox button blast radius.** The sandbox app setup button must not exist outside sandbox mode. Test should verify by attempting to call it with sandbox off.
