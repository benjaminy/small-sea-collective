# Candidate contract: human resolution of a sibling berth disagreement

Branch: `issue-238-shared-berth-changes`.
Status: Move 10 candidate contract, under revision after review of `5b18218`.
It is not an implementation handoff and proposes no runtime schema.
The decision ledger and open questions remain in [plan.md](plan.md); the repair-side terms it uses are defined in [contract-sibling-repair.md](contract-sibling-repair.md).
The single-predecessor proposal and the classification of missing ancestry below have counterexamples; see the [review and next research step](notes.md#review-of-move-10-and-next-research-step-2026-09-07).
The body preserves the reviewed candidate, not corrected semantics.
The next step is model-level comparison under the plan, before extending the runtime probe; neither public multi-parent ancestry nor removal of the counter is accepted.

This contract answers the four questions the [Move 9 review](notes.md#review-of-move-9-2026-09-07) left open.
Three were settled by the 2026-09-07 discussion and are recorded here with their costs.
The fourth is argued below and proposed, not settled.

## What a disagreement is

Two selections for one berth are **competing** when a device holds both, neither names the other as an ancestor through predecessor links it holds, and they name different route content.
Competing selections are an observed conflict, not incomplete evidence.
A device that has not yet received a sibling's selection is not in disagreement; it is uninformed, and this contract says nothing about it.

Detection is the existing publication refusal plus the parked head.
Move 9 established that the evidence survives: the competing allocation and its predecessor are recoverable from the parked ref's `core.db` blob.
Nothing in the Manager API reads it, which is the first gap this contract asks the runtime to close.

## The pause

**Scope: the whole NoteToSelf channel, in both directions.**
An unresolved disagreement on any berth stops sibling allocation traffic entirely, including unrelated account and device-management changes.
This is the loud choice, taken deliberately: a research project benefits more from a visible stop that provokes the next iteration than from a narrow one that lets a dispute sit unnoticed.
It also preserves the existing refusal boundary without partially integrating a shared database.
The cost is recorded rather than mitigated: Move 9 demonstrated blocked account propagation, and this contract accepts that delay.
Narrowing to the disputed berth is reopened by a blocked workflow whose consequence is shown to be unacceptable, not by the delay alone.

**Team publication stops too.**
While a disagreement is unresolved, the device publishes nothing to the team Core chain: no new announcement for the disputed berth, no replay of already-signed bytes for it, and no other team publication.
Move 9 found the opposite behavior in the runtime — `push_note_to_self` is refused while `push_team` succeeds — so a teammate can be told about a route the publishing device's own sibling will never learn it selected.
Stopping both channels removes that split by the loudest available means.
The narrower containment rule (stop only announcements naming the disputed berth) is the obvious alternative and is deliberately not taken yet; it is reopened by a demonstrated need to keep unrelated team work moving during a pause.

The pause has no deadline and requires no eventual merge.
A person may leave their own device paused indefinitely.

## The resolution operation

One operation, invoked by a person after reviewing a report.

**The report** names the berth, both competing allocations with their route content and predecessors, which device selected each, and the operations currently paused.
Producing this report requires reading the parked blob, which is the infrastructure Move 9 showed is missing.

**The choice** is either preserved allocation — the incoming sibling's, or the one this device already holds.
Move 9 established that only the first is expressible today: withdrawal has no Manager operation and `reconcile_team_route` deliberately takes no location, so keeping this device's own location cannot be requested at all.
The probe stands in for both gaps; see "What the probe must do".

**The binding** is the load-bearing part.
The operation names the exact evidence the person reviewed — the disagreement's identity, both selection IDs, and the revisions the report was drawn from — and refuses if any of that changed between report and application.
A refusal produces a new report and a new choice.
This is what prevents a conflicting selection arriving mid-review from silently redirecting an approved choice into something the person never saw.
If the runtime cannot express that binding, building it is in scope for the eventual handoff.

**The effect** is that the chosen allocation becomes this device's selection, unrelated work from both sides survives, and the channel resumes.
The chosen allocation gets a successor selection minted by the resolution, so the device's own announcement stops naming a location it gave up.
Move 9 needed three operations for this (withdraw, integrate, reconcile) and the runtime offered two; this contract asks for one operation boundary, not necessarily one transaction framework.

Withdrawal is not proposed as an interface.
It was sufficient in Move 9's schedule, but it names neither the accepted alternative nor the reviewed evidence, which is exactly what the binding above requires.

## Succession ordering: proposed simplification

The Move 9 review asked whether the pause removes the need for automatic counter-based priority.
It does not remove all of it, because two different obligations were being served by one mechanism.

- **Ordinary succession.**
  A participant rotates X to Y; a teammate receives Y first and X late.
  Nobody disagrees.
  Pausing here would demand a human decision for every routine rotation delivered out of order, which is a worse outcome than the ranking it replaces.
  Move 1 already established that signing time cannot decide this, and the shipped selector ranks on `announcement_id` (`packages/wrasse-trust/wrasse_trust/transport.py:123`), which is not succession evidence at all.
- **Competing branches.**
  Move 3 established that counters do not serve this: unequal counters do not establish agreement, equal counters still conflict, and detection requires retained predecessor history regardless.

So the counter is load-bearing only for the first.
The proposal is that **the predecessor link serves it instead, and the scalar counter and its greatest-observed high-water mark are dropped.**
A teammate holding X and Y where Y names X as predecessor knows Y replaced X, with no clock, no counter and no dependence on delivery order.
Where the links do not connect the two — a shared predecessor, or a gap where the teammate never received the intermediate — that is the competing case, and the teammate reports rather than ranks.

What this buys: one public field instead of two, and the ordering rule stops being a separate mechanism from the fork-detection rule that Move 3 showed is needed anyway.
It also removes the high-water-mark restore rule, which existed to keep a monotone scalar from being replaced by an old snapshot.

What it costs, stated plainly so the trade is visible:

- A counter orders two selections across a gap; a predecessor chain does not.
  A teammate that missed an intermediate announcement pauses under this proposal where the counter-based candidate would have routed.
  Whether that gap is common depends on Core chain delivery, which is unprobed.
- It makes public predecessor links a requirement rather than the advisory diagnostic aid the Move 4 entry preferred.
  That raises the metadata exposure question — traffic analysis and correlation across berths and teams, still unpriced — and it touches the [#224 decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) that deferred allocation-generation binding in announcements, so it needs the issue owner's agreement.

Reopened by: evidence that gaps in teammate announcement delivery are ordinary rather than exceptional, or a decision that public predecessor exposure is unacceptable.
Either would restore the counter as the ordering mechanism.
Neither restores it as a resolution mechanism.

## What the probe must do

Move 10 probes this contract against current code, standing in for the two operations the runtime lacks, as Move 9 stood in for the withdrawal.
The stand-ins are probe actions and not proposed APIs.

1. Choose B's existing location, with unrelated changes on both A and B.
   The selected location and both unrelated changes must survive publication and sibling adoption.
   This is the schedule Move 9 could not run.
2. Restart and replay the already-resolved histories, separately from making new selections.
   Replay must not silently reverse the choice.
   A genuinely new deliberate disagreement may pause again; Move 9 established that is not a durability failure.
3. Introduce additional conflicting evidence between report and application, and check that the operation refuses rather than applying to changed evidence.
4. Check that team publication is in fact stopped, against the Move 9 observation that it is not.

Delayed announcements at a receiving teammate, both NoteToSelf/Core delivery orders, and restore remain required before a handoff and are not in this probe's scope.
The succession proposal above is not testable here: no announcement carries a predecessor or a counter today, so it stays a model-level claim until the runtime does.

## Assumptions to validate later

- That a report can be produced from the parked blob for arbitrary berth counts and providers, not just Move 9's single berth.
- That the binding refusal is expressible at the operation boundary rather than detected afterwards, which is the same requirement the retry rule already carries.
- That resolution establishes appropriate succession.
  Move 9 established this cannot be checked against current code, since deliberate human reselection has no runtime expression.
  The probe's stand-in tests the contract, not the runtime's ability to honor it.
