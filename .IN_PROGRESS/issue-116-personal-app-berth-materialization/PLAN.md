# Branch Plan: Real Materialization of Personal (NoteToSelf) App Berths

**Branch:** `issue-116-personal-app-berth-materialization`
**Base:** `main`
**Primary issue:** #116 "Design real NoteToSelf app berth materialization"
**Kind:** Design + small implementation slice.
Likely to spawn at least one follow-up.

**Predecessor context:**
This is a direct follow-up from #111.
That branch shipped two-level app registration (participant + team) and resolved D1/D2 toward local-ID-plus-`app_unification`.
On the participant side, `register_app_for_participant` currently writes the `app` and `team_app_berth` rows to `NoteToSelf/Sync/core.db` and creates an **empty** directory at `NoteToSelf/{AppName}/`.
That empty directory is the stub this branch must define.

**Related code of interest:**
- `packages/small-sea-manager/small_sea_manager/provisioning.py` — `register_app_for_participant` (creates the stub) and `activate_app_for_team` (does *not* create any per-berth directory on the team side).
- `packages/small-sea-hub/small_sea_hub/backend.py` — `_resolve_berth`, which today only ever opens `{team}/Sync/core.db`, never a per-app-berth file.
- `packages/small-sea-note-to-self/small_sea_note_to_self/db.py` — owner of `NoteToSelf/Sync/` and `NoteToSelf/Local/` layout.
- `packages/shared-file-vault/shared_file_vault/sync.py` — current Vault sync, which does not yet read or write under `NoteToSelf/SharedFileVault/`.
- `architecture.md` Berth definition (§9), Manager spec §App Management, Hub spec §Berth resolution.

## Purpose

After #111, an app registered for a participant has a row in NoteToSelf and an empty directory on disk.
Nothing in the framework writes to that directory.
No app reads from it.
No sync mechanism touches it.

This branch's purpose is to decide what that directory **is for**, and to land the smallest concrete thing that proves the answer.

Specifically we want to answer:

1. Is the personal app berth a *real* synced location for per-participant personal state (settings, drafts, multi-device personal data that should follow the user across their own devices)?
2. Or is the berth purely a logical record in NoteToSelf, with no framework-managed local-disk area, and apps choose their own storage (cloud bucket, in-app config, etc.)?
3. Or some third thing: a hybrid where the directory exists but only opt-in apps write to it.

Whichever answer we land, the framework should stop shipping an empty stub directory whose meaning is undefined.

## Why This Plan Needs To Be Strict

Empty stubs are the easiest place to accidentally invent durable semantics.
If we leave the directory as-is, the next app that needs personal state will pick a convention by accident — write a SQLite file there, push it through NoteToSelf's git repo, and now we have a de-facto schema that nobody designed.

The branch should be strict about three things:

1. The decision about whether the framework owns this directory's *contents* is made before any app writes to it.
2. If we decide the framework does own it, the sync mechanism is named explicitly (not inferred from where the file happens to live on disk).
3. The team-side asymmetry is acknowledged.
   `activate_app_for_team` does not create a `{team}/{App}/` directory today.
   The participant-side decision should either bring the team side along or explicitly say why the two sides differ.

## Branch Contract (v1 slice)

The branch is successful if all of the following are true:

1. The repo has one written answer to "what is the personal app berth for?" recorded in the Manager spec and `architecture.md`.
2. `register_app_for_participant` no longer creates a stub whose meaning is undefined.
   Either it creates a directory whose contents the framework guarantees, or it creates nothing on disk and the spec says so.
3. The team-side equivalent question is either answered the same way for symmetry, or has an explicit follow-up issue.
4. At least one micro test exercises whatever the new shape is — either "personal berth has the documented framework-created shape after registration" or "registration creates only DB rows, no on-disk artifact under `NoteToSelf/{App}/`."
5. Vault is the canary: if the answer is "framework creates a synced area," Vault uses it for something concrete and small.
   If the answer is "no framework-managed contents," Vault keeps not touching it.

Everything else justifies itself by serving that loop.

## Phase 0 Decisions To Freeze Before Coding

### D1. What is the personal app berth for?

Candidate answers:

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

### D2. Team-side symmetry

If D1 lands D1.A or D1.C, does `activate_app_for_team` also create `{team}/{App}/` (and possibly `{team}/{App}/Sync/`)?

Candidate answers:

- **D2.A — Symmetric.** Both sides materialize the same shape.
- **D2.B — Asymmetric, intentional.** Team-side berth contents are already in cloud storage (per-berth bucket), so no local directory is needed; only personal-side gets a local directory.
- **D2.C — Defer team side to a follow-up.** This branch covers only the participant side; team-side gets an explicit issue.

The team side already uses `ss-{berth_id_hex[:16]}` cloud buckets for its data (see `backend.py:1117`).
That argues for asymmetry: team berths sync through their bucket, personal berths sync through NoteToSelf's existing transport.
If we accept that, D2.B is the honest answer and the branch should record why.

### D3. Sync transport for personal berths (only relevant under D1.A)

- **D3.A — Reuse NoteToSelf's existing git transport.**
  `NoteToSelf/{App}/Sync/.git` is pulled/pushed the same way `NoteToSelf/Sync/.git` is, against the same remote.
  Cheapest mechanism; tightly couples per-app personal state to NoteToSelf's overall sync cycle.
- **D3.B — Per-berth bucket like team apps.**
  Personal app berths get their own cloud bucket (`ss-{berth_id_hex[:16]}` or similar) and sync independently.
  Symmetric with team app berths.
- **D3.C — Single repo, app-scoped subdirectory.**
  The app's personal data lives as a subdirectory inside `NoteToSelf/Sync/` (not a separate repo).
  Framework reserves a path; the app writes there; one repo for all participant state.
  Simpler than D3.A, blurrier ownership.

This is only worth deciding if D1 lands D1.A.
Under D1.B no transport is needed.

### D4. Framework-managed schema?

Under D1.A or D1.C, does the framework write any file inside `NoteToSelf/{App}/` at registration time?

- **D4.A — Empty directory only.** Framework creates the dir; app fills it.
- **D4.B — Initialized git repo only.** Under D1.A + D3.A, the framework also runs `git init`.
- **D4.C — Framework creates a minimal manifest** (e.g. `.berth-info.json` with berth ID, app friendly name) for diagnostics.
  Pre-alpha pricetag is low but it adds a thing the app must not modify.

Default working answer: D4.B if D1.A, else D4.A.

### D5. What does Vault actually need from this?

Vault is the only real app in-tree and is the natural canary.
We should answer: does Vault have personal state that wants to sync across the user's devices?

Working hypothesis: Vault's current personal state is mostly its CLI/web config plus the local vault root path.
That state is *device-local*, not cross-device-personal.
If that holds, Vault has no immediate need for D1.A and we can land D1.B without a Vault-side change.

If Vault does have legitimate cross-device personal state (e.g. "which niches has this participant subscribed to across all teams"), then we want D1.A and Vault becomes the canary user of the new sync area.

This needs a concrete answer before committing to D1.

## Working Direction (subject to Phase 0 resolution)

Pending the debates above, the working direction is:

- **D1.B** as the default: registration writes DB rows only; no `NoteToSelf/{App}/` directory creation.
  Reasoning: pre-alpha freedom is best spent avoiding the wrong durable artifact.
  An empty stub is a worse signal than no stub.
- **D2.B** for symmetry of *honesty*: team side already has no per-berth local directory, and that asymmetry is now intentional.
- **D5**: confirm Vault has no near-term need for cross-device personal state.

If during Phase 0 we discover that an in-tree consumer needs D1.A (per-participant cross-device personal state), the working direction flips to D1.A + D3.A + D4.B, and Vault or that consumer becomes the canary.

This working direction is what makes the branch small.
If D1.A wins, the branch grows accordingly and we should revisit the cut line.

## Branch Cut Line

**Must land on this branch**

- A written decision (with rationale) for D1, D2, and (if relevant) D3/D4 in `architecture.md` and `packages/small-sea-manager/spec.md`.
- The code change that implements the decision in `register_app_for_participant`.
- One or two micro tests proving the new shape.
- If the decision is D1.B: removal of the empty-directory creation, and a regression test that the directory is *not* created.
- Spec/doc sweep that lands the decision in prose.

**First things to cut**

- Team-side symmetry work, if the decision is to leave team activation alone for now (spawn as follow-up).
- Any Vault-side use of a new personal sync area beyond the minimum needed to prove the contract.
- Any cross-device propagation tests beyond a single happy-path micro test.

## Phasing (draft)

**Phase 0 — Resolve D1/D2/D3/D4/D5 in this document.**
Exit gate: the plan names one chosen answer for D1 and D2, and the working direction is consistent across the spec sweep, code change, and tests.

**Phase 0.5 — Failing micro test skeleton.**
Add the test(s) the branch will turn green before writing implementation.
Under D1.B, the test asserts that `register_app_for_participant` leaves `NoteToSelf/{App}/` absent and the participant DB rows present.
Under D1.A, the test asserts that `NoteToSelf/{App}/Sync/.git` exists and is a valid repo after registration.
Exit gate: the new tests fail on `main` and the plan references them by name.

**Phase 1 — Implement the chosen direction.**
- Under D1.B: remove `app_dir.mkdir(...)` from `register_app_for_participant`, audit callers/tests for any incidental reliance on the directory existing.
- Under D1.A: add `Sync/` subdir + `git init` + initial empty commit in `register_app_for_participant`, wire it into whatever transport D3 chose.
- In both cases: leave the participant DB write path alone.
Exit gate: implementation is one mechanical change scoped to `register_app_for_participant` (plus transport wiring if D1.A) and its tests.

**Phase 2 — Spec sweep.**
- `architecture.md`: update the Berth section to name the personal-vs-team berth contents distinction explicitly.
- `packages/small-sea-manager/spec.md` §App Management: record the decision and the rationale.
- `packages/small-sea-hub/spec.md`: only if D1.A and the Hub now needs to know the personal berth's storage location.
Exit gate: a skeptical reader can read the spec and answer "what does NoteToSelf/{App}/ contain after registration?" without reading code.

**Phase 3 — Follow-ups filed.**
- If D2 punts the team side, file the follow-up.
- If D1.B wins, file a follow-up for any future app that needs cross-device personal state.
- If D1.A wins, file a follow-up for `berth_storage` indirection alignment (#114) since that issue's design now touches both sides.

## Validation Strategy (smart-skeptic test)

A skeptical reviewer should be able to convince themselves of all the following without leaving the repo:

**The undefined stub is gone.**
- `rg` over the repo for `NoteToSelf/{App}` style paths shows either (D1.B) no framework code creating a per-app directory at all, or (D1.A) exactly one creation site with a defined contract.
- The Manager spec answers "what is in `NoteToSelf/{App}/`?" in prose.
- A reviewer reading the new micro test sees the expected shape asserted explicitly, not inferred.

**The team-side asymmetry is intentional, not accidental.**
- `activate_app_for_team` and `register_app_for_participant` either match in what they materialize on disk, or the spec names the divergence and the issue tracker has a follow-up.
- Code review can verify there is no silent partial implementation (e.g. a directory created on one side but not the other without a docstring or spec).

**No app is silently relying on the old stub.**
- Vault tests still pass without depending on `NoteToSelf/{App}/` existing.
- A grep over `packages/` for reads from `NoteToSelf/[A-Z]` paths finds nothing under D1.B, or finds only consumers of the new contract under D1.A.

**Hub berth resolution is unaffected.**
- The Hub's `_resolve_berth` reads only `{team}/Sync/core.db`.
- Changing the personal-berth on-disk shape must not change which file the Hub opens for `NoteToSelf` sessions (`NoteToSelf/Sync/core.db`).
- A micro test or spec note records this invariant.

**Pre-alpha guardrails.**
- Any new schema (D4.C) lands as prose first, code second.
- No migration shims are introduced for the empty-directory change; pre-alpha rules apply (AGENTS.md).

## Non-Negotiable Invariants

1. The Hub continues to never write to participant or team DBs.
   This branch does not change Hub write paths.
2. Manager remains the sole writer of `core.db` in NoteToSelf and team DBs.
   The branch only changes whether (and what) Manager writes under `NoteToSelf/{App}/`.
3. App data ownership: even under D1.A, the *contents* inside a per-app berth directory are owned by the app.
   The framework owns the directory, the git repo bones (if any), and the transport.
   It does not introduce a schema inside.
4. No backward-compat shims for the existing empty-directory behavior.
   Pre-alpha freedom (AGENTS.md) lets us delete the `mkdir` outright if D1.B wins.

## Risks and Open Questions

- **Hidden consumer of the empty directory.**
  If any test or app fixture relies on `NoteToSelf/{App}/` existing as a directory, switching to D1.B will surface it.
  Mitigation: Phase 1 audit step.
- **D3 lock-in for personal-berth transport.**
  Reusing NoteToSelf's git transport (D3.A) is cheap but couples per-app sync to NoteToSelf's overall sync cadence.
  Per-berth buckets (D3.B) are heavier but symmetric with team berths.
  Mitigation: only relevant under D1.A; if we land D1.B, the question deferred.
- **Spec drift between Manager spec and architecture.md.**
  Touching the Berth concept means two docs must agree.
  Mitigation: Phase 2 explicit step, reviewer can diff both sections.
- **Pre-empting #114 (`berth_storage`).**
  If we choose D1.A + D3.B, we're effectively previewing the indirection #114 is supposed to deliver.
  We should not implement `berth_storage` here; only note the alignment.

## Sub-Issues To Spawn (placeholder)

To be populated as Phase 0 resolves:

1. (Tentative, under D1.B) Cross-device personal app state — design and implementation when a concrete consumer appears.
2. (Tentative, under D2.C) Team-side per-berth local directory question.
3. (Tentative, under D1.A + D3.B) Alignment with #114 `berth_storage`.
