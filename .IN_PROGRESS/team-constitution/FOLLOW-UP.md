# Follow-up: split issue #163 into an epic plus sub-issues

Issue #163 ("Implement the Team Constitution: signed append-only teammate history") describes what is really an epic: six or seven independently-shippable pieces plus design questions the doc explicitly leaves open.
This branch lands the first slice (shared envelope + `integration_mode_change`), so the remaining work should be filed as sub-issues and #163 rewritten as the tracking issue.

**Status: done (2026-07-01).**
The sub-issues below were filed as #164–#169 and #163 was rewritten as the tracking issue; the drafts are kept here for the design rationale.

One refinement to the split as originally sketched: `admission_proposal`'s move onto the shared envelope belongs with the endorsement/finalization generalization (they are one flow in `Documentation/team-constitution.md`), not with the mechanical migration of the other two pre-envelope record types.
The drafts below reflect that.

---

## Rewrite of #163 (convert to tracking/epic issue)

Keep the existing body's "Why this is the foundation" and "Related issues folding into this" sections — the sequencing rationale is the issue's real value.
Replace the implementation expectation with a checklist of sub-issues:

> ## Status: tracking issue
>
> The field-level schema is `Documentation/team-constitution.md`.
> Implementation is split into independently-shippable slices:
>
> - [x] Shared signing envelope + `integration_mode_change` vertical slice (PR for the `team-constitution` branch)
> - [ ] Admission onto the shared envelope: `admission_acceptance`, generalized `endorsement`/`finalization` (#164) — unblocks #162
> - [ ] Migrate `key_certificate` and `teammate_berth_storage_announcement` onto the shared envelope (#165)
> - [ ] `anchor_frontier` / `predecessor_record_id`, replacing the interim `constitution_digest` (#166)
> - [ ] Projection rebuild: `constitution_skeleton_at` / `constitution_projection` replacing direct mutation (#167) — folds in #57
> - [ ] `exclusion` record type (#168)
> - [ ] `staleness_observation` + Constitution retention exemption (#169)
>
> Deliberately not filed as implementation issues yet (design still open per the doc's "Deliberately left open" section):
> prepared recovery (`prepared_recovery_registration` / `recovery_event` ceremony mechanics) and the PII types (`display_name_claim`, `teammate_unification_claim` commitment scheme).
> Those get issues when the corresponding `open-architecture-questions.md` items are settled.

---

## Sub-issue drafts

### #164 — Admission onto the shared envelope: `admission_acceptance`, generalized `endorsement`/`finalization`

Labels: `priority:high`, `type:task`.
Blocks #162.

> Part of #163.
> Move the one quorum-gated Constitution flow onto the shared envelope, per `Documentation/team-constitution.md` ("Generalized: the one quorum-gated flow").
>
> - `admission_proposal`: adopt the shared envelope columns and signing helper (`wrasse_trust.constitution`).
>   Drop `role`; the Manager UI's admin/contributor/observer preset becomes a set of `integration_mode_change` records appended alongside finalization.
> - `admission_acceptance`: new append-only record replacing the mutated acceptance columns on the proposal row.
>   Self-certifying signature (verified against the embedded `invitee_device_public_key`) — the schema's one exception to "the signer already has standing."
> - `endorsement`: generalized from `admin_approval`.
>   Keeps `subject_record_id` (FK) and `subject_digest` (content commitment, generalizing `transcript_digest`) as separate columns.
>   Dedupes by `endorsing_teammate_id`, fixing the known device-vs-teammate double-count gap in today's `UNIQUE(proposal_id, approver_device_key_id)`.
> - `finalization`: new, minimal — records that the required endorsement count was observed; only finalization makes a proposal effective.
>
> Why this is the next slice: #162 (mode-aware replication / merge-request discovery) is written in terms of `integration_mode_change` (landed) plus this generalized `endorsement`/`finalization` pair.
>
> Out of scope: `proposal_revision` (named in the doc but not fully specified), any endorsement threshold above one for non-admission actions.

### #165 — Migrate `key_certificate` and `teammate_berth_storage_announcement` onto the shared envelope

Labels: `type:task`.

> Part of #163.
> The two pre-envelope record types adopt the shared `record_id`/`record_type`/`schema_version` columns and the `wrasse_trust.constitution` signing helper, replacing the independent reimplementations of the canonical-bytes idiom in `identity.py` and `transport.py`.
>
> Per the doc: `teammate_berth_storage_announcement` is not governance-bearing, so `anchor_commit`/`anchor_frontier` stay `NULL` for it.
> `key_certificate` rows (`membership`, `device_link`, `revocation`, …) are governance-bearing and will need predecessor chains — but wiring those up is #166's job; this issue is only the envelope/helper alignment.
>
> Pre-alpha stance applies: `USER_SCHEMA_VERSION` bump, no in-place migration; existing team databases are deleted and recreated.

### #166 — `anchor_frontier` / `predecessor_record_id`, replacing the interim `constitution_digest`

Labels: `type:task`.
Depends on #164 and #165.

> Part of #163.
> The `team-constitution` branch deliberately shipped `integration_mode_change` anchored by a `constitution_digest` (live-query digest generalizing `governance_digest`) as a stepping stone, because record-to-record anchoring needs every governance-bearing type on the envelope first.
> This issue retires that stepping stone:
>
> - Add `predecessor_record_id` (nullable, self-referential per table) to each governance-bearing record table.
> - Implement `anchor_frontier` as the authoritative anchor: per-table tip `record_id`s, chain-followed to reconstruct `constitution_skeleton_at(frontier)` from local records only — no git checkout, no live-head dependency.
> - `anchor_commit` remains informational only.
> - Distinguish malformed (frontier cannot be chain-followed consistently) from stale (frontier is no longer the current tip), matching the freshness semantics `admission_proposal` already has.
>
> The exact wire representation (one tip per table vs. rolling accumulator vs. Merkle) is listed under "Deliberately left open" in the doc; settling it is part of this issue's design phase.

### #167 — Projection rebuild: `constitution_skeleton_at` / `constitution_projection`

Labels: `type:task`.
Folds in #57.

> Part of #163.
> Implement the two deterministic replay functions from `Documentation/team-constitution.md` ("Replay and projections"):
> `constitution_skeleton_at` (governance-only, feeds anchor digests) and `constitution_projection` (rebuilds the mutable convenience tables `teammate`, `berth_role`, `invitation`, `team_device`).
>
> Manager write paths ("set integration mode," "remove teammate," "revoke device," …) become "append the Constitution record, then rebuild the affected projections," replacing today's direct row mutation.
> `set_teammate_integration_mode` (landed in the `team-constitution` branch) currently updates its `berth_role` projection row directly and should be converted.
>
> This answers #57's question — trusted device sets are a projection over admitted cert history — generalized to all four mutable tables, so #57 closes into this.
>
> Both functions must be deterministic over the record tables alone: no wall-clock reasoning, no reliance on row-arrival order.

### #168 — `exclusion` record type

Labels: `type:task`.

> Part of #163.
> Single-signer governance record per the catalog: `excluded_teammate_id`, `reason_commitment` (signed), `reason_payload` (separable), valid when signed by a current automatic Core integrator.
> Matches the Manager spec's existing "remove teammate" description as a unilateral, socially-adopted-or-not act.
>
> Note: the *shape* (commitment/payload split) is fixed by the envelope's separable-payload mechanism and is implementable now; only the commitment *construction* is open.
> Do **not** land records carrying a placeholder commitment construction: Constitution records are never deleted, so a weak commitment over a low-entropy exclusion reason (e.g., an unsalted hash) is permanently brute-forceable from any Core snapshot — a bad placeholder is un-fixable, not temporary.
> Preferred path: settle the construction as part of this issue — a standard salted commitment (`sha256(salt ‖ canonical payload bytes)` with a random ≥128-bit salt stored inside the separable `reason_payload` and discarded with it) is textbook and needs a brief review, not novel cryptanalysis.
> If it truly must wait, the fallback is the schema/authorization skeleton only, with `reason_commitment` nullable and unpopulated — never a stored stand-in commitment.
>
> Small once #164 through #167 exist; the `integration_mode_change` slice is the template.

### #169 — `staleness_observation` + Constitution retention exemption

Labels: `type:task`.

> Part of #163.
> Self-signed, explicitly non-authoritative testimony record per the catalog:
> `observing_teammate_id`, `observed_teammate_id`, `observed_berth_id`, `last_observed_signal`, `local_update_counter_or_elapsed`, `warning_horizon`.
> Cannot exclude anyone, advance a retention horizon, or declare finality; disagreement between observers is not a malformed state.
>
> Pairs with the retention-horizon work (`architecture.md`, "Retention Horizons and Staleness"): Constitution record tables are exempt from retention trimming (nothing is ever deleted), while separable payloads are the designed drop/encrypt point.
> The staleness-to-checkpoint rule is deliberately left open; this issue covers only the record type and the retention exemption, not that rule.

---

## Not filed (design-blocked, per the doc's "Deliberately left open")

- **Prepared recovery** (`prepared_recovery_registration` / `recovery_event`): the ceremony's anti-replay/rollback mechanics, private-side key format, storage, and rotation are open in `open-architecture-questions.md` (Section 5).
  File an implementation issue only once that design lands.
- **PII types** (`display_name_claim`, `teammate_unification_claim`): the commitment scheme construction needs review, tracked in `open-architecture-questions.md`.
  The envelope's signed-commitment/separable-payload split (landed in this branch's helper design) is the fixed shape they'll use.
  The expected construction is small — a salted commitment as described under #168, not novel cryptography — so once #168 settles it, these become fileable implementation issues gated only on that decision.
- **`proposal_revision`**: named in the doc under `endorsement`, columns not specified; specify it when #164's work makes it concrete.

## Other follow-ups from this branch

- The known device-vs-teammate endorsement dedupe gap in today's `admin_approval` (`UNIQUE(proposal_id, approver_device_key_id)`) is fixed by design in #164's `endorsement` record; no separate issue needed.
- #150 (cross-member berth storage announcement delivery test) stays its own issue; `teammate_berth_storage_announcement` is only touched mechanically by #165.
