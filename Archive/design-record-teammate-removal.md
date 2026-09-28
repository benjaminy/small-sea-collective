# Task 72: teammate removal

All code citations refer to committed `main`.
Below, `M` means `packages/small-sea-manager/small_sea_manager/`.

**Removal should withdraw current authority without deleting its evidence.**
Authentic events remain stored; each device computes removal’s effect under its own adopted anchor and policy.
No event declares a team-wide finalized membership state.

## 1. Event

Add `teammate_removed` with payload:

```json
{
  "version": 1,
  "team_id": "<hex>",
  "author_teammate_id": "<hex>",
  "teammate_id": "<hex>"
}
```

The author’s linked team-device key signs the existing `v0` envelope.
Do not permit workhorse keys to remove teammates.
Parents are every current stored head, as `append_local_event` already implements.
No timestamp, certificate list, or claimed role is needed.
The target is the teammate identity, including devices discovered later.
Sources: `packages/wrasse-trust/wrasse_trust/events.py:37`; `M/constitution_store.py:104`.

## 2. Authority and ambiguity

An unambiguously authorized automatic Core integrator may remove another teammate, including another integrator.
Replace the unsigned Core-role check with the authority view.
Today that check reads `berth_role` (`M/provisioning.py:4669`).

Arrival checks shape, team scope, signature, ID, and parents.
A well-formed, authentic but unauthorized removal stays stored and does nothing.
Malformed events retain the existing whole-source refusal rule; unauthorized events do not refuse their source (`M/provisioning.py:6853`).

Evaluate authority whenever the view changes, using the receiver’s current evidence and adopted anchor.
Neither the signer’s claimed view nor authority at an ancestor proves current authority (`M/berth_authority.py:29`).

Removal creates a dependency problem: deleting its author’s authority can invalidate the removal itself.
For this slice, **detect that problem and pause**:

- First find candidate removals using the authority evidence before applying removal effects.
- A candidate whose authority remains unambiguous after applying all candidate removals takes effect.
- If removal would invalidate or make ambiguous another candidate’s authorization, report the affected removals and authority chains as ambiguous; do not choose an evaluation order.
- A candidate that undermines its own authorization also requires human resolution.

Recompute from evidence each time.
This deliberately pauses some sequential cases too; it avoids inventing historical authority or a fixed-point winner.

## 3. Certificates, delegations, and modes

For an effective removal `R` targeting teammate `T`, exclude from current authority:

- Certificates identifying `T`, regardless of issuer.
- Delegations issued by `T`.
- Mode records granting `T` standing.
- Certificates and mode grants issued by `T` as an authority source.

Recompute dependent trust and standing.
Another teammate keeps authority only through an independent surviving chain.
Do not grandfather a grant merely because it predates removal.
This follows the existing requirement that trust and grants chain from the adopted anchor (`M/berth_authority.py:210`, `:292`).

Define `A < B` when following parent links from `B` reaches `A`.
Events `A` and `B` are concurrent exactly when `A != B`, neither `A < B` nor `B < A`.

For records affected by removal:

- `E < R`: preserve `E` as earlier evidence; it supplies no withdrawn current authority.
- `R < E`: preserve `E`; it cannot restore the removed identity.
- Concurrent: preserve both and expose their relationship; removal still suppresses the target’s current authority.

Concurrency alone does not make every old delegation a dispute.
A competing removal or an authorization dependency does trigger the ambiguity rule above.
A later merge naming both parents does not resolve that dispute (`architecture.md:61`).

Thus late delivery cannot resurrect authority.
Readmission under the same teammate ID requires a future explicit policy.

## 4. Self-removal, last integrator, and anchor

Keep self-removal unsupported in this slice, matching `M/provisioning.py:4650`.
An authentic self-removal remains stored but has no policy effect.

Do not require an automatic integrator to survive.
An otherwise effective removal may leave a berth without one; pause its automatic integration and show why.
Do not silently promote someone.

Anchor removal requires human resolution.
Today the anchor both establishes trust and receives blanket berth standing (`M/berth_authority.py:13`).
Keep its evidence, pause affected authority decisions, and require explicit local anchor selection before resuming.
Automatic anchor succession is deferred.

## 5. Projections and runtime effects

Stop deleting `teammate`.
That deletion can cascade into signed admission history (`M/sql/core_other_team.sql:83`).
Keep teammate/device metadata for identification and inspection; expose derived active, removed, or ambiguous status.

Replace per-arrival projection updates with a full rebuild after each accepted batch and local append.
Rebuild the effective `key_certificate`, `workhorse_delegation`, and `integration_mode_change` rows under the view.
Read raw evidence from events when explaining excluded records.
Never use filtered tables as the next rebuild’s input.
Current insertion-only projection is at `M/constitution_projection.py:226`.

Unsigned `berth_role` and `team_device` rows must not bypass removal checks.
All authorization and key-distribution readers must consult the effective view.

Foreign-authored records still written directly to tables need a separate evidence input until their author’s events arrive.
Do not erase them during rebuilding or fabricate wrapping signatures.

Apply event storage and projection rebuilding in one transaction.
After effective membership changes, run existing runtime reconciliation, including receiver-state cleanup and sender-key rotation.
Run it for imported removals too.
Ambiguity pauses affected integration and key distribution, while preserving local files and parked work (`M/provisioning.py:4542`, `:4703`).

## 6. Minimum demo and UI

The Files demo needs removal propagated through Core integration, followed by authority re-evaluation before merging parked work.
Show “removed” or “authority disputed” through the Hub; preserve fetched work.
Files must not interpret Constitution events itself.

Manager’s existing removal action should show target, author, event ID, local effect, and relevant conflicting evidence.
Replace its unconditional success notice with the actual outcome (`M/web.py:1087`).

Defer quorum removal, readmission, anchor succession, general conflict-resolution events, history acceptance tools, incremental rebuilding, and revoking previously obtained plaintext.
A visible pause is sufficient for unresolved cases (`architecture.md:214`).

## 7. Micro tests

- `test_removal_envelope`: verifies payload scope, device signature, and current-head parents.
- `test_unauthorized_removal_kept`: retains authentic evidence without changing authority.
- `test_bad_removal_refuses_source`: rolls back that source’s batch only.
- `test_removal_suppresses_all_target_keys`: includes later-discovered linked devices.
- `test_removal_dag_relations`: covers ancestor, descendant, and precisely concurrent records.
- `test_removal_rebuild_preserves_evidence`: retains events, metadata, and admission history.
- `test_removal_dependency_ambiguity`: competing removals pause without selecting a winner.
- `test_self_anchor_and_last_integrator`: establishes each boundary rule.
- `test_imported_removal_reconciles_runtime`: stops affected merges and future key distribution.
- `test_removal_arrival_order_independent`: permutations, duplicates, and pending-parent promotion produce identical views for identical evidence and local inputs.

## 8. Owner questions

1. **Conservative dependency pauses?** Recommend yes; defer automatic resolution even for sequential removals that invalidate earlier authorization.
2. **Self-removal now?** Recommend retaining the prohibition; add a separate leave policy later.
3. **Anchor removal?** Recommend explicit local resolution, with no automatic successor.
4. **Same-ID readmission?** Recommend deferring it; ordinary certificates must never undo removal.
## Owner answers (2026-09-28)

All four recommendations accepted.
Readmission, for future design: no rehydrating the same cryptographic identity.
Instead, a grace period for undoing a removal (like unsending an email), and a way to link a new identity to the old one.

## Implementation note (task 75, 2026-09-28)

Section 5 asked for a full projection rebuild under the view after each batch.
Task 73 removed the last path that wrote foreign records directly, so the tables are now a pure projection of authentic events.
Task 75 therefore keeps them unfiltered as evidence and makes membership readers ask the view instead of rebuilding filtered tables.
This meets section 5's goals (evidence kept, readers follow the view) with less machinery; revisit if a reader cannot reasonably consult the view.

## Owner approval (2026-09-28)

The simplification above is accepted, as is task 75's narrower scope.
Removal is not urgent; task 75 took one step and recorded the unknowns it exposed, rather than finishing the feature.
