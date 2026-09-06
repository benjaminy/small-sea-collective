# Open discussion: shared berth allocation and routes

Issue: [#238](https://github.com/benjaminy/small-sea-collective/issues/238).
Branch: `issue-238-shared-berth-changes`.
Status: open research discussion; no protocol or implementation plan accepted.
This file is the discussion agenda until there is a design worth handing to an implementer.
A separately reviewed correction of two false Hub spec claims landed on `main` in `2ebe983`, verified on return on 2026-09-06; see [doc-fixes.md](doc-fixes.md).
This branch has not incorporated that commit and still contains the old passages.
Their completion changes neither the open design status nor #238's implementation requirements.

## Decided and open

This ledger exists so that a round can close something.
Add an entry when a question is settled, stating the reason and what would reopen it.
An entry without a falsifier is a preference, not a decision.

Decided.

- **Participant-owned shared allocation.**
  One participant's devices share one allocation and one route per berth, there is no owner device, and any currently trusted team device may rotate or repair that route.
  Reason: [#224's decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384).
  Reopened by: a demonstrated requirement that only per-device allocations can satisfy.
- **No guaranteed convergence.**
  Devices and users may disagree indefinitely, and never-ending sync loops are permitted.
  Reason: the 2026-09-06 return discussion; at human scale, making disagreement impossible is not a useful requirement.
  Reopened by: nothing expected.
  This bounds what the design must promise rather than what it must do.
- **Provisional local decisions with retrospective checking.**
  A device treats its view as at best probably true, decides what evidence is good enough to proceed, and supports later detection of contradiction.
  Reason: the same discussion; requiring a durable event that grants authority presupposes globally settled state.
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
  This is not a general retention guarantee: an ad hoc control with a reused attestation ID loses one contradictory claim under either delivery order, with the survivor decided by arrival order.
  How conflicting payloads under a reused identity remain detectable is still an obligation.
  Reopened by: a requirement that two siblings' attestations be addressable under one identity, or an argument that the eventual retention design removes the conceptual advantage of representing signing acts separately.
  Both candidates still need payload comparison to tell equivalent duplicates from contradictions, so that is not a reason for the choice.
- **Succession ordering is public; conflict detection is not derived from it.**
  A public scalar succession counter decides provisional routing priority, and a fresh selection that exceeds every counter its author has observed survives delayed delivery and repeated attestation of the branch it was chosen over.
  Detecting that two selections are competing branches is a separate capability that no counter comparison supplies; it requires retained selection history naming predecessors, including the superseded intermediate selections.
  Reason: the Move 3 model, where X₂ at counter 3 outranks Y₁ at counter 2 while no evidence connects them, and where a fork is found only after the intermediate X₁ is retained or adopted.
  Reopened by: a schedule where counter ordering alone must establish observed replacement, or a detection requirement that teammates must meet without receiving predecessor evidence.
  This decides what the counter is for; it does not decide what the public attestation carries.
  A public report may therefore locate a branch and say which side lost routing priority, and may never call that a resolution: the post-Move 5 review's control produces the same classification from a deliberate human resolution and from an author who never observed the branch it outranks.
- **An unknown publication outcome is incomplete evidence, not an observed conflict.**
  After an unacknowledged publication a device keeps using and attesting to its own selection and reports the publication as unknown.
  A definite refusal and an already-superseded result are observed conflicts: the device falls back to the greatest selection it still holds that it has not itself seen refused, or to the one the refusal names, and surfaces the conflict.
  Publication outcome bounds what a device may claim about publication, not what it routes to; a device that later adopts a greater selection routes there and still cannot say whether its own was published.
  Reason: the Move 5 model, where the device's standing and its retry are identical whether or not the lost write landed.
  Reopened by: a schedule where proceeding after an unknown outcome causes damage that retrospective detection cannot make visible, or a runtime whose refusals are themselves unreliable enough that refused and unknown cannot be distinguished.
  Three corrections from the post-Move 5 review are part of this entry, each with a preserved control.
  An outcome describes one attempt: a refusal establishes that this attempt wrote nothing and is no evidence that an earlier attempt on the same record did not land, so what a device may claim about a record never weakens as attempts accumulate.
  Refusals are durable for the device that observed them: a selection it has seen refused is not a fallback candidate later, because private history records what was chosen rather than what the store accepted.
  A refusal that names a greater selection is evidence to be adopted rather than quoted, since attesting to a selection means holding it and the next deliberate selection must outrank what the device has now been told about.
- **A retry republishes a record; it never makes a selection.**
  Retrying an uncertain publication resends the identical selection ID, counter, predecessor and route.
  Reason: the Move 5 model, where a retry expressed as a fresh selection takes a counter above everything the device has observed and so resurrects a route a sibling deliberately replaced — the Move 1 failure without any delayed signing.
  A retry that has not yet seen the replacement instead lands on its counter and manufactures a tie nobody chose.
  Deliberate human reselection produces a mechanically identical record, so this must be enforced at the retry call site rather than detected afterwards.
  Reopened by: a retry schedule that cannot be expressed by replaying the original record.
- **Merge and restore need one rule beyond ordering, and it constrains devices.**
  Merging published records is union, and counters decide it in any merge order; restoring an old snapshot of published records revives nothing.
  The two rules that do not follow from ordering are that the private-to-published boundary is crossed only by an explicit publication, and that a device's greatest observed counter is a monotone high-water mark that a restore adds to rather than replaces.
  Reason: the Move 5 model, where publishing a device's private history promotes a definitely refused selection, and where a restore that replaces state leaves a human's deliberate choice below the selection it was chosen over and unpublishable.
  Reopened by: a durable representation in which the high-water mark can be lost rather than replaced, or a merge that is not union.
- **A selection record freezes complete route content; signing reads only the record.**
  Protocol, endpoint, the finalized locator and every account value a route depends on are fixed in the selection record, and attesting to a selection reads nothing else.
  Reason: the Move 6 model, where resolving account state at signing time makes a sibling's repair contradict the selection it was repairing, with two authentic signatures, neither device at fault and no route left for the teammate; and where union-merging a record set with a peer's account row derives a route nobody signed.
  A content digest satisfies the same correctness property and was rejected on cost, not on correctness: the record alone cannot then support repair, so a second delivery path is needed for content that freezing already delivers.
  Reopened by: a requirement that route content not be published to siblings in the clear, which is what would make the digest's separation of naming from content worth its delivery path, or a schedule where a selection must legitimately mean something different after account state changes.
  This says which values are bound, not which row holds them.
- **A selection is not minted until every field it freezes is final.**
  Provider finalization precedes selection, so a record never names a locator the provider has not settled on.
  Reason: the Move 6 model, where a record minted before finalization fixes the requested locator and the attested route names storage that does not exist, under both freezing and digesting.
  Late binding is the only representation that tracks the correction, and only because it will change what a selection means later, which the account schedule rules out; so this is an ordering rule that no representation replaces.
  Reopened by: a provider interaction that cannot settle a locator before a selection must exist, or evidence that finalization is not observable as a completed step.
- **Devices retain superseded selections.**
  A device keeps its own private selection record per berth without pruning at this stage, because fork detection rests entirely on holding the intermediate selections.
  Reason: the Move 3 control, where a device holding P, Y₁ and X₂ but not X₁ detects nothing.
  Reopened by: a workload where per-berth selection history grows without a human-scale bound.

Open.

**Advisory public predecessor links remain preferred, but the choice is still open.**
The Move 4 correction supplied the missing comparison: honest counter-only provisional routing, which claims no supersession and leaves retrospective checking to siblings, routes identically to the verifying policy in every world.
The link therefore buys diagnosis, not routing correctness.
The Move 4 correction closed the gaps that review named — rivals are classified rather than flagged, and contradictory attestations for one selection are no longer collapsed — but the claim that the reporting gaps were closed was premature.
The post-Move 5 review found two more in the same report and both are now corrected with preserved controls: a tie returned no route and dropped the evidence for it, which also made `open_disagreement` unreachable, and a located branch was reported as a matter of record when nothing public establishes that anyone resolved it.
What remains is still the argument rather than the model, with the standing caution that a reporting surface has now been found wrong twice by review rather than by its own cases.
Accepting the payload still requires defending teammate diagnostics as worth the change, the issue owner's agreement to revise #224, and an answer on metadata exposure, which remains unpriced.

The remaining design questions are enumerated once under "Questions for the next rounds".
Question 1's attestation half is closed by the attestation-identity ledger entry; its succession half is closed for ordering and open for representation.
Question 4's ordering result stands; its public representation and post-resolution reporting remain open.
Question 2's publication half is closed by the three Move 5 entries; what a disconnected sibling may prepare before publishing is untouched.
Question 6's merge and restore half is closed by the same entries; its provider-finalization and integration boundaries are not.
Question 3's binding half is closed by the two Move 6 entries; what remains of it, and of question 6, is the same thing: the provider and publication boundaries the models have been asserting rather than checking.
Nothing is now blocked on further modeling.

## How to approach the discussion

The existing implementation gets no weight in choosing the design.
Existing schemas, wire formats, selectors, helper boundaries and micro tests are neither requirements nor reasons to prefer a candidate.
There is no deployed product, installed user base or production data requiring compatibility.
Judge proposals by their semantics, correctness, conceptual simplicity and fit with the project's purposes, not by fields added or code preserved.
Keep schema/version markers so later compatibility work remains possible, without building compatibility machinery now.

Giving the existing implementation no weight does not mean giving every candidate equal standing.
The `discussion` commit generalized the first rule into the second, which is a different rule and was not argued for.
That generalization is withdrawn here.
A round should name the candidate it prefers and carry the burden of defending it.
Recommending one is how the option space narrows; withholding a recommendation leaves every question open by construction.

Code can expose a counterexample or tell us what a component actually does.
That evidence can falsify an assumption about using the component; it cannot require us to retain the component or its current behavior.
Separate deliberate architectural choices from accidental implementation choices, and examine the reasons for the former when they matter.
Record arguments and disconfirming evidence in [notes.md](notes.md).
No candidate becomes accepted because it was investigated first or described in the most detail.

## Problem to understand

A participant's devices share a berth allocation, while teammates learn routes through signed announcements that may arrive late or in a different order.
Given a device's evidence, when is proceeding justified, what does it claim by proceeding, and how can later evidence challenge that claim?
How can a sibling finish interrupted work without changing that selection or reviving a superseded route?

The 2026-09-06 return discussion establishes the planning frame: Small Sea is radically decentralized, and a device's view is provisional rather than globally authoritative.
The design must define what evidence is good enough to act on and what retrospective checks can discover when more evidence arrives.
Shared participant ownership does not imply that all siblings currently agree on a selection.
If a candidate uses "settlement" or "authority," it must specify whose judgment, which evidence and what scope those words describe.
This does not weaken enforced local authorization boundaries such as the Hub's client checks.

Guaranteed convergence and termination of every sync loop are not requirements.
Devices or users may keep disagreeing or reintroducing conflicting state indefinitely.
Human scale makes inspection and intervention practical; it does not prove that disagreement will end.
Engineering should reduce avoidable churn and support progress under stated favorable conditions without requiring an impossibility proof for endless loops.

The desired properties motivating this discussion are:

- Distinguish acting with incomplete evidence from disregarding an observed conflict.
  Define which provisional actions are acceptable and what must become visible when sufficient contradictory evidence arrives.
- Delayed signing or delivery must not turn an earlier selection into a successor.
- A currently trusted sibling must be able to finish interrupted work from durable shared evidence without unnecessarily allocating storage again.
- Provider finalization and account metadata changes must not substitute different route content under an earlier selection or attestation.
- Conflicts, uncertain outcomes and human choices must remain explicit.
- Reports must distinguish local decisions, provider effects, publication and adoption of shared evidence, and delivery to teammates.
  Retaining an old valid announcement does not establish that its storage remains reachable.
- Retrospective checks must state the evidence and communication needed for detection and the limits of subsequent repair.
  Detection can be delayed indefinitely and cannot undo every intervening effect; visible unresolved disagreement is an acceptable outcome.

[#224's decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) supplies the current deliberate starting point: participant-owned allocations, equal trusted siblings and coordination through NoteToSelf.
It rejects a peer-verifiable selection DAG and defers allocation-generation binding in announcements.
Examine those choices by their reasons; identify and discuss a substantive conflict rather than silently treating an implementation convention as one of those decisions.
A public succession value, allocation-generation identity and peer verification of private history are separate questions.

The repository's architectural responsibilities remain applicable: Manager owns management policy, Hub mediates provider I/O, and generic sync should not acquire allocation policy.
Core and arbitrary app berths should admit the same explanation of selection, attestation and reconciliation.

## Questions for the next rounds

1. What are the distinct concepts?
   Distinguish storage allocation identity, selected route content, succession, signed attestation identity and delivery.
   Which changes create a new allocation, which create a successor route, and which merely attest to or deliver an existing selection?
   Does a timestamp serve an actual human or protocol need?
2. What evidence is good enough to proceed?
   What may a disconnected sibling prepare, announce or use, and which observations require it to stop or surface a conflict?
   Which durable evidence can a sibling adopt, and what does successful publication establish locally?
   If finalized-state publication before signing is proposed, explain its guarantees without assuming global agreement.
   How should an unknown publication outcome differ from a definite refusal or an already superseded state?
3. What binds a selection and its attestations to exact route content?
   Consider protocol, endpoint, final locator and any relevant account state.
   Compare a frozen projection with other explicit bindings; do not assume current table boundaries.
   State the atomicity and merge properties each representation requires.
4. How does succession survive concurrency and human resolution?
   Explain delayed actions, competing candidates, restoration and returning to a previously used location.
   Where does the necessary predecessor evidence live, and how is it preserved when a selection changes?
   What should peers do with equivalent attestations or contradictory routes claiming equal succession?
5. What does repair mean?
   Distinguish replaying stored signed bytes, issuing another attestation and making a new selection.
   What must a sibling observe first, which steps require communication, and what does repeating the operation guarantee?
   Evaluate invitation-specific signer requirements separately from established-team repair.
6. Where are the decision and retrospective checking boundaries?
   Explain creation, ordinary changes, provider finalization, integration, generic publication and courier delivery.
   Could merging or restoring state silently promote a refused candidate or revive obsolete ordering?
   What evidence is retained, who can inspect it, and what happens when a contradiction is discovered?
   Do not let today's entry-point organization define the protocol.

## Candidates to examine

The recommended starting hypothesis separates independently minted announcement identities from the selection and succession information attested to with exact route content.
Its attraction is that siblings could attest to the same selection without creating a new selection, sharing a primary key or reserving a signer.
It still needs conditions for proceeding, successor rules, content binding and retrospective treatment of contradictory attestations.
Neither the candidate nor a particular succession representation has been accepted.

Earlier discussion proposed reserving the announcement identity itself, with either an intended signer or a composite key.
Those candidates remain available for substantive arguments, but preserving the existing format gives them no advantage.
The need to coordinate signer or timestamp bytes may be a cost caused by that representation rather than a requirement of the problem.

These are examples, not an exhaustive menu or a decision to use reservations, scalar ordering, UUIDs or a particular row layout.
A stronger alternative should be expressible in the same terms: local evidence, claims, transitions, checks, assumptions and counterexamples.

## Next moves

Move 1 reproduced the delayed-signing reversal in `98c28ae` and earned the signing-time ledger entry above.
The recipient-validation control in `e7e811c` closes Move 1's remaining evidence gap.
Move 2's contract is in [contract-sibling-repair.md](contract-sibling-repair.md) and its model is in [models/model_sibling_repair.py](models/model_sibling_repair.py); together they earned the attestation-identity ledger entry above.
Move 3 challenged succession with competing branches and earned the succession-ordering ledger entry above; its model is in [models/model_succession_branches.py](models/model_succession_branches.py).
Move 4 supports the retention decision and an advisory-link preference; its model is in [models/model_public_payload.py](models/model_public_payload.py).
Its correction added the honest counter-only comparison and preserved the two post-review controls.
Move 5 answered uncertain publication, retry, merge and restore, and earned three ledger entries; its model is in [models/model_publication_outcomes.py](models/model_publication_outcomes.py).
Its correction narrowed the publication entry and the reporting surface after review found five counterexamples, all now preserved as controls.
Move 6 settled route-content binding and provider finalization, and earned two ledger entries; its model is in [models/model_route_binding.py](models/model_route_binding.py).
That was the last modeled question, so the next move is the probe: check the provider and publication boundaries the models assert, against local services.
Runtime implementation remains later work.

### Move 1: complete, including the recipient-side control

The preserved [probe](probes/probe_delayed_signing.py) establishes B's field-for-field adoption of finalized X, deliberate replacement with Y and A's delayed signing of X.
The real selector returns X in both insertion orders under ordinary clock conditions.
Commands, results and limitations are recorded under "Move 1 result" and "Move 1 control" in [notes.md](notes.md).

The added control establishes that Y alone selects Y in the recipient, then adding X reverses the selection.
Both insertion-order assertions and the ID comparison remain.
This rules out Y's failure to validate as the explanation for X winning; it strengthens the evidence without changing runtime behavior or choosing a design.

The staged retryable failure is a legitimate interruption before signing.
Authentic rows are inserted directly into recipient database copies; full fetch/merge delivery remains later integration validation and is not needed to discriminate the next candidates.
The probe fixes nothing.
Disposition as a permanent regression micro test follows the chosen design.

### Move 2: complete, including the contradiction schedules

Prefer distinct attestation identities carrying stable selection/succession information and exact route content.
The delayed-signing witness rejects signing-time priority but does not choose this candidate over an identity reserved at selection time.
Sibling repair supplies the next comparison, but need not distinguish the candidates on correctness.
Compare independently minted attestation IDs with a selection-time reserved ID that permits multiple signers and retains conflicting signed payloads.
Do not assume a shared identity forces overwriting or a fixed signer.
Both candidates need payload comparison; evaluate coordination and recipient rules independently of the current schema.

The candidate contract is drafted in [contract-sibling-repair.md](contract-sibling-repair.md).
It states the durable evidence identifying a selection and binding its exact finalized route, the conditions under which another currently trusted sibling may attest,
what a recipient may conclude from one attestation and from several, and the coordination each candidate requires.
Its durability assumptions are recorded as obligations for later runtime validation, and drafting it accepted neither candidate.
Review it with the model; revise it if the model disconfirms part of it.

Then use a small executable model with two cases:

- Durable evidence describes one selection and its exact finalized route.
  A becomes unavailable before signing; B adopts the evidence and issues its own attestation; A later resumes and issues another.
  Neither attestation creates a successor or requires another allocation.
- B's attestation changes one route field while retaining the same claimed selection/succession.
  Once both attestations arrive, the contradiction remains visible rather than disappearing behind an identity tie-break.
  Include a variant that changes the predecessor under the same selection ID; that is also a contradictory claim.

For each case, state the evidence each actor needs, the meaning of its claim, and acceptable recipient behavior before and after both attestations arrive, exercising both delivery orders.
Before contradictory evidence arrives, the recipient can judge only what it has.
Define the model's assumptions about durable evidence explicitly; model success does not prove that NoteToSelf supplies those guarantees.
Keep the contract independent of runtime table and helper boundaries.
Record commands, results and any disconfirming case alongside the model in this branch folder.
Assert that equivalent duplicates are harmless and that repair creates neither a selection nor an allocation.

Result: both candidates satisfy the repair and contradiction properties, and the choice was made on what each requires of recipients rather than on impossibility.
The model runs 26 cases across both schemes, both delivery orders, both recipient retention keys and both contradiction variants; commands, observations and limits are under "Move 2 model result" in [notes.md](notes.md).
Repair created no successor and no allocation under either scheme, and an unadopted device could not attest.
The model's disconfirming case is the reserved identity under identity-keyed retention.
The subsequent ad hoc reused-ID control narrows the ledger's retention claim; its command, result and limits are recorded in [notes.md](notes.md).
Preserve that control as an explicit model case in the next executable increment; a complete retention design need not precede Move 3.
The model assumes unit adoption of evidence, adoption distinguishable from reachability, and surviving predecessor evidence; those remain runtime obligations.
Its succession handling is minimal by construction and does not address competing successors.

### Move 3: complete, including the preserved reused-ID control

Prefer testing public scalar counters with private retained selection history; this is a candidate, not an accepted representation.
Compare it with counter-only behavior to expose what the private evidence contributes.
Keep teammate attestations and sibling NoteToSelf evidence as distinct model views.
The Move 2 model's public predecessor access is a hypothesis, not permission to give teammates private history in this experiment.

Use one bounded schedule:

1. A and B adopt P, then disconnect.
2. A selects X₁ and B selects Y₁ from P; A advances to X₂ without observing Y₁.
3. A teammate receives X₂ and Y₁ in both orders, initially without X₁.
   Distinguish higher provisional routing priority from evidence that X₂ supersedes B's choice.
4. A sibling later adopts enough private history to inspect the competing branches.
   State exactly which evidence enables detection, what communication delivers it, and what the sibling and teammate may do before and after discovery.
5. After observing both branches, a human deliberately chooses Y's location.
   Test whether a fresh selection establishes appropriate succession while delayed X₂ delivery and repeated attestation cannot reverse that choice.
   Preserve evidence of the earlier disagreement; agreement need not follow.

Unequal counters alone do not establish observed replacement, and equal-counter conflict detection misses this schedule.
The candidate must explain how private conflict discovery affects subsequent local actions and public claims without assuming teammates see private evidence.
If the candidate needs peer-verifiable selection history, argue the substantive change to #224's rejection of a peer-verifiable selection DAG.
Do not infer agreement from numeric order or require detection before the necessary evidence arrives.

Deliver one executable model, its counterexample and supported conclusions, and a succession decision with assumptions and a falsifier or a precise remaining discriminator.
Runtime publication and adoption probes follow once the candidate states the guarantees those mechanisms must supply.
This increment does not authorize runtime implementation.

Result: the counter and the private history do different jobs, and the model separates them.
Under both public representations the teammate routes to X₂ on its higher counter and can conclude nothing about replacement, while the equal-counter check finds nothing at all because this schedule produces 3 against 2.
Fork detection required retaining the superseded intermediate selection: a device holding P, Y₁ and X₂ but not X₁ found nothing, and B found the fork only after adopting A's history.
The human's fresh selection at counter 4 won in every delivery order against delayed X₂ delivery and repeated attestation, under both representations, and B's history still showed the fork afterwards.
The 32 model cases and their limits are under "Move 3 model result" in [notes.md](notes.md); no teammate is given private history anywhere in the model.
The Move 2 model now carries the reused-ID control as an explicit case, as the previous round required.

### Move 4: complete, with its comparison corrected; the decision stays open

Move 3 shows the predecessor selection ID buys a teammate two things: it distinguishes a verified chain from one with a missing link, and it locates a branch point once the intermediate selection is delivered.
Both benefits depend on delivery of superseded selections that no teammate is promised, and the model did not price the exposure of publishing selection history to peers, which is what #224 rejected.
That is the discriminator: whether a teammate should receive predecessor links at all, or whether provisional routing takes the counter alone and every conflict claim goes through a sibling holding private history.
Argue the substantive change to #224's rejection if the answer is that teammates need the links.
Also state how much superseded selection history a device retains, since detection rests on it, and what a teammate does with an unverified higher counter.
Then decide, with assumptions and a falsifier.

Initial result: advisory links add diagnostic information, while requiring a complete chain stalls routing on ordinary delay.
Excluding opaque identifiers and route content, a counter-only teammate cannot distinguish a clean succession from a concealed fork; the two views are the same, so no recipient policy separates them.
With the link, the verifying policy is quiet in the clean world and reports Y₁ as unresolved in the forked one while still routing provisionally.
A recipient that requires a complete chain refuses to route in an uncontested succession whose middle link is late, which is why the link is advisory.
The initial decision, the argument about #224 and the exposure the model does and does not price are under "Move 4 decision" in [notes.md](notes.md).
The subsequent review narrows that decision: counter-only routing need not assume supersession, so the missing comparison is provisional routing with retrospective checks left to siblings.

The correction, now made:

- `PROVISIONAL_ONLY` is the honest counter-only policy — greatest counter routes, no supersession is claimed, retrospective checking is left to siblings.
  It routes identically to the verifying policy in all three worlds, so the link's case rests on teammate diagnostics rather than on routing correctness.
- The report classifies each rival as missing evidence, historical divergence or open disagreement, and only the first and third count as unresolved.
  The preserved post-resolution control routes to `berth-y1` with a complete chain and reports Y₁ as divergence of record; a companion control shows the resolution itself is not public evidence, since a device that never saw Y₁ produces the same claim for Z.
- The contradictory-attestation control runs in both delivery orders.
  `Attestation.claim` excludes signing identity, and a contradicted selection is reported rather than projected away, so delivery order no longer chooses the route.

The result is in "Move 4 correction" in [notes.md](notes.md).
Move 3's ordering result stands; these were composition and reporting gaps, not demonstrated runtime defects.

### Move 5: complete, with its reporting corrected

Question 2 and question 6 are the remaining ones with counterexamples waiting in the evidence table:
competing candidates with an uncertain publication outcome, publication finding a descendant, and merge or restore silently promoting refused or obsolete state.
Take uncertain publication first, since what a device may claim after an unknown outcome bounds what the other schedules can assume.
Distinguish an unknown outcome from a definite refusal and from an already superseded state, and say what each permits locally and what it may attest to.
Start with publication succeeding but its acknowledgment being lost, then a sibling adopting and replacing that selection before the original device retries.
Compare definite refusal and an already-superseded result, recording each device's actual evidence and what it may use, attest to and report.
Then decide whether merge and restore need a rule beyond the ledger's ordering, or whether counters and predecessor links already deny them the ability to promote old state.
Challenge importing historical evidence without promoting a refused candidate, resurrecting obsolete priority or erasing the highest counter already observed.
Keep deliberate human reselection distinct from retry.

Result: the four outcomes are not degrees of certainty.
An unknown outcome carries no evidence of a competing selection, so it is ordinary incomplete evidence; a refusal and an already-superseded result are observed conflicts.
The device's standing and its retry are identical whether or not the lost write landed, so it never has to learn the outcome.
Retry must replay the record, because a retry expressed as a fresh selection resurrects a route a sibling deliberately replaced, and a human's deliberate reselection produces a mechanically identical record.
Merging and restoring published records need nothing beyond counter ordering; the two rules that do not follow from it constrain devices, not records — the private-to-published boundary is crossed only by an explicit publication, and the greatest observed counter is a monotone high-water mark.
The schedules, the disconfirming detail and the limits are under "Move 5 result" in [notes.md](notes.md).

The subsequent review found five counterexamples, three in this model's reports and two in Move 4's.
All five are now preserved as controls, and each fails against the code it was found in:

- One publication outcome describes one attempt.
  A retry refused as superseded said "not published" about a record an earlier attempt had already written, so `publication_claim` is now taken over every attempt on a record and never weakens, reported beside the separate `attempt_effect` and routing priority.
- A refusal is durable evidence for the device that observed it.
  Two refusals in a row let the fallback promote the first refused selection, because private history records what was chosen rather than what the store accepted.
- A refusal that names a greater selection is adopted, not quoted.
  Quoting it let a device attest to a record it did not hold and take its next deliberate counter below the selection it had just been told about.
- A tie is classified rather than dropped.
  The report returned early when there was no route, discarding the evidence for the tie, which also made `open_disagreement` unreachable: with a unique leader every rival is strictly older.
- A located branch is not evidence of resolution.
  `historical_divergence` is now `located_divergence`, `unresolved_rivals` is now `unaccounted_rivals`, and a control shows a deliberate human resolution and an author who never observed the branch produce the same classification.

The result is in "Move 5 correction" in [notes.md](notes.md).
Move 5's four-outcome, retry and merge results stand; these were reporting and eligibility gaps, not demonstrated runtime defects.

### Move 6: complete for the model; the probe is the next move

Settle exact route-content binding across provider finalization and account changes, which question 3 asks and no model has touched.
The evidence table's account-only change and differing-locator rows are the counterexamples.
Compare a frozen projection with other explicit bindings, state the atomicity and merge properties each requires, and do not assume current table boundaries.
Then probe the real publication and adoption boundaries required by the candidate, rather than continuing to model them.

Result: the binding question and the finalization question are separable, and both are now decided, in [models/model_route_binding.py](models/model_route_binding.py).
Three bindings were compared by what a signer reads — the record, the mutable rows at signing time, or content it holds against a digest.
Reference binding is falsified: after an account row moves, a sibling's repair contradicts the selection it repairs with two authentic signatures and nobody at fault, and merging a record set with a peer's account row derives a route neither device signed.
The digest is correct and was rejected on cost, since the record alone can no longer support repair; that rejection is conditional on route content being publishable to siblings in the clear.
Freezing needs no atomicity beyond writing one immutable record, and it is closed under union merge because it reads nothing; reference binding needs cross-row consistency that union merge cannot provide.
No representation rescues a record minted before the provider settles the locator, so finalization before minting is a separate ordering rule.
Binding also runs one way: identical route content under distinct selections stays distinct, so route equality is never a deduplication key.
The schedules, the conditional rejection and the limits are under "Move 6 result" in [notes.md](notes.md).

The next move is to check the provider and publication assumptions against real components using local services.
Proceed in this order:

1. **Interrupted provider finalization.**
   Pause before and after storage exists and before the final locator is recorded.
   Establish what counts as completed finalization, what evidence survives interruption, and what a sibling can recover from a partially materialized allocation.
2. **Publication and sibling adoption.**
   Exercise lost acknowledgments, superseding refusals and interrupted repair through real boundaries.
   Check whether a sibling receives enough frozen content to attest to the same selection, and whether generic publication or restore can silently publish private or refused candidates.
3. **Design decision and implementation handoff.**
   Use the probe results to revise unsupported assumptions, identify the actual account fields a route must freeze, and settle the public-predecessor-link tradeoff between diagnostic value and metadata exposure, including the required agreement to revise #224.
   Prepare the implementation handoff only after the remaining design choices are accepted.

Start with interrupted finalization; further modeling is not currently the bottleneck.
The five models passing does not establish runtime behavior or authorize implementation, and #238 remains open until its correctness requirements are implemented and validated.

### Documentation housekeeping still pending

Add further ledger entries as they are earned.
[follow-up.md](follow-up.md) still presupposes a design that does not exist and should shrink to a stub until there is one.
Incorporating `2ebe983` from `main` and updating the stale-branch status in [doc-fixes.md](doc-fixes.md) remain separate pending work.
The schedules below remain the catalog for subsequent rounds.

## Evidence that would advance the discussion

Use concrete schedules to challenge a candidate before expanding it into an implementation checklist.
Start with two devices, explicit pause points and both delivery orders.
Add devices or other cases where they expose a distinct assumption.
For each schedule, record each device's evidence, acceptable effects before conflict discovery, detection requirements, response after discovery and conditions for further progress.
Do not require all schedules to end in agreement or imply detection without the necessary evidence arriving.

| Question or schedule | What it should establish |
| --- | --- |
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

Small executable models and focused probes are in scope when they answer an open question.
A model must state its assumptions, explored bounds and limitations; a finite witness is not a proof of the runtime.
If a candidate relies on an existing component's semantics, inspect or probe those semantics rather than assuming them in a model.
If those semantics are unsuitable, reconsider the component or design instead of preserving the implementation by default.
Use local mocks or services for provider interactions.
Record actual commands and results in the notes; do not imply that suggested witnesses have run.

## Scope and eventual handoff

The present work is discussion and design investigation.
Do not turn a promising candidate into runtime work or accepted spec language without a design decision.
Full app provisioning UX, the Files capstone, provider migration and old-location cleanup remain outside this branch.

When the discussion supports a decision, state the chosen semantics, rejected alternatives and reasons, assumptions, remaining limitations and supporting evidence.
Then turn this agenda into an implementation handoff and agree where the intended design belongs in the specs.
The earlier split into a central implementation follow-up and a later integration follow-up is retained as a tentative outline in [follow-up.md](follow-up.md).
Their concrete scope must follow the design.
Issue #238 remains open until its correctness requirements are implemented and validated; discussion or model completion is not issue completion.
A human handles the PR and branch-folder cleanup.
