# Shared berth allocation and routes

Issue: [#238](https://github.com/benjaminy/small-sea-collective/issues/238).
Branch: `issue-238-shared-berth-changes`.
Status: research discussion; individual decisions below are settled or explicitly reopened, and no complete protocol or implementation handoff is accepted.
Moves 1–9 are complete within their recorded limits.
The Move 10 contract has been proposed and reviewed, and the model comparison in steps 1–5 below is complete; the contract records its corrections, and the comparison's checkpoint has been reviewed and its two findings addressed in `a7ce749`.
Evidence, commands and review corrections live in [notes.md](notes.md); eventual issue work lives in [follow-up.md](follow-up.md).

## Next work

The retirement-timing experiment is complete; review of `a71d129` found that its gate can be opened by another routeless record without a referencing route selection.
The gate and the history report are now corrected with focused counterexamples; see [notes.md](notes.md#move-10-review-follow-through-2026-09-07-the-corrected-gate-and-history-report).
The next deliverable is the semantic checkpoint below, which needs a human decision, before durability work.
The review and recommended sequence are in [notes.md](notes.md#review-of-a71d129-2026-09-07).
The completed comparison below supplies its starting evidence, not a runtime implementation or an accepted wire format.
The review of `5b18218` and the committee discussion are recorded in [notes.md](notes.md#review-of-move-10-and-next-research-step-2026-09-07).

1. **Preserve the counterexamples before modeling a fix.** Done in `models/model_human_resolution.py`; see [notes.md](notes.md#move-10-step-1-2026-09-07-the-counterexamples-are-executable).
   It reuses earlier model vocabulary where it fits without building a general simulation framework.
   Preserve controls that demonstrate the single-predecessor failure after resolution followed by ordinary rotation, and the clean X → Y → Z history misclassified when Y is missing.
   Add the DAG variant where a held common ancestor appears to show a fork, but a missing second-parent path later proves succession.
   Controls should pass by asserting the demonstrated failure of the old candidate; new candidates must satisfy separately stated behavioral assertions on the same schedules.
2. **Write down what each actor can actually conclude.** Done in [obligations-actor-claims.md](obligations-actor-claims.md) and the second half of `models/model_human_resolution.py`; see [notes.md](notes.md#move-10-step-2-2026-09-07-what-each-actor-may-conclude).
   For the selecting device, its sibling and a receiving teammate, specify held evidence, missing evidence, current route or pause, and permissible claims.
   Separate evidence of supersession, unresolved incompatible choices, incomplete ancestry, and contradictory signed payloads.
   A common held ancestor alone cannot establish incompatibility when missing ancestry could establish supersession.
   Distinguish remaining tips from historical forks, and retain distinct selection identities even when route content matches.
   Record a candidate resume rule: resume when new evidence removes the cause of the pause and no other pause or explicit human hold remains; missing history may remain unavailable indefinitely.
   Retrieving or inspecting evidence must remain distinguishable from integrating it into live state or publishing it.
3. **Compare representations against those obligations.** Done in `models/model_resolution_candidates.py`; see [notes.md](notes.md#move-10-steps-35-2026-09-07-comparing-resolution-representations).
   Model multi-parent supersession first because it directly addresses the counterexample.
   Compare it with a separate record of the reviewed choice and with deriving local resolution from retained Git evidence while keeping public succession separate.
   Reuse the counter-based candidate as an ordering baseline; a counter is neither evidence of human review nor a conflict-resolution rule.
   For each candidate, identify the minimum evidence each actor needs and what remains unverifiable to that actor.
   Do not assume private Git history is available to teammates, or that a signed multi-parent event proves human involvement.
   Do not require every representation to succeed: preserve the smallest counterexample or explicit unmet obligation when one fails.
4. **Run the first comparison on a bounded set of schedules.** Done in the same file.
   Resolve either side of a two-sibling fork, rotate normally afterwards, and deliver old selections late or repeatedly to a teammate.
   Exercise the missing-link and missing-second-parent schedules in both arrival orders; distinguish evidence that clears uncertainty from evidence that confirms a disagreement.
   Introduce a third conflicting selection after inspection but before application, while leaving the two reviewed selections unchanged.
   The operation must refuse before changing the selected allocation or publishing effects, preserve all alternatives, and produce a report that includes the new evidence.
   Check that merely signing a merge-shaped event cannot justify a report claiming human review.
   Record actor traces and assertions alongside outcomes so a reviewer can inspect why a candidate passes, rather than trusting its own classifier as the oracle.
5. **Review the comparison before expanding the experiment.** Written up in [notes.md](notes.md#preference-weakness-and-the-next-discriminating-experiment); the outside review of the checkpoint found two model defects, both fixed and recorded in [notes.md](notes.md#review-correction-2026-09-07).
   Produce a compact comparison in `notes.md`: correctness evidence, retained state, public disclosure, missing-history behavior, restore obligations, and dependencies on existing policy.
   Identify which facts need durable representation and which choices can remain Manager policy or local interpretation.
   For public ancestry, explicitly assess the recorded #224 boundary and the extra disclosure that a signer observed multiple branches.
   Prefer a candidate on the evidence, state its weaknesses and reopening conditions, and name the next discriminating experiment if the comparison is inconclusive.
   Revise the contract only to the extent justified by that review; multi-parent links, separate resolution records, counters and automatic resumption remain candidates until then.

The comparison rules out deriving resolution from retained private evidence alone and confirms the counter as an ordering baseline only.
It does not settle the representation between multi-parent links and a separate resolution record.
The named discriminating experiment — partial and interrupted delivery of a selection and its resolution record, with the selection naming the record's identity but not what it retires — was then run, and is recorded in [notes.md](notes.md#move-10-2026-09-07-the-discriminating-experiment).
It removes the separate record's one clear disadvantage at the cost of one classification rule, that a routeless node is never a tip, and it does not rescue keeping resolution evidence private.
That is evidence for the checkpoint; the review findings are addressed, and the representation is still open until a human accepts the checkpoint.

### Immediate checkpoint: when retirement takes effect

The record-first correction excludes a routeless record from route candidates, but receiving it still retires a branch and can clear a teammate's pause before its referencing selection arrives.
That is not inert behavior; the reproduced trace and the distinction from the addressed review findings are in [notes.md](notes.md#checkpoint-follow-up-2026-09-07-when-retirement-takes-effect).

The comparison is run in `models/model_retirement_timing.py`; results are in [notes.md](notes.md#move-10-checkpoint-2026-09-07-gating-retirement-on-the-referencing-selection).
On the tested fork, independent and gated retirement differ while a record is held without its referencing selection, including the interval before an eventually delivered selection arrives.
Gating remains the recommendation, subject to the gate correction found in review.
An unrelated defect in the shared classifier's history report was found and recorded rather than fixed.
The reassessment below is written up as evidence; no representation is accepted and the contract is unchanged, so the checkpoint still needs a human decision.

- Compare a record that independently retires its named alternatives with one that supplies ancestry only through a held referencing selection.
  Prefer the latter for the next experiment, not as an accepted rule: partial delivery should not complete retirement independently of the selection whose resolution it explains.
  Done; the preference survived the experiment.
- Preserve the current record-first trace as a control, and exercise both delivery orders into a populated fork and a settled lineage, including permanent withholding of either artifact.
  Assert the exact tips, available route, pause causes and permission to resume before and after each delivery, with all alternatives retained.
  Keep multi-parent selections as the comparison control, and state expected behavior independently of the classifier.
  Done.
- Reassess the separate-record preference on those results and update the candidate contract before expanding into durability modeling.
  Optional disclosure still costs a teammate an indefinite pause; accepting public resolution evidence still requires an explicit decision about the recorded #224 boundary.
  The reassessment is written; the contract update waits on acceptance of the gating rule.

### Review follow-through before durability

1. Preserve the review's two-orphan-record counterexample and correct the activation boundary.
   Done: activation is rooted in a held route selection and is not transitive, so record-to-record links are inert rather than rejected.
   A routeless record must not open another record's gate merely by naming it.
   Both delivery orders, redelivery and independent retirement as the control are exercised; the existing cases are unchanged.
2. Correct the known history-report defect in the same bounded model cleanup.
   Done: fork subjects are route selections, and the record/selection surplus pair is gone from `Actor.inspect()` at all three points under both semantics and both chosen sides.
   Routeless records supply ancestry and remain inspectable evidence, but are not incompatible route choices.
3. Review the corrected evidence and decide the candidate semantics before adding persistence states.
   Recommend gating, explicitly accepting its extra pause when the record arrives before the selection, indefinitely if that selection never arrives.
   Keep the separate-record preference provisional: this experiment favors gating within that representation, but does not establish a new advantage over the multi-parent control.
   Carry the existing #224 disclosure decision separately; no public representation or runtime handoff is accepted by this review.

## Work after that checkpoint

The sequence below remains adjustable in response to the model results.
Durability and a flexible foundation matter more than reaching an implementation handoff quickly.
Keeping options open means preserving evidence, explicit assumptions and narrow semantic boundaries; it does not mean implementing every candidate or adding speculative extensibility machinery.

- Extend the surviving candidate's model with restart, replay, restoration of older state, and interruption between resolution steps.
  State what evidence survives each interruption and what may safely resume; do not assume an atomic transaction or a surviving high-water mark.
  Check unrelated work on both siblings, more than one disputed berth, competing human resolutions, and a fresh deliberate disagreement after a completed resolution.
  An old resolved branch must not regain effect merely through replay; a fresh disagreement may legitimately pause again.
- Recheck the earlier obligations against delayed signing, retries that manufacture selections, promotion of unfinished or refused work, and contradictory payloads under reused identities.
  Any changed obligation needs an explicit argument rather than a relaxed assertion to make a candidate pass.
- Extend the runtime probe once there is a coherent behavioral contract to probe.
  Choose B's existing location while preserving unrelated work from both devices, bind application to the reviewed evidence, and exercise the specified pause boundaries.
  Test a receiving teammate under both NoteToSelf/Core delivery orders, including team publication before disagreement is detected.
  Label stand-ins and distinguish model guarantees from properties the runtime actually demonstrates.
- Reassess the current channel-wide pause and public evidence boundary against concrete workflows and model results.
  Keep indefinite human pauses available; continued automatic progress is not a universal goal.
  Evaluate versioned extension boundaries before any public schema is accepted, without adding compatibility layers for research artifacts.
  Prepare a handoff only after the behavioral choices, evidence limits and remaining research questions have been reviewed.

Restore was not probed and is the one item from the previous round left undone; the Move 5 restore rules stay model obligations.
This branch changes no runtime code, and #238 stays open until its correctness requirements are implemented and validated.

## Design frame

One participant's devices share a berth allocation while teammates learn routes through signed announcements that can arrive late or out of order.
A device acts from provisional local evidence and must support retrospective contradiction checking; persistent disagreement and never-ending sync loops are permitted.
Distinguish incomplete evidence from an observed conflict, and state the evidence needed for detection and the effects later discovery cannot undo.
Local authorization checks remain enforced boundaries.

Existing code supplies counterexamples and component behavior, not a reason to preserve its design, schemas, APIs or micro tests.
There is no production compatibility requirement; keep schema/version markers without adding speculative recovery machinery.
Prefer and defend a candidate on its merits rather than treating all candidates as equally supported.
Manager owns management decisions, Hub performs provider I/O, and generic sync must not acquire allocation policy.
Core and arbitrary app berths need the same account of selection, attestation and repair.

The recorded [#224 decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) establishes shared ownership, equal trusted siblings and NoteToSelf coordination, rejects a peer-verifiable selection DAG, and defers allocation-generation binding in announcements.
Public succession, allocation identity and peer verification of private history are separate questions; substantive changes to that decision must be argued explicitly.

## Decision ledger

Each decision retains its reason and the evidence that would reopen it.
The complete protocol and implementation handoff remain open.
The 2026-09-07 discussion reopens automatic routing, fallback and union-adoption requirements for comparison with pausing for human resolution.
Their model results remain evidence about those candidates, not proof that the machinery is necessary.

- **Participant-owned shared allocation.**
  One participant's devices share one allocation and one route per berth, there is no owner device, and any currently trusted team device may rotate or repair that route.
  Reason: [#224's decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384).
  Reopened by: a demonstrated requirement that only per-device allocations can satisfy.
- **No guaranteed convergence.**
  Devices and users may disagree indefinitely, and never-ending sync loops are permitted.
  Reason: the 2026-09-06 return discussion; at human scale, making disagreement impossible is not a useful requirement.
  Reopened by: nothing expected.
  This bounds what the design must promise rather than what it must do.
- **A visible pause can be an intended outcome.**
  On detecting incompatible choices, a device may preserve the alternatives and pause the affected operation until a human decides, including indefinitely.
  Reason: the 2026-09-07 discussion; blocking a person's own device does not by itself justify an automatic correction algorithm.
  The decision must be meaningful and have a defined effect, but neither automatic adoption nor continued retry is required before the human acts.
  Move 9 establishes what the current runtime charges for that pause: the disagreement is not reported, the whole NoteToSelf channel stops in both directions rather than the disputed berth, and the demonstrated adoption requires a raw deletion the Manager does not offer.
  None of that reopens the entry; it names what an accepted pause must add.
  Reopened by: a concrete requirement for automatic progress or harm from waiting that this pause would not contain.
- **Provisional local decisions with retrospective checking.**
  A device treats its view as at best probably true, decides what evidence is good enough to proceed, and supports later detection of contradiction.
  Reason: the 2026-09-06 discussion; requiring a durable event that grants authority presupposes globally settled state.
  Reopened by: a schedule where retrospective detection is impossible and the resulting damage is unacceptable.
- **Signing or delivery must not advance route succession.**
  Succession must survive delayed attestation and delivery independently of when those actions occur.
  Reason: the move 1 witness in `98c28ae` records B adopting X and deliberately replacing it with Y, yet A's delayed signing makes the selector prefer X.
  This rejects priority assigned at signing time, including a signing timestamp; it does not reject every timestamp use or choose a succession representation.
  Reopened by: evidence that the witness does not establish observed replacement, or an explicit change to the requirement that observed replacement survive delayed signing.
- **Attestation identity is minted per signing act, not reserved per selection.**
  A device repairing interrupted work signs its own attestation identity; identities carry no selection, succession or ordering meaning.
  Reason: both candidates can satisfy the modeled repair and contradiction properties; separate identities represent distinct signing acts directly and avoid sharing an identity during ordinary sibling repair.
  With fresh IDs, identity-keyed retention preserves both claims; a reserved identity requires retaining multiple payloads under one identity.
  This is not a general retention guarantee: the preserved reused-ID control loses one contradictory claim under either delivery order, with the survivor decided by arrival order.
  How conflicting payloads under a reused identity remain detectable is still an obligation.
  Reopened by: a requirement that two siblings' attestations be addressable under one identity, or an argument that the eventual retention design removes the conceptual advantage of representing signing acts separately.
  Both candidates still need payload comparison to tell equivalent duplicates from contradictions, so that is not a reason for the choice.
- **Public succession ordering; automatic priority reopened.**
  In the counter-based candidate, a public scalar succession counter decides provisional routing priority, and a fresh selection that exceeds every counter its author has observed survives delayed delivery and repeated attestation of the branch it was chosen over.
  Detecting that two selections are competing branches is a separate capability that no counter comparison supplies; it requires retained selection history naming predecessors, including the superseded intermediate selections.
  Reason: the Move 3 model, where X₂ at counter 3 outranks Y₁ at counter 2 while no evidence connects them, and where a fork is found only after the intermediate X₁ is retained or adopted.
  Reopened by: a schedule where counter ordering alone must establish observed replacement, or a detection requirement that teammates must meet without receiving predecessor evidence.
  This decides what the counter is for; it does not decide what the public attestation carries.
  The human-resolution walkthrough now reopens whether automatic priority is needed at all; a pause must still prevent delayed evidence from silently reversing an observed replacement.
  A public report may therefore locate a branch and say which side lost routing priority, and may never call that a resolution: the post-Move 5 review's control produces the same classification from a deliberate human resolution and from an author who never observed the branch it outranks.
- **An unknown publication outcome is incomplete evidence, not an observed conflict.**
  After an unacknowledged publication a device keeps using and attesting to its own selection and reports the publication as unknown.
  A definite refusal and an already-superseded result are observed conflicts: the Move 5 candidate falls back to the greatest selection the device still holds that it has not itself seen refused, or to the one the refusal names, and surfaces the conflict.
  Publication outcome bounds what a device may claim about publication, not what it routes to; a device that later adopts a greater selection routes there and still cannot say whether its own was published.
  Automatic fallback after an observed conflict is reopened in favor of evaluating an explicit pause; the distinction between unknown and refused remains necessary.
  Reason: the Move 5 model, where the device's standing and its retry are identical whether or not the lost write landed.
  Reopened by: a schedule where proceeding after an unknown outcome causes damage that retrospective detection cannot make visible, or a runtime whose refusals are themselves unreliable enough that refused and unknown cannot be distinguished.
  Three corrections from the post-Move 5 review are part of this entry, each with a preserved control.
  An outcome describes one attempt: a refusal establishes that this attempt wrote nothing and is no evidence that an earlier attempt on the same record did not land, so what a device may claim about a record never weakens as attempts accumulate.
  Refusals are durable for the device that observed them: a selection it has seen refused is not a fallback candidate later, because private history records what was chosen rather than what the store accepted.
  A refusal that names a greater selection is evidence to be adopted rather than quoted, since attesting to a selection means holding it and the next deliberate selection must outrank what the device has now been told about.
  Move 8 narrows the unknown window when a successful settlement reread finds the attempted head and proves the write's condition spent.
  A failed reread can leave even a landed write unresolved; a changed etag alone does not identify whose write landed.
  Cod Sync parks the divergent observed head and names the merge base, supplying inspectable Git evidence rather than a finding that another route supersedes this one.
  What no runtime path supplies is durability: `push_note_to_self` discards every disposition and writes no marker, so the publication claim exists only for the duration of the call.
- **A retry republishes a record; it never makes a selection.**
  Retrying an uncertain publication resends the identical selection ID, counter, predecessor and route.
  Reason: the Move 5 model, where a retry expressed as a fresh selection takes a counter above everything the device has observed and so resurrects a route a sibling deliberately replaced — the Move 1 failure without any delayed signing.
  A retry that has not yet seen the replacement instead lands on its counter and manufactures a tie nobody chose.
  Deliberate human reselection produces a mechanically identical record, so this must be enforced at the retry call site rather than detected afterwards.
  Reopened by: a retry schedule that cannot be expressed by replaying the original record.
- **Union merge and restore rules; automatic adoption reopened.**
  In the Move 5 model, published records merge by union and counters determine provisional routing or a tie in any merge order; restoring an old snapshot of published records revives nothing.
  The two rules that do not follow from ordering are that the private-to-published boundary is crossed only by an explicit publication, and that a device's greatest observed counter is a monotone high-water mark that a restore adds to rather than replaces.
  Reason: the Move 5 model, where publishing a device's private history promotes a definitely refused selection, and where a restore that replaces state leaves a human's deliberate choice below the selection it was chosen over and unpublishable.
  Reopened by: a durable representation in which the high-water mark can be lost rather than replaced, or a merge that is not union.
  Move 8 shows union is not available in current storage: `idx_berth_cloud_allocation_berth` makes one allocation per berth a schema constraint, replacement inserts rather than updates, and merging two siblings' rotations is refused outright.
  Equal-counter selections can remain conflicted even with ordering, so a counter alone would not choose between them.
  The human-resolution candidate may preserve the alternatives in parked histories and defer live integration until a human decides; union adoption is no longer a prerequisite.
- **A selection record freezes complete route content; signing reads only the record.**
  Protocol, endpoint, the finalized locator and every account value a route depends on are fixed in the selection record, and attesting to a selection reads nothing else.
  Reason: the Move 6 model, where resolving account state at signing time makes a sibling's repair contradict the selection it was repairing, with two authentic signatures, neither device at fault and no route left for the teammate; and where union-merging a record set with a peer's account row derives a route nobody signed.
  A content digest satisfies the same correctness property and was rejected on cost, not on correctness: the record alone cannot then support repair, so a second delivery path is needed for content that freezing already delivers.
  Reopened by: a requirement that route content not be published to siblings in the clear, which is what would make the digest's separation of naming from content worth its delivery path, or a schedule where a selection must legitimately mean something different after account state changes.
  This says which values are bound, not which row holds them.
  Move 8 splits the answer by consumer.
  What a teammate consumes is `TransportEndpoint(protocol, url, location)`, and the signed announcement freezes exactly those.
  That does not establish that selection content is frozen before signing; this requirement remains unimplemented.
  What a repairing sibling consumes is read at repair time from a JOIN onto the account row, and includes `client_id` and `path_metadata`, which no announcement carries, plus device-local credentials that are published nowhere.
  The reference-binding counterexample's precondition is therefore present in current code on the repair path, with no shipped mutation to trigger it.
- **A selection is not minted until every field it freezes is final.**
  Provider finalization precedes selection, so a record never names a locator the provider has not settled on.
  Reason: the Move 6 model, where a record minted before finalization fixes the requested locator and the attested route names storage that does not exist, under both freezing and digesting.
  Late binding is the only representation that tracks the correction, and only because it will change what a selection means later, which the account schedule rules out; so this is an ordering rule that no representation replaces.
  Reopened by: a provider interaction that cannot settle a locator before a selection must exist, or evidence that finalization is not observable as a completed step.
  Move 7 supports call-site ordering on the probed S3 path, but the retained allocation and local route report contain no completion marker.
  Provider state does survive; a sibling cannot establish completion from the shared allocation alone and the observed repair repeats materialization.
  This is evidence about the current path, not a decision to remove the candidate contract's marker or a proof about concurrent finalization.
  Two probe findings bear on it and are unresolved: `ensure_bucket_public` has an interior state where storage exists and no teammate can read it, which no model represents, and no shipped adapter returns a provider-issued locator, so the case that motivates the rule is unimplemented.
- **Devices retain superseded selections.**
  A device keeps its own private selection record per berth without pruning at this stage, because fork detection rests entirely on holding the intermediate selections.
  Reason: the Move 3 control, where a device holding P, Y₁ and X₂ but not X₁ detects nothing.
  Reopened by: a workload where per-berth selection history grows without a human-scale bound.

## Open questions

- **Public predecessor links.**
  Advisory links were preferred for teammate diagnostics under the counter-based candidate; revisit their need after the human-resolution walkthrough.
  Decide whether diagnostics justify metadata exposure and revising #224, including the issue owner's agreement.
  Exposure beyond route content, including traffic analysis and correlation across berths or teams, is unpriced.
  A located branch and lost routing priority are never evidence that anyone resolved the disagreement.
- **Durable publication and adoption.**
  Establish what evidence detection and a meaningful human decision need, including conflicting payloads under reused identities and publication observations.
  Move 8's competing allocations block the exercised integration and repair operations; Move 9 recovers the incoming alternative and predecessor from Git blobs, so decide what a conflict report names and how a choice survives replay of the resolved evidence.
  A new deliberate disagreement may pause again; that does not establish a failure of resolution durability.
  Decide how a person selects either preserved allocation, whether withdrawal belongs in the interface, and whether the pause should be scoped to the disputed berth.
  Decide whether the counter-based candidate's whole-record adoption and greatest observed counter are needed, then specify durable storage for the evidence the chosen design actually uses.
  Decide how a selection reaches a sibling at all, since allocations travel over NoteToSelf while announcements travel over the team Core chain with nothing ordering the two.
  Restore is still unprobed, so its rules remain model obligations.
  What a disconnected sibling may prepare before publication is still open.
- **Complete route content and provider completion.**
  Identify the actual account fields a route must freeze and the consistency needed to capture them.
  Move 8 gives the current answer — three frozen fields for a teammate, a read-time join plus device-local credentials for a repairing sibling — and leaves open whether repair should read a frozen record instead, and what a sibling does when the credentials a repair needs were never published.
  Decide whether to represent the interior state of multi-call materialization and what completion evidence a repairing sibling needs.
  No shipped adapter returns `materialized_with_locator`; the provider-issued-locator case remains modeled and stub-tested, not demonstrated by Move 7.
  Decide its present design role without treating a hypothetical provider as a requirement for new machinery.
- **Repair and integration boundaries.**
  Keep replaying signed bytes, issuing another attestation and selecting a successor distinct.
  Explain required communication, unavailable or untrusted signers, local/offline creation and linked-device bootstrap.
  Justify invitation-specific signer requirements separately from established-team repair.
  Reports must distinguish local decisions, provider effects, publication, adoption and teammate delivery; a valid announcement does not establish reachable storage.

## Validation and schedule catalog

Use two installations with distinct trusted device keys, explicit pause points and local mocks or MinIO.
Do not replace the decision checks, publication, integration or selector under examination with success stubs.
For each schedule record actor evidence, permissible effects, detection requirements, responses after discovery and conditions for progress.
An explicit, evidence-preserving pause may satisfy a schedule; distinguish that outcome from misleading readiness or silent promotion of a choice.
Move 9 examined the pause, demonstrated adoption of A with A's unrelated change, established the lack of a Manager operation to choose B's existing location, and reproduced a fresh conflict after resolution.
It did not demonstrate choosing B, preservation of unrelated changes from both sides through resolution, or resistance to replay of the resolved disagreement.
Validate safety separately from conditional progress; finite models and passing probes establish only their stated bounds.
The research artifacts assert observed behavior, including defects, rather than fixing it.

| Question or schedule | What it should establish |
| --- | --- |
| Sibling rotations refuse integration | The disagreement and paused operations are visible; alternatives survive until a human chooses, with no deadline or requirement to merge first. |
| A selects X, B selects successor Y, A resumes | Delayed signing and delivery cannot promote X above Y, regardless of clocks. |
| Competing candidates and uncertain publication | Which actions are locally justified, what uncertainty remains, and how later evidence exposes incompatible claims or a silently promoted refused candidate. |
| Competing successors, then another advance on one branch | Unequal succession counters do not establish agreement; identify the evidence and observer needed to detect competing history. |
| Publication finds a descendant | Historical inclusion is distinguished from current selection; consider both related and unrelated changes. |
| Interrupted work and sibling repair | The durable evidence needed for repair, without unnecessary reallocation or coordinated reuse of signed identity. |
| Equivalent and contradictory attestations | Same-selection duplicates have defined behavior; contradictory claims are not hidden by an arbitrary tie-break. |
| Account-only change or differing provider locators | Selections and attestations bind complete route content, including values that might otherwise be read from separately changing state. |
| Human chooses a previously refused candidate | Resolution establishes appropriate succession instead of reusing a competing candidate's obsolete priority. |
| X, then Y, then an intentional return to X | The final choice outranks delayed Y even when an older matching X announcement already exists. |
| Merge, restore or generic publication | These operations cannot silently turn old or refused state into a new selection. |
| Signer unavailable or no longer trusted | Explain sibling repair and peer trust behavior without claiming reachability from signature validity. |
| Local work succeeds but delivery fails | State and retry semantics remain honest at each boundary. |
| Repeated sync or contested human resolution | Disagreement may persist visibly; distinguish renewed choices from retry-generated selections and state the conditions under which progress is expected. |

The eventual integration audit includes creator setup, established-team changes, invitation preparation/export/import, ready-route shortcuts, generic NoteToSelf and Core publication, and the public management interface.
Existing integration, divergent-publication, signature/import and Hub routing micro tests may supply useful harnesses when their assumptions still apply.
At the implementation integration checkpoint, run affected micro tests and the repository suite with `uv run pytest`, recording commands and blocked checks.
Check architectural responsibility and consistency across berths as well as observable outcomes.

## Scope and housekeeping

Full app provisioning UX, the Files capstone, provider migration and old-location cleanup remain outside this branch.
A possible later split is one established-team Core implementation followed by broader integration; its concrete scope must follow the accepted design.
A human handles the PR and branch-folder cleanup.
The two independent Hub spec corrections in `2ebe983` are already incorporated here; [doc-fixes.md](doc-fixes.md) records the verified completion.
