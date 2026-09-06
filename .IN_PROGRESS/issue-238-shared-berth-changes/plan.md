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

Open.

The remaining design questions are enumerated once under "Questions for the next rounds".
The succession and attestation half of question 1 is the next one to close, for the reasons under "Next moves".

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
The next small increment should write a reviewable candidate contract for sibling repair before building the executable model.
The model and comparison should then support a decision about separation with reasons, limits and a falsifier, if the evidence warrants one.
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

### Move 2: evaluate separation through sibling repair

Prefer distinct attestation identities carrying stable selection/succession information and exact route content.
The delayed-signing witness rejects signing-time priority but does not choose this candidate over an identity reserved at selection time.
Sibling repair supplies the stronger discriminator.
Compare the reserved-identity candidate on its own simplicity merits, independently of the current schema.

First write a short candidate contract stating what durable evidence identifies a selection, binds its exact finalized route, and lets another currently trusted sibling attest to it.
Explain what a recipient may conclude from one attestation and from several, and what coordination each candidate requires.
Make the durability assumptions explicit obligations for later runtime validation.
The contract gives the subsequent model a reviewable claim to test; drafting it does not accept the candidate.

Then use a small executable model with two cases:

- Durable evidence describes one selection and its exact finalized route.
  A becomes unavailable before signing; B adopts the evidence and issues its own attestation; A later resumes and issues another.
  Neither attestation creates a successor or requires another allocation.
- B's attestation changes one route field while retaining the same claimed selection/succession.
  Once both attestations arrive, the contradiction remains visible rather than disappearing behind an identity tie-break.

For each case, state the evidence each actor needs, the meaning of its claim, and acceptable recipient behavior before and after both attestations arrive, exercising both delivery orders.
Before contradictory evidence arrives, the recipient can judge only what it has.
Define the model's assumptions about durable evidence explicitly; model success does not prove that NoteToSelf supplies those guarantees.
Keep the contract independent of runtime table and helper boundaries.
Record commands, results and any disconfirming case alongside the model in this branch folder.

### Move 3: challenge succession with competing branches

After the separation experiment, test a scalar counter as a candidate, not an accepted representation.
Disconnected siblings create competing successors from one predecessor; one sibling then advances its own branch again.
The competing announcements now have different counters, so detecting only different routes at equal counters would miss the disagreement.

Explain whether the higher value merely determines provisional routing, which retained evidence exposes the competing history, who can inspect it, and what communication detection requires.
Distinguish public succession claims from private retrospective checking.
If the candidate needs peer-verifiable selection history, argue the substantive change to #224's rejection of a peer-verifiable selection DAG.
Do not infer agreement from numeric order or require detection before the necessary evidence arrives.

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
