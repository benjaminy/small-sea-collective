# Issue follow-up after the design decision

The [implementation handoff](handoff-sibling-disagreement.md) is implemented and all four closeout checks have runtime evidence; final results are in [notes.md](notes.md#branch-closeout-2026-09-09).
These are local proposals, not posted issue changes.
The scope below can now be written against implemented behavior rather than a design.

An earlier tentative split put established-team Core implementation before broader integration and reports; eventual issue scope should now follow the reviewed human-resolution handoff and its validation results.
Any eventual scope must account for creation, invitations, changes, repair, integration, generic publication and courier delivery, or explicitly identify which paths remain outside the contract.
Concrete scope follows the accepted design, not today's helpers or tables.
The human-resolution walkthrough and Move 10 comparisons remain historical evidence.
The accepted 2026-09-08 direction permits evidence gathering from relevant admissible sources while normal operations for the affected berth pause on the detecting device until a human decides.
That path is implemented; normal multi-location operation remains deferred.
Automatic recovery from sibling disagreement is still not an assumed acceptance requirement.

- **#238:** the behavior is implemented; the issue can now describe what exists rather than what is intended.
  Record the evidence-gathering exception, explicit human resumption, local pause scope and any broader storage-imposed block.
  Distinguish fetching evidence from live integration, write placement and normal operation.
  Keep practical retirement and discovery limits explicit without requiring global agreement or elaborate retirement mechanisms.
  Add what the 2026-09-08 probe established: the read path selects the newest announcement and asks exactly one store, with no fallback when that store is empty or incomplete, even when the reader holds an authenticated announcement for a complete one;
  a single chain walk is not bound to the location it began at, because the Hub selects again on every object request;
  a device's own writes already stay at its own location when a sibling announces a newer one;
  and reading a sibling's location requires that sibling's sender key to have been distributed before it published, since a distribution carries the chain at its current iteration and none of the earlier message keys.
  Keep it open until the resulting runtime correctness requirements are implemented and validated.
- **#224:** explain that the discussion reopens exactly-one-route cardinality while retaining participant ownership, equal trusted siblings and NoteToSelf coordination.
  Multiple locations do not imply per-device ownership or new device authority.
  Do not require a public selection DAG for investigation or local human resolution; the earlier need for public retirement evidence depended on readers having to recognize one globally resolved route.
  If the surviving design nevertheless requires public predecessor or resolution links, explain the concrete obligation and disclosure cost and obtain the issue owner's agreement to that substantive change.
  A channel-use stop does not revoke a signer or promise provider erasure.
  The reviewed handoff removes allocation uniqueness while disagreement is open and restores one live allocation on explicit resolution.
  Losing rows are deleted from shared NoteToSelf; a paused device retains its historical alternatives and still needs its own explicit decision.
- **#139 / #235:** record actual dependencies and interfaces for arbitrary app berths and Core/Files use.
  Their app provisioning UX and Files capstone scope remain separate.
- **#237:** preserve the distinction between shared accounts and device-local credential repair when discussing overlap.
- **Independent defects:** prepare focused issue proposals for demonstrated problems outside the agreed scope.
  No duplicated-selector-policy defect was found; full provider migration, provider-object cleanup and speculative recovery remain outside this work.
  Preserve minimal-fuss provider migration as an eventual goal.
  Record future adoption-time security checks over old and incoming placement state, with human intervention when needed, without designing them in this branch.
  Honor explicit read and write stops, with possible missed work explicit; general retirement workflows are deferred and provider-controlled retention is not an erasure obligation.
- **Sibling-rotation wedge (historical Move 8/9 evidence, fixed by this branch):** two disconnected rotations formerly violated the unique allocation index during NoteToSelf integration.
  The refusal blocked unrelated rows and both directions of the NoteToSelf channel while reporting `route: ready`; effective repair required a raw database deletion and could not express either existing choice symmetrically.
  The old witnesses remain in `probes/probe_publication_adoption.py` and `probes/probe_human_resolution.py` as historical evidence.
  Use this as the before-state in #238, not as a new defect issue.
  The implemented path adopts unrelated work, holds a visible berth pause, and supports explicit choice of either existing allocation while preserving alternatives locally.

The Manager and Hub specs carry the implemented semantics, including the restoration limit.
The architecture already states the human-scale coordination principle; the branch-specific reasoning is preserved in `design-record.md`.
The two already-completed Hub spec corrections have their own status in [doc-fixes.md](doc-fixes.md).

## Added by the step 5 implementation

The latest-commit review found and fixed two inspection defects: mutable account routing could redirect a candidate walk, and inspection refs could become visible outside the reservation used by resolution while remaining absent from the reviewed digest.
Focused micro tests now cover route mismatch, both observation/decision orderings, and recovery from ref publication followed by an interrupted report update.
The [verification closeout](handoff-sibling-disagreement.md#verification-closeout) completes the adoption, allocation/locator/account race, publication-retry and older-state-restoration schedules.

- **#238, scope closure:** all four formerly open verification checks now have runtime results.
  Reference `devtools/sandbox/scenarios/scenario_berth_source_resolution.py` and `packages/small-sea-manager/tests/test_berth_source_closeout.py` and state the limits of exception-based interruption and controlled local service schedules.
  Record that restoring an older device-local snapshot can erase the only pause/choice record; competing live rows still trigger refusal and reprojection, but a sole surviving row cannot reconstruct a lost decision.
  Preserve this limitation without proposing a recovery subsystem as part of this branch.
- **#224:** the exactly-one-route cardinality is now "one live allocation per berth once the placement is settled", enforced by a Manager projection and a Hub check rather than a unique index.
  No public selection DAG was introduced and no retirement record exists, as the reframe argued.
- **Announcement gate on investigation (new):** the reviewed handoff kept an admissible-announcement check on the investigation path.
  The runtime shows that makes the sibling's candidate unreadable, because its announcement travels in a chain published to the location this device cannot reach.
  The gate is off that path and the status is recorded as evidence instead.
  Worth an issue note, because it changes what "admissible source" means for a read that never writes.
- **Placement and publication are separate (new):** choosing a sibling's location does not make its Core chain integrable.
  `push_team` reports the ordinary divergence afterwards.
  Any issue text about "resuming" should not imply otherwise.
- **Ambiguity reaches ordinary management operations (new):** `get_berth_cloud_allocation_for_berth` raises rather than picking, so `create_invitation` fails while a berth is ambiguous, since an invitation must name a route.
  That is correct but it is a raised error, unlike `reconcile_team_route`, which reports `berth_source_paused`.
  Consider a focused issue for making the reporting consistent.
