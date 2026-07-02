# Review note: team-constitution (phase 1)

First vertical slice of the Team Constitution (#163): shared signing envelope helper + the `integration_mode_change` record type, end to end.

Where to look, in reading order:

1. `packages/wrasse-trust/wrasse_trust/constitution.py` — the shared envelope helper (canonical bytes, record-id derivation, sign/verify); generalizes the idiom previously triplicated across `identity.py`/`transport.py`/`provisioning.py`, used only by the new type for now (#165 migrates the legacy types).
2. `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql` — new `integration_mode_change` table; `USER_SCHEMA_VERSION` 61 → 62, no migration (pre-alpha stance).
3. `provisioning.py::set_teammate_integration_mode` — authorization (caller must hold `automatic` standing on the target berth), signed record append, direct `berth_role` projection update.
4. Tests: `packages/wrasse-trust/tests/test_constitution.py` (envelope round trip, id determinism, per-field tamper detection) and `packages/small-sea-manager/tests/test_integration_mode_change.py` (happy path, standing rejection, unknown teammate/berth/mode).

Two deliberate deviations from `Documentation/team-constitution.md`, both tracked:

- Anchor is an interim `constitution_digest` (live-query digest + stored snapshot), not `anchor_frontier` — stepping stone, retired by #166.
- Projection row is mutated directly rather than rebuilt — converted by #167.

Validation run at wrap-up: 5/5 constitution micro tests, 100/100 full manager suite, `git diff --check` clean.
