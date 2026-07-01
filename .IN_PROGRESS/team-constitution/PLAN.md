# Team Constitution Implementation Plan (Phase 1)

Tracks GitHub issue #163.
Builds on `Documentation/team-constitution.md` and `Archive/design-record-team-constitution-schema.md`.

## Goal

Land the first real, working vertical slice of the Team Constitution: one complete record type, end to end (shared signing envelope, table, signed construction, authorization check, projection update, micro tests), as the template later branches extend for the rest of the catalog.

## Scope decision (stated up front, not silently narrowed)

The full catalog in `Documentation/team-constitution.md` is too large for one branch.
This phase implements exactly one record type — **`integration_mode_change`** — chosen because it's the simplest single-signer type in the catalog (no quorum, no PII commitment/payload split, no recovery ceremony) and it's a direct, immediate unblock for issue #162 (mode-aware replication), the next epic in the sequencing.

**Anchor mechanism, narrowed for this phase.** The design doc's target anchor is `anchor_frontier` (record-to-record references, no git dependency) — but that only becomes fully realizable once `key_certificate` and `admission_proposal` are *also* migrated onto the shared envelope with predecessor chains, which is out of scope here.
For this phase, the new record's anchor is a `constitution_digest`: a live-query digest over current teammate/device/berth-role state, generalizing `admission_proposal`'s existing `governance_digest` (which today only covers Core admins) to cover every berth's mode.
This is a deliberate, temporary stepping stone — not the doc's target mechanism — and is called out as such in code comments and the eventual design-record update.

## Implementation

1. `packages/wrasse-trust/wrasse_trust/constitution.py` — the shared canonical envelope helper the design doc and the branch's original grounding survey call for: `canonical_constitution_bytes`, `derive_record_id`, `sign_constitution_record`, `verify_constitution_record`.
   Generalizes the idiom duplicated today in `identity.py`, `transport.py`, and `provisioning.py` — used by the new type, not retrofitted onto the existing three (that stays a named follow-up).
2. `packages/wrasse-trust/tests/test_constitution.py` — micro tests: round trip sign/verify, record_id determinism, tamper detection on every field.
3. New `integration_mode_change` table in `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`; bump `USER_SCHEMA_VERSION` 61 -> 62 (pre-alpha: existing local team DBs are expected to be deleted and recreated, per the project's own migration stance — not migrated in place).
4. `set_teammate_integration_mode(root_dir, participant_hex, team_name, teammate_id, berth_id, mode)` in `provisioning.py`: requires the caller's own teammate identity to currently hold `automatic` (`read-write`) standing on the target berth, appends the signed record, and updates the `berth_role` projection row to match (`automatic` -> `read-write`, `proposal-only` -> `read-only`).
   This is new functionality, not a migration of an existing call path — no "set teammate role after admission" function exists in the codebase today (`berth_role` rows are currently only ever inserted at creation/admission, never updated).
5. Micro tests in `packages/small-sea-manager/tests/` covering: valid mode change updates both the Constitution record and the projection; rejection when the caller lacks standing on the berth; unknown teammate/berth rejected; unknown mode value rejected.
6. Update `packages/small-sea-manager/spec.md`'s "Set teammate integration mode" section from target-behavior prose to describe what's implemented.

## Validation

- `uv run pytest packages/wrasse-trust/tests/test_constitution.py`
- `uv run pytest packages/small-sea-manager/tests/` (full package, to confirm no regression to admission/invitation/team-creation flows, none of which this phase touches)
- `git diff --check`
- Manual check: the new table's columns match `Documentation/team-constitution.md`'s `integration_mode_change` catalog entry, with the anchor-mechanism narrowing above being the one deliberate deviation.

## Non-goals (this phase)

- No other record type from the catalog (admission generalization, exclusion, recovery, display-name/unification PII types, staleness observations).
- No migration of `key_certificate` / `teammate_berth_storage_announcement` onto the shared envelope.
- No `anchor_frontier`/`predecessor_record_id` mechanism — see scope decision above.
- No change to the existing admission/quorum flow, including the known device-vs-teammate endorsement dedupe gap (flagged in the design record, not fixed here — unrelated call path).
