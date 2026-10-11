# Decisions after T1-T3, 2026-10-10

T1-T3 (`encoding`, `policy`, `history`, `reconcile`, `view` under `small_sea_manager/spine/`) were built and staged with 122 micro tests.
Their authors raised four points; the owner answered them.

1. **Children of rejected events.**
   The owner says the Manager should probably reject events that build on an event a valid reconciliation rejected.
   This replaces the plan's rule that such children "stay visible and blocking" (section 4, View).
   The device keeps the child as an object marked invalid ("builds on a rejected event"), so the evidence survives and the team is not blocked.
   A branch that *adopts* what another rejected (for example a join adopting Y) still goes to a person, as `reconciliation-design.md` says.
   The owner notes this is a policy question: the protocol could work either way, and we have to start somewhere.
   - Decided (owner agreed to the orchestrator's proposal): an event whose parent is known only as a copy inside an *invalid* reconciliation is a different case.
     Nobody validly rejected that parent.
     Proposal: treat the copy as a received event with its own disposition, and let ancestry pass through it, so a malformed reconciliation cannot sink honest events.
2. **Resolved tips that are ancestors of other resolved tips.**
   Allowed, as now.
   The owner: a reconciliation should in general be able to name any published tip and say what it does with it.
3. **Reconciliations while followed.**
   A followed view lists "reconciliation" among the kinds it may publish; such a reconciliation would fail its own shape check.
   Left as is; not important to nail down.
4. **Untested branches in `reconcile.py`.**
   No micro test covers an R whose kind is not "reconciliation" (decoding probably prevents it from reaching the checks) or a base that is itself invalid.
   Noted here rather than tested now.

## After R1

5. **New children of adopted events published before the reconciliation was seen.**
   Example: Alice publishes X2 on X before she sees R, which adopts X.
   Owner decision: classify R plus X2 as a "split awaiting join", not a conflict; it blocks Constitution publishing but does not call for a reconciliation, and future join work picks it up.
   Not yet implemented; today's view reports a conflict (`test_spine_adversarial.py` asserts that and must change with it).
6. **Known gap.** The "builds on a rejected event" rule lives only in the view.
   Moving it into the Evaluator makes check 5 call itself in a loop, so `check_reconciliation` alone accepts a reconciliation whose base builds on a rejected event, while the view refuses it.
7. **Replay guard.** `reconcile.py` refuses non-ordinary replay steps, which the partition check already makes impossible; kept for now as protection on the most sensitive path.
8. **Envelope conflicts** (R1 finding 10) are untested and belong to T5.
