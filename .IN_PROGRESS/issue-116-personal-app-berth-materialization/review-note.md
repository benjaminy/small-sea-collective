# Review Note — Issue #116

**What this PR does:** removes two empty-directory stubs (`NoteToSelf/SmallSeaCollectiveCore/` and `NoteToSelf/{AppName}/`) and records the ownership boundary in docs.
Manager registers and authorizes app berths; apps own their local materialization tree.

**What changed:**

- `provisioning.py`: two `mkdir` calls deleted. DB writes and NoteToSelf git commits untouched.
- `test_create_team.py`: existing Core test flipped from `is_dir()` to `not exists()`; one new test for `register_app_for_participant`.
- `architecture.md`, `small-sea-hub/spec.md`, `small-sea-manager/spec.md`: spec sweep on the ownership boundary and the `/session/info` metadata shape.

**Where to focus review:**

1. The two `mkdir` deletions in `packages/small-sea-manager/small_sea_manager/provisioning.py`.
2. The architecture.md paragraph following the Core Concepts bullets — it states the central conceptual move (participant is not a third berth coordinate; materialization is app-owned).
3. The Manager spec paragraph at §App Management explaining registration is authorization, not materialization.

**What this PR does not do:**

- No framework-managed cross-device personal sync.
- No new app-home helper API.
- No `/session/info` field additions.
- No Vault migration.
- No normative directory-naming convention.
  `architecture.md` describes app-owned materialization as principle-level guidance; exact names are app choice.

**Follow-ups:**

- #130 — first app-owned materialization consumer integration (filed).
- Two conditional follow-ups (explicit `team_id`/`app_id` in `/session/info`; cross-device personal sync ergonomics) deliberately not filed — see design-record.

**Validation:**

- Full affected suites: 248 passed, 3 skipped (pre-existing) across `small-sea-manager`, `small-sea-hub`, `shared-file-vault` tests.
- Grep gates pass — see design-record §Validation summary.

**Process note:**

Phase 0.5's red-test-first discipline collapsed into a single implementation commit.
End state is correct; git history does not show the red phase.
See design-record §Process notes.
