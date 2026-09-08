# Issue follow-up after the design decision

No implementation handoff is ready; [plan.md](plan.md) holds the remaining questions and validation obligations.
These are local proposals, not posted issue changes.

An earlier tentative split put established-team Core implementation before broader integration and reports; that split remains open pending the runtime probe, the wedge decision and design review.
Any eventual scope must account for creation, invitations, changes, repair, integration, generic publication and courier delivery, or explicitly identify which paths remain outside the contract.
Concrete scope follows the accepted design, not today's helpers or tables.
The human-resolution walkthrough and Move 10 comparisons remain historical evidence.
The 2026-09-08 direction instead probes multiple readable locations against the runtime and decides the sibling wedge before further resolution-representation or runtime work.
Automatic recovery from sibling disagreement is still not an assumed acceptance requirement.

- **#238:** record the multiple-location comparison, the distinction between reads and write placement, practical retirement, discovery limits, rejected alternatives and the agreed implementation split.
  Distinguish changed obligations from implementation fixes: a reader need not resolve route succession merely to fetch valid work.
  Add what the 2026-09-08 probe established: the read path selects the newest announcement and asks exactly one store, with no fallback when that store is empty or incomplete, even when the reader holds an authenticated announcement for a complete one;
  a single chain walk is not bound to the location it began at, because the Hub selects again on every object request;
  a device's own writes already stay at its own location when a sibling announces a newer one;
  and reading a sibling's location requires that sibling's sender key to have been distributed before it published, since a distribution carries the chain at its current iteration and none of the earlier message keys.
  Keep it open until the resulting runtime correctness requirements are implemented and validated.
- **#224:** explain that the discussion reopens exactly-one-route cardinality while retaining participant ownership, equal trusted siblings and NoteToSelf coordination.
  Multiple locations do not imply per-device ownership or new device authority.
  Start the new comparison without a public selection DAG; the earlier need for public retirement evidence depended on readers having to recognize one resolved route.
  If the surviving design nevertheless requires public predecessor or resolution links, explain the concrete obligation and disclosure cost and obtain the issue owner's agreement to that substantive change.
  A channel-use stop does not revoke a signer or promise provider erasure.
- **#139 / #235:** record actual dependencies and interfaces for arbitrary app berths and Core/Files use.
  Their app provisioning UX and Files capstone scope remain separate.
- **#237:** preserve the distinction between shared accounts and device-local credential repair when discussing overlap.
- **Independent defects:** prepare focused issue proposals for demonstrated problems outside the agreed scope.
  No duplicated-selector-policy defect was found; full provider migration, provider-object cleanup and speculative recovery remain outside this work.
  Practical read and write stops are now in behavioral scope, with possible missed work explicit; provider-controlled retention is not an erasure obligation.
- **Sibling-rotation wedge (new, Move 8):** two of one participant's devices rotating the same berth's route while disconnected produce a NoteToSelf state that cannot be merged.
  Integration fails on `UNIQUE constraint failed: berth_cloud_allocation.berth_id`, and the refused device stays diverged through refresh, integrate, reconcile and a further rotation while reporting `route: ready`.
  Reproduced by `probes/probe_publication_adoption.py`; evidence in [notes.md](notes.md).
  Frame the proposed issue around preserving alternatives and unrelated work plus meaningful control of writes, linked to #238.
  Reassess whether separating retained locations from the write choice removes the schema refusal before prescribing a pause interface.
  The refusal is the unique index `idx_berth_cloud_allocation_berth`; the 2026-09-08 multiple-location reframe does not change it, and plan step 4 asks a human to choose between removing it and keeping it as a visible pause.
  Integration refusal by itself may be intended behavior; neither a counter nor automatic merging is a prescribed fix.
  Move 9 adds what the issue should ask for: the refusal reports a commit SHA and no domain conflict, one berth's disagreement stops the whole NoteToSelf channel in both directions, and the demonstrated resolution requires a raw row deletion.
  Ask for an effective choice over the reviewed alternatives that preserves unrelated work from both sides; withdrawal is a candidate mechanism, not a prescribed API.
  Keeping the blocked device's own choice is not expressible at all, since `reconcile_team_route` has no location parameter.
  Distinguish replay of an already-resolved disagreement from a fresh deliberate conflict, which may legitimately pause again.
  Reproduced by `probes/probe_human_resolution.py`.

After acceptance, put the appropriate semantics in Manager/Hub specs and architecture, clearly separating designed from implemented behavior and removing unsupported safety claims.
The two already-completed Hub spec corrections have their own status in [doc-fixes.md](doc-fixes.md).
