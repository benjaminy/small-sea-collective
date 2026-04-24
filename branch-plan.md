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
- "Bundled" apps are not special at runtime. Vault becomes an ordinary Small Sea app named `SharedFileVault` that walks the same path as any third-party app would.

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
   At minimum the vocabulary must cover: app unknown, identity registration missing, team activation missing, transient "Manager sync may resolve," and local rejection/disposition if that part lands.
3. **Manager API shape.**
   The implementation should expose two clearly named operations, not one kitchen-sink helper with flags:
   `register_app_for_identity(...)`
   `activate_app_for_team(...)`
4. **Minimum durable sighting record.**
   The plan should freeze the exact v1 fields before migration work starts. Minimum useful shape:
   `app_name`, `team_name`, `client_name`, `first_seen_at`, `last_seen_at`, `seen_count`, machine rejection reason, and durable local disposition/state.
5. **Discovery scope for this branch.**
   Record whether this branch includes a new Hub read API for app self-configuration, or whether apps only get the structured rejection and the instruction to open Manager.

If any of the five items above remain fuzzy, the branch is still in document-iteration mode, not implementation mode.

## Non-Negotiable Invariants

The implementation is only acceptable if all of these remain true:

1. Apps never write to NoteToSelf or team DBs. Their only Hub-mutating action remains opening a session and using it.
2. Apps never read Manager DBs directly. Anything an app needs to discover during bootstrap is exposed through a Hub HTTP endpoint, or returned in the structured rejection.
3. The Hub's unknown-app sightings table is local Hub state only. It is never synced to peers and never read by apps.
4. Identity-level registration and team-level activation are separately authorized decisions. The Manager UI/CLI may bundle them in a single flow for convenience, but the underlying data model treats them as distinct.
5. Bundled apps (Vault) get no special path through the Hub. They walk the same rejection-and-registration loop as any third-party app. The only acceptable special case is sandbox-mode developer convenience (see §Sandbox dev escape hatch).
6. Vault stops opening sessions as `SmallSeaCollectiveCore` everywhere — `sync.py`, `web.py`, CLI, and all test fixtures.
7. The shape of the structured rejection is stable enough that an app written today can keep using the same response codes after later branches add finer-grained reasons.

## Branch Goals

When this branch is done, the repo should provide all of the following:

1. A written design of the two-level registration model (identity-level and team-level), recorded in `architecture.md` and the Manager and Hub specs, with the open debates explicitly named (see §Open Design Debates).
2. A Hub-side schema for durable unknown-app sightings, with enough fields to support Manager UX, dedupe, and per-sighting disposition.
3. A Hub-side structured rejection vocabulary for `/sessions/request` covering at least: app unknown, app known personally but no personal berth, app known personally but not activated for the requested team, and "transient — Manager sync may resolve."
4. A Hub-side read endpoint that lets the Manager enumerate sightings.
5. Manager-side identity-level registration: writes the new `app` row and `team_app_berth` for the participant's NoteToSelf team, and creates the `NoteToSelf/{App}` directory. The framework does nothing further inside that directory; its contents belong to the app.
6. Manager-side team-level activation: writes the `app` and `team_app_berth` rows in the team DB, plus `berth_role` rows for current members.
7. Manager-side disposition handling for "I don't want this app on this device" (identity-level, in `NoteToSelf/Local/device_local.db`) and "I don't want this app on team T" (team-level, in the per-team Manager-local sidecar DB used today for admission-prompt dismissals).
8. Schema sketches for the unification tables driven by the eventual debate outcomes (identity-level `app_unification`, team-level either deterministic IDs or `app_unification` + `berth_storage` indirection). Schema sketches are in this branch; implementation is in follow-ups.
9. `SharedFileVault` is the Vault's canonical app name. Vault's `sync.py`, `web.py`, CLI, tests, and fixtures all use `SharedFileVault` and never `SmallSeaCollectiveCore`.
10. An end-to-end micro test that walks the full loop: fresh sandbox → Vault requests session → Hub rejects with structured reason and records sighting → Manager observes sighting → Manager registers Vault at identity level → Manager activates Vault for team T → Vault retries successfully.
11. A sandbox-mode dev convenience: a single Hub or Manager endpoint that initializes the default bundled apps (currently just Vault) without walking the human prompt path. Gated behind the same sandbox flag as `/sessions/pending`.
12. Spec/doc updates so `architecture.md`, the Manager spec, and the Hub spec reflect the shipped two-level model and the new rejection vocabulary, and so the open debates are recorded as explicit open questions.

## Open Design Debates (carried into this branch, not pre-resolved)

These remain open and will be argued through in successive iterations on this plan.

### D1. Identity-level app identity model

- **D1.A — Canonical name as identity.** App identity is a single string. No new tables. Renaming is a destructive operation handled as uninstall + reinstall.
- **D1.B — Local UUID + canonical name + `app_unification`.** Mirrors the existing `participant_unification` shape. Renaming is non-destructive. Provides a real recovery mechanism for the squatting/typo case and preserves the option of certificate-based app identity later.

Default if undecided at implementation time: build the v1 slice against D1.A (simpler, fewer schema moves), reserve the `app_unification` table name for D1.B follow-up.

### D2. Team-level app identity model

- **D2.A — Deterministic IDs.** `app.id = uuid5(team_id, canonical_name)` and `team_app_berth.id` derived in turn. Concurrent activation by two admins converges by primary-key collision under splice-sqlite merge. No reconciliation logic needed for the common case. Does not solve typos or rebrands.
- **D2.B — Local UUID + `app_unification` + `berth_storage` indirection.** Symmetric with D1.B. Adds a `berth_id → bucket_name / topic` indirection table that pays for itself independently (credential rotation, provider migration, compaction). Typos and rebrands fall under the same unification mechanism as concurrent activations.

D1 and D2 may legitimately resolve to different answers; symmetry is a goal but not a requirement.

### D3. Discovery endpoint scope (#8 interaction)

- **D3.A — No new read endpoint on this branch.** The structured rejection from `/sessions/request` carries enough information that the app's only response is "tell the human to open Manager." `/info` lands in #8 separately.
- **D3.B — Land a minimal `/info` here.** If the app needs to render a useful UI before sending the human to Manager (e.g. "you're registered for these teams, want to use this one?"), the Hub exposes a small read-only endpoint covering team list and per-app status.

Working decision for this branch: **D3.A**.

The first vertical slice should not add `/info` or `/teams` here. That work already has a natural home in #8, and pulling it into this branch would blur the line between "bootstrap failure reporting" and "general self-configuration API."

If implementation later proves that Vault cannot present a minimally sane "open Manager" flow without more Hub-read context, stop and reopen this plan explicitly rather than quietly expanding scope.

## Scope Decisions Already Made

- Vault's canonical name is `SharedFileVault`. Not set in stone, but final for this branch.
- Unification implementation is **defined now, implemented later**. Schema sketches land here; the writing/reading code lands in follow-up issues spawned from the design.
- `NoteToSelf/{App}` berths belong to the app. The Manager creates the directory at registration time and does nothing further. No core-framework schema for it.
- No special path for bundled apps. The sandbox dev button is the only allowed shortcut, and it lives behind the sandbox flag.
- Rejection-reason transport is HTTP-level structured fields, not a strongly-typed enum. The wire shape just needs to let an app distinguish the relevant cases.
- This branch's first shipped slice is the Vault bootstrap loop. Generic app-self-configuration APIs stay in #8 unless the code proves that separation unworkable.

## In Scope

- New Hub-private SQLite table for unknown-app sightings (additive migration on `small_sea_collective_local.db`).
- Restructuring `_resolve_berth` and `request_session` so they distinguish unknown-app, missing-personal-berth, missing-team-berth, and transient cases, and so they record sightings on the unknown branches.
- A Hub HTTP endpoint exposing sightings (read-only) for Manager consumption.
- Manager-side identity-level registration and team-level activation as separate operations in `provisioning.py`, plus thin web/CLI exposure.
- Manager-side disposition tables: identity-level rejection in `NoteToSelf/Local/device_local.db`; team-level rejection in the existing per-team Manager-local sidecar DB.
- Renaming Vault's app identifier across all of `shared_file_vault/`, `tests/`, and any sandbox fixtures.
- An end-to-end micro test for the full bootstrap loop (see §Validation).
- A sandbox-mode dev endpoint for "initialize default apps."
- Spec/doc updates: `architecture.md`, `packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`.

## Out of Scope (Explicitly Deferred)

- Implementation of `app_unification` (D1.B) or `berth_storage` indirection (D2.B) beyond schema sketches.
- Real `NoteToSelf/{App}` materialization beyond an empty directory.
- Cross-device coordination of sightings (sightings are local Hub state, not synced).
- Apprise / additional notification adapters in the OS prompt path.
- Any cryptographic identity for apps (certificate-signed app identity).
- Any UX polish in the Manager web UI beyond what is needed for the end-to-end micro test to be observable.
- Any change to how transport metadata flows for newly registered apps. Transport configuration is B7 territory.
- Hub-side enforcement of which clients are allowed to request which apps (today there is none; this branch does not add any).

## Branch Cut Line

If this branch starts to sprawl, the cut line should be explicit rather than implicit.

**Must land on this branch**

- Structured Hub rejection for app-bootstrap/configuration failures.
- Durable Hub sightings plus a Manager-readable sightings endpoint.
- Separate Manager identity-registration and team-activation operations.
- Vault de-impersonation to `SharedFileVault`.
- At least one end-to-end micro test proving the full bootstrap loop from first rejection to successful retry.
- Spec/doc updates that explain the shipped ownership boundary and rejection vocabulary.

**First things to cut if needed**

- Sandbox-mode "initialize default apps" convenience endpoint.
- Rich disposition UX beyond the minimum plumbing needed to keep repeated prompts from flapping.
- Detailed schema-sketch work for D1.B / D2.B beyond the notes needed to preserve the option space.

## Phasing (draft)

Phases are sized for reviewability, not for hard sequencing. Several can proceed in parallel once the schema and rejection vocabulary are settled.

**Phase 0 — This document.**
Iterate on `branch-plan.md` to convergence on rejection vocabulary, registration API shape, and the branch cut line. D3 is fixed to D3.A for this branch. D1 and D2 stay open.
Exit gate: the plan names one concrete response shape, one concrete reason vocabulary, and one cut line for what this branch will refuse to absorb.

**Phase 1 — Hub sightings table and structured rejection.**
- Add `unknown_app_sighting` schema to Hub local DB (additive migration via `PRAGMA user_version`).
- Refactor `_resolve_berth` to return a typed result distinguishing the rejection reasons in §Branch Goal 3.
- Update `/sessions/request` to record a sighting on rejection paths and return a structured rejection body.
- Add a Hub read endpoint (`/sightings` or similar) that returns sightings with their durable disposition.
Exit gate: Hub micro tests cover success plus each shipped rejection reason, and the migration is visibly additive-only.

**Phase 2 — Manager registration and activation.**
- Identity-level registration in `provisioning.py`: writes the NoteToSelf `app` row, the NoteToSelf `team_app_berth`, and creates the `NoteToSelf/{App}` directory.
- Team-level activation in `provisioning.py`: writes the team DB `app` row, `team_app_berth`, and `berth_role` for current members.
- Sightings consumer: Manager reads sightings from the Hub endpoint, runs the sync-then-re-evaluate loop, surfaces remaining sightings to the user.
- Disposition handling for both rejection levels.
- Thin web/CLI surface — only what Phase 4's micro test needs.
Exit gate: the code exposes separate identity/team operations that a micro test can call directly, without any app package writing SQLite rows itself.

**Phase 3 — Vault de-impersonation.**
- Replace `_HUB_APP_NAME = "SmallSeaCollectiveCore"` with `"SharedFileVault"` in `sync.py` and `web.py`.
- Walk the test suite. Anywhere the test setup pre-creates a Core-app session for Vault, switch it to register Vault explicitly via the new Manager APIs.
- Remove any sandbox fixtures that grant Vault Core access by hand; replace with the sandbox dev button (Phase 5).
Exit gate: `rg` over `packages/shared-file-vault/` and its tests finds no remaining runtime use of `SmallSeaCollectiveCore`.

**Phase 4 — End-to-end micro test.**
- Fresh participant + team. No apps registered.
- Vault calls `request_session(participant, "SharedFileVault", team, client)`. Expect structured rejection with reason "app unknown."
- Manager polls Hub sightings. Expects one sighting matching the request.
- Manager performs identity-level registration of `SharedFileVault`. Vault retries; expects rejection with reason "app known personally but not activated for team T."
- Manager performs team-level activation. Vault retries; expects success.
- A second test: rejection-then-disposition path. After the first sighting, Manager records identity-level rejection. Vault retries; expects the same rejection rather than a new prompt.
Exit gate: a reviewer can read one micro test and see the reason transition from "unknown" to "not activated" to success without depending on hidden fixture magic.

**Phase 5 — Sandbox dev escape hatch.**
- Single endpoint behind `SMALL_SEA_SANDBOX_MODE=1` that registers and activates the default bundled apps (currently just Vault) for the active participant and a named team.
- Gated identically to `/sessions/pending`.
Exit gate: with sandbox off, the endpoint is absent; with sandbox on, it performs no writes outside the normal Manager-owned DBs.

**Phase 6 — Spec/doc sweep.**
- Update `architecture.md` with the two-level registration model and the unknown-app sighting concept.
- Update `packages/small-sea-manager/spec.md` to expand §App Management, document the rejection-disposition tables, and revise the open-questions section.
- Update `packages/small-sea-hub/spec.md` to document the sightings table, the structured rejection vocabulary, and the new endpoint.
- Record D1, D2, D3 outcomes in the open-questions sections of the affected specs.
Exit gate: a skeptical reader can understand the trust boundary, the provisioning boundary, and the bootstrap loop from docs alone without reverse-engineering code.

**Phase 7 — Spawn follow-up issues.** (See §Sub-Issues to Spawn.)

## Validation Strategy (smart-skeptic test)

A skeptical reviewer should be able to convince themselves of all the following without leaving the repo:

**Trust boundary still holds.**
- `grep` Vault for any direct access to `core.db` files outside of Hub session APIs. Should return nothing.
- `grep` for `SmallSeaCollectiveCore` in the Vault package. Should return nothing.
- The Hub sightings table is in `small_sea_collective_local.db`, not in any Manager-owned DB. Verified by file path.

**Two-level model is real, not cosmetic.**
- The end-to-end test in Phase 4 exercises both rejection levels distinctly. A reviewer can read the test and see the rejection reasons change between retries.
- Manager has separate `register_app_for_identity` and `activate_app_for_team` operations. They are not collapsed into one function with optional flags.

**Bundled apps get no special path.**
- The Vault de-impersonation test starts from zero registered apps and walks the full Hub sighting + Manager registration path before getting a session.
- The sandbox dev button is gated behind `SMALL_SEA_SANDBOX_MODE` and is the only place in the repo that registers an app without walking the prompt path. Test verifies that turning sandbox mode off makes the endpoint return 404.

**Repo integrity.**
- No new direct DB reads from any app package into Manager DBs (Vault is the canary).
- The Hub remains a read-only consumer of Manager DBs; its writes are only to its own local DB.
- Existing micro tests still pass. Any test that currently sets up a Vault session via `SmallSeaCollectiveCore` is rewritten to use the new path; none are deleted to make the suite green.

The validation section should be revisited at end-of-branch with concrete file paths, line numbers, and named tests once the implementation lands.

## Sub-Issues to Spawn

Tracked here so they don't get lost during iteration:

1. **Implement `app_unification` (D1.B path)** if D1 resolves to local UUID + unification.
2. **Implement `berth_storage` indirection (D2.B path)** if D2 resolves that way.
3. **Real `NoteToSelf/{App}` materialization** beyond the empty-directory stub.
4. **Hub `/info` discovery endpoint** if D3 resolves as D3.A on this branch (#8).
5. **Manager web UI for sightings review and registration approval** beyond the bare-bones surface this branch lands.
6. **App-side bootstrap helper in `small-sea-client`** so apps don't each reinvent the rejection-handling and "tell the human to open Manager" message.
7. **Per-sighting cleanup policy.** Issue #111 says "more or less forever for now." A future issue should decide whether to age out resolved sightings.
8. **Cross-device sighting visibility (out of scope here, possibly never).** Sightings are intentionally local. Revisit only if a concrete user need appears.

## Risks and Open Questions

- **Schema migration risk on the Hub local DB.** Pre-alpha, but the Hub local DB now carries session state that survives restarts. The migration should be additive only; reviewers should verify no destructive `ALTER` is introduced.
- **Test setup churn.** Vault de-impersonation will touch many test fixtures. The risk is that tests get partially migrated and pass for the wrong reason. Phase 3 acceptance must include an explicit grep gate (no `SmallSeaCollectiveCore` strings in Vault package or its tests).
- **Rejection vocabulary stability.** Apps will start coding against the rejection shape. Choosing the wrong field names or codes here costs us later. Settle the vocabulary in Phase 0 before writing any of Phase 1.
- **Disposition semantics under unification.** If D1 resolves to D1.B (unification), an identity-level rejection of canonical name `X` and a later unification of `X` with `Y` raises the question of whether the rejection still applies. Out of scope for this branch but worth flagging.
- **Sandbox button blast radius.** The "initialize default apps" button must not exist outside sandbox mode. Test should verify by attempting to call it with sandbox off.
