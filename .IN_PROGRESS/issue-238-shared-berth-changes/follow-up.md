# Issue follow-up after the design decision

No implementation handoff is ready; [plan.md](plan.md) holds the remaining questions and validation obligations.
These are local proposals, not posted issue changes.

An earlier tentative split put established-team Core implementation before broader integration and reports; that split remains open pending the model comparison and design review.
Any eventual scope must account for creation, invitations, changes, repair, integration, generic publication and courier delivery, or explicitly identify which paths remain outside the contract.
Concrete scope follows the accepted design, not today's helpers or tables.
The human-resolution walkthrough is done (Move 9); review of the Move 10 candidate calls for comparing resolution evidence and succession representations before extending the runtime probe.
Automatic recovery from sibling disagreement is still not an assumed acceptance requirement.

- **#238:** record the decision, rejected alternatives, evidence, limitations and agreed implementation split.
  Keep it open until runtime correctness requirements are implemented and validated.
- **#224:** if public predecessor links are accepted, obtain the issue owner's agreement and explain the substantive change and diagnostic/privacy tradeoff.
  Public multi-parent ancestry specifically revisits the recorded rejection of a peer-verifiable selection DAG; explain why that additional evidence belongs in the public contract and what it reveals.
  A wire change alone is not an ownership or trust change.
  The model comparison narrows the ask: what a resolution retires is the fact that must be public for a teammate to conclude anything, and a separate resolution record makes that disclosure separable from ordinary succession, at the cost of two artifacts that must travel together.
- **#139 / #235:** record actual dependencies and interfaces for arbitrary app berths and Core/Files use.
  Their app provisioning UX and Files capstone scope remain separate.
- **#237:** preserve the distinction between shared accounts and device-local credential repair when discussing overlap.
- **Independent defects:** prepare focused issue proposals for demonstrated problems outside the agreed scope.
  No duplicated-selector-policy defect was found; provider migration, old-location cleanup and speculative recovery remain outside this work.
- **Sibling-rotation wedge (new, Move 8):** two of one participant's devices rotating the same berth's route while disconnected produce a NoteToSelf state that cannot be merged.
  Integration fails on `UNIQUE constraint failed: berth_cloud_allocation.berth_id`, and the refused device stays diverged through refresh, integrate, reconcile and a further rotation while reporting `route: ready`.
  Reproduced by `probes/probe_publication_adoption.py`; evidence in [notes.md](notes.md).
  Frame the proposed issue around visibility of the pause and a meaningful way to apply a human choice, linked to #238.
  Integration refusal by itself may be intended behavior; neither a counter nor automatic merging is a prescribed fix.
  Move 9 adds what the issue should ask for: the refusal reports a commit SHA and no domain conflict, one berth's disagreement stops the whole NoteToSelf channel in both directions, and the demonstrated resolution requires a raw row deletion.
  Ask for an effective choice over the reviewed alternatives that preserves unrelated work from both sides; withdrawal is a candidate mechanism, not a prescribed API.
  Keeping the blocked device's own choice is not expressible at all, since `reconcile_team_route` has no location parameter.
  Distinguish replay of an already-resolved disagreement from a fresh deliberate conflict, which may legitimately pause again.
  Reproduced by `probes/probe_human_resolution.py`.

After acceptance, put the appropriate semantics in Manager/Hub specs and architecture, clearly separating designed from implemented behavior and removing unsupported safety claims.
The two already-completed Hub spec corrections have their own status in [doc-fixes.md](doc-fixes.md).
