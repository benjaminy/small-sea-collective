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
