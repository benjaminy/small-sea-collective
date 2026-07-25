# Design record: admission onto the shared envelope (#164)

Moves the one quorum-gated Constitution flow — admission — off the mutable
`admission_proposal` state machine and onto the shared append-only envelope
already used by `integration_mode_change`.
Four record types now carry the flow: `admission_proposal`, `admission_acceptance`,
`endorsement`, `finalization`.
Everything mutable is a computed view or a projection, never a mutated record.

## Choices a future developer might want to revisit

### Status is computed, never stored (`_admission_status`)

The proposal row has no `state`/`invalid_reason`/acceptance/finalization columns.
Status is derived from which records exist:
`finalized` (a `finalization` row) → `invalidated` (revoked, or governance drift) →
`expired` (`expires_at` passed) → `awaiting_quorum` (an `admission_acceptance` exists) →
`awaiting_invitee`.
Expiry and drift are *computed at read time* and produce a refusal at
endorse/finalize time (`_admission_action_block_reason`), never an `UPDATE`.
The old code mutated the row to `expired`/`invalidated` on the same conditions;
the new behavior is observationally the same for the UI but keeps the record
immutable. Cost: `_admission_status` recomputes the constitution snapshot digest
on every call. Fine at research scale; revisit if proposal lists get large.

### `admission_acceptance` is the schema's one self-certifying record

Built and signed invitee-side in `accept_invitation`; the inviter transports it
and inserts it verbatim after a *self-certifying* verify: recompute canonical
bytes, verify the signature against the record's own embedded
`invitee_device_public_key`, and require `author_device_key_id` to be that key's
id and `record_id` to match the contents.
The invitee has no `device_link` yet, so there is no cert graph to check against —
this is the deliberate exception documented in `team-constitution.md`.
Its envelope `anchor_commit`/`constitution_*` are NULL: the invitee has no
standing to attest to a governance view.

### `endorsement` dedupes per teammate, not per device (the #164 fix)

`UNIQUE(subject_record_id, author_teammate_id)` + `INSERT OR IGNORE`, so one
steward's two linked devices (or a repeat) count once. The old
`steward_approval` keyed on `(proposal_id, approver_device_key_id)`, letting a
single steward's two devices double-count toward quorum.
`subject_digest` is the untruncated sha256 over the endorsed acceptance's
canonical bytes (the acceptance's `record_id` is the first 16 bytes of the same
hash), generalizing the old `transcript_digest`.

### Merged rows are verified where they are consumed

Rows arrive by sync merge, so presence in the local DB implies nothing about validity: without read-path verification, a forged endorsement row in a real steward's name would satisfy the raw `COUNT(*)` quorum.
Every admission decision point therefore re-verifies what it reads.
The invariants:

- Quorum counts only endorsements that verify end-to-end (`_count_valid_endorsements`): signature over recomputed canonical bytes, `author_device_key_id` hashes from the device public key, `subject_digest` equals the recomputed acceptance commitment, the endorsement anchors at the proposal's own `constitution_digest`, and the (teammate, device) pair is an automatic Core integrator in the proposal's snapshot.
- `constitution_snapshot_json` is parsed only via `_verified_proposal_snapshot`, which checks it against the signed `constitution_digest` first.
- `endorse_admission`, `complete_invitation_acceptance`, and `finalize_admission` re-verify the proposal row (`_verify_proposal_row`) and the acceptance row (`_verify_acceptance_row`, the same self-certifying checks run on the transported token) before acting — merged rows are never trusted as already-checked local state.
- A proposal is `finalized` only if `_valid_finalization_exists`: some finalization row verifies one-hop — recomputed `record_id`, key-bound signature, author == the proposal's author (the inviter-only rule), `subject_digest` == the acceptance commitment.
  Bare row presence would let a forged merged finalization freeze the proposal ("already finalized" at every decision point) while the UI showed success with no projections anywhere.
  `finalization` has no `UNIQUE(subject_record_id)`, so invalid rows are ignored and the legitimate finalization is appended alongside them.
- Every governance-bearing record binds its author *device* to its claimed author *teammate*; without that binding, an insider can sign with their own registered device while claiming another teammate as author (misattributed proposals, insider finalization freezes).
  The binding source differs per record on purpose: `_valid_finalization_exists` checks the finalizer's device against the proposal's snapshot (`teammate_devices`), which is safe (any later device change drift-refuses the proposal) and strong (the snapshot rides the row whose content-derived `record_id` the whole chain FKs against); `_verify_proposal_row` checks `team_device.teammate_id` instead, because the proposal's own snapshot is self-referential — a forger signs whatever snapshot they like into a forged row.
  Endorsements carry the binding via snapshot eligibility; acceptance is exempt by design (the self-certification exception).
- The device-binding argument is non-circular only because `_valid_finalization_exists` runs `_verify_proposal_row` before trusting the proposal's author or snapshot: `record_id` is a *stored* column, so its binding to row content exists only where `derive_record_id(canonical) == record_id` actually runs.
  Without that check, an in-place UPDATE of the proposal row (same PK, swapped author, optionally a self-consistent crafted digest+snapshot) re-roots the device binding at attacker-chosen data, letting a validly signed finalization from the new "author" read as terminal.
  The predicate degrades to False (never raising) so status display survives tampered rows.
- The inviter's auto-endorsement at acceptance time is gated on the same integrator eligibility, so the code never authors an endorsement the counter would refuse.

Deliberate non-checks and known boundaries:

- A finalization's `constitution_digest` is not compared with the proposal's: a legitimate finalization anchors the *post-projection* snapshot, which always differs (also why `finalized` is decided before drift in `_admission_status`).
- Finalization validity does not re-count quorum: the threshold is currently mutable, so re-checking would retroactively invalidate legitimate finalizations when quorum is raised; the signed `endorsement_count` claim gets audited by #167's replay.
  Consequence, stated openly: a malicious *inviter* can sign a no-quorum finalization — the finalizer is the trusted principal in this slice (follow-up notes).
- Tampered `expires_at`/digest columns still freeze a proposal as `expired`/`invalidated` before any verification runs.
  Asymmetry kept deliberately: those tampers produce *refusal*, the conservative direction, and an in-place-tampered proposal is dead on that replica regardless; only the finalization branch could convert an unverified row into a false positive.
- `_count_valid_endorsements` relies on every caller handing it an already-verified proposal row.
- All of this is one-hop verification (device keys authenticated by key-id binding against the proposal's signed snapshot, no chain-follow to genesis); the chain-of-trust story is #166/#167 — see the follow-up notes.
- `_endorsement_count` (raw) survives only for the event-feed display.
  Status computation verifies signatures per call (finalization rows, at minimum); fine at research scale, same note as the snapshot-digest recompute above.

### Only `finalization` makes a proposal effective

At quorum 1 (default), `complete_invitation_acceptance` inserts the acceptance,
auto-endorses as the inviter, and finalizes inline.
At quorum ≥ 2 the acceptance alone yields `awaiting_quorum` with **no** projection
writes — a stronger property than the old code, where a quorum-met acceptance
finalized silently. All projection writes (`teammate`/`team_device`/cert/
`berth_role`) key off inserting the `finalization` row.

### Preset → per-berth `mode_plan` expansion (D5)

The `steward`/`contributor` preset is a UI presentation string translated to an
expansion *rule* at the manager boundary (`mode_plan_for_preset`):
`{"core_mode": ..., "other_mode": ...}` with `automatic`/`proposal-only` values.
It rides on the proposal as a **signed, NOT NULL** `mode_plan` column so a
different steward on a different device can finalize with the inviter's intent
intact, without storing Manager preset vocabulary.
The plan must be signed because it is the sole input to the authority granted at finalization: an unsigned plan could be escalated undetected, with the tamper laundered into honestly-signed `integration_mode_change` records.
Signing it also means every endorsement commits to the plan for free, since `subject_record_id` is a content digest of the proposal.
Rejected alternative: committing to the plan via the endorsement's `subject_digest` only — that binds it from first endorsement onward, leaving the creation-to-acceptance window open in the quorum-1 auto-finalize path.
There is no default plan at finalization: a missing/malformed plan refuses rather than granting the steward expansion.
At finalization it expands to one signed `integration_mode_change` record per
berth for the invitee — `core_mode` on Core, `other_mode` elsewhere — reusing
`_append_integration_mode_change` (extracted from `set_teammate_integration_mode`),
which also writes the `berth_role` projection.
This fixes a documented mismatch: the old `_role_to_core_berth_role` gave a
contributor `read-only` on *every* berth; `architecture.md` specifies
`proposal-only` on Core and `automatic` elsewhere.

Deliberate authority asymmetry in the expansion: `set_teammate_integration_mode` requires its author to hold automatic standing on the berth it changes, but finalization's expansion skips that check.
The finalizer's authority is the met quorum, not per-berth standing — they may legitimately grant the invitee `automatic` on a berth where they themselves are only `proposal-only`.
If a future slice wants finalization to respect per-berth authority, that predicate moves into the shared builder with a caller opt-out.

### Drift is deliberately strict this slice

`constitution_snapshot` covers all berth roles, so adding a berth mid-proposal
changes the digest and endorsement/finalization refuse — "expand across the
current berth set" only ever sees the berths the endorsers saw.
Narrowing drift to admission-relevant authority is a live research question
(follow-up notes, pointed at #166).

### Revocation is a projection, not a record

`revoke_invitation` inserts an `admission_revocation` row (a mutable
disposition) rather than mutating the proposal; `_admission_status` reads it as
`invalidated`. A signed revocation record is out of scope (follow-up notes).

## Divergences from `Documentation/team-constitution.md`

Cross-checked column-by-column; three intentional divergences found.
Two were doc errors and the doc was amended on this branch to match the implementation:

1. **`invitee_device_public_key` placement.**
   The doc listed it as an `admission_proposal` column, but that key does not exist until acceptance — it lives only on `admission_acceptance` (where the doc also, correctly, uses it as the self-certification key).
   The proposal is signed at creation and cannot embed it.
2. **Signed `mode_plan` on the proposal.**
   The doc said the preset is "not a field on the proposal itself"; the implementation stores the signed per-berth expansion rule (mode vocabulary, never the preset name) on the proposal, because the plan determines granted authority and must carry the inviter's signature.
   Mode *facts* are still established only by `integration_mode_change` records at finalization.

One divergence remains, deliberately: **interim anchor** — like `integration_mode_change`, records use the phase-1 `constitution_digest`/`constitution_snapshot_json` stand-in, not the target `anchor_frontier` (#166).

## Repo-integrity notes

- Zero new canonical-bytes idioms: all four types go through
  `wrasse_trust.constitution`. This branch *removes* the admission-local
  `_approval_payload`/`_finalization_payload`/`_proposal_transcript_*` signing
  helpers and the `_governance_snapshot`/`_governance_digest` pair.
- `set_teammate_integration_mode` and admission finalization now share one
  record-builder (`_append_integration_mode_change`).
- `USER_SCHEMA_VERSION` 62 → 64, no migration (pre-alpha stance); `steward_approval`
  dropped, three record tables + one revocation projection + scan indexes added.
- `sign_steward_approval` → `endorse_admission`, no compat shim; the HTTP route
  path `/approve` is unchanged (only the handler internals moved).
- Append-only audit:
  `grep -rniE '\b(UPDATE|DELETE FROM)\s+(admission_proposal|admission_acceptance|endorsement|finalization)\b' packages/small-sea-manager/small_sea_manager/`
  is clean (0 hits). Scoped to the package *source* deliberately: the tamper
  micro tests in `tests/test_admission_records.py` mutate these tables on
  purpose, so widening the scope re-flags them by design, not by defect.
