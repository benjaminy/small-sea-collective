# Open discussion: shared berth allocation and routes

Issue: [#238](https://github.com/benjaminy/small-sea-collective/issues/238).
Branch: `issue-238-shared-berth-changes`.
Status: open research discussion; no protocol or implementation plan accepted.
This file is the discussion agenda until there is a design worth handing to an implementer.
A separately reviewed correction of two false Hub spec claims is in [doc-fixes.md](doc-fixes.md); the user plans to make it on `main`.
Before resuming this discussion, follow that file's return instructions to check whether the corrections landed and whether this branch contains them.
Their completion changes neither the open design status nor #238's implementation requirements.

## How to approach the discussion

The existing implementation gets no weight in choosing the design.
Existing schemas, wire formats, selectors, helper boundaries and micro tests are neither requirements nor reasons to prefer a candidate.
There is no deployed product, installed user base or production data requiring compatibility.
Judge proposals by their semantics, correctness, conceptual simplicity and fit with the project's purposes, not by fields added or code preserved.
Keep schema/version markers so later compatibility work remains possible, without building compatibility machinery now.

Code can expose a counterexample or tell us what a component actually does.
That evidence can falsify an assumption about using the component; it cannot require us to retain the component or its current behavior.
Separate deliberate architectural choices from accidental implementation choices, and examine the reasons for the former when they matter.
Record arguments and disconfirming evidence in [notes.md](notes.md).
No candidate becomes accepted because it was investigated first or described in the most detail.

## Problem to understand

A participant's devices share a berth allocation, while teammates learn routes through signed announcements that may arrive late or in a different order.
What makes an allocation the participant's selected allocation, and what authorizes a device to announce its route?
How can a sibling finish interrupted work without changing that selection or reviving a superseded route?

The desired properties motivating this discussion are:

- Competing device actions must not silently authorize incompatible selections.
- Delayed signing or delivery must not turn an earlier selection into a successor.
- A currently trusted sibling must be able to finish interrupted work from durable shared evidence without unnecessarily allocating storage again.
- Provider finalization and account metadata changes must not substitute different route content under an earlier authorization.
- Conflicts, uncertain outcomes and human choices must remain explicit.
- Reports must distinguish local decisions, provider effects, shared settlement and delivery to teammates.
  Retaining an old valid announcement does not establish that its storage remains reachable.

[#224's decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) supplies the current deliberate starting point: participant-owned allocations, equal trusted siblings and coordination through NoteToSelf.
It rejects a peer-verifiable selection DAG and defers allocation-generation binding in announcements.
Examine those choices by their reasons; identify and discuss a substantive conflict rather than silently treating an implementation convention as one of those decisions.
A public succession value, allocation-generation identity and peer verification of private history are separate questions.

The repository's architectural responsibilities remain applicable: Manager owns management policy, Hub mediates provider I/O, and generic sync should not acquire allocation policy.
Core and arbitrary app berths should admit the same explanation of allocation and route authority.

## Questions for the next rounds

1. What are the distinct concepts?
   Distinguish storage allocation identity, selected route content, succession, signed attestation identity and delivery.
   Which changes create a new allocation, which create a successor route, and which merely attest to or deliver an existing selection?
   Does a timestamp serve an actual human or protocol need?
2. What constitutes settlement and signing authority?
   Which durable evidence can a sibling adopt, and what does successful publication establish?
   Is finalized-state publication before signing sufficient, and what additional assumptions does it need?
   How should an unknown publication outcome differ from a definite refusal or an already superseded state?
3. What binds authority to exact route content?
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
6. Where are the authorization boundaries?
   Explain creation, ordinary changes, provider finalization, integration, generic publication and courier delivery.
   Could merging or restoring state authorize a refused candidate or revive obsolete ordering?
   Do not let today's entry-point organization define the protocol.

## Candidates to examine

One candidate separates independently minted announcement identities from a signed succession value settled with exact route content.
Its attraction is that sibling attestations can share route authority without sharing a primary key or reserving a signer.
It still needs a settlement argument, successor rules, content binding and treatment of contradictory attestations.

Earlier discussion proposed reserving the announcement identity itself, with either an intended signer or a composite key.
Those candidates remain available for substantive arguments, but preserving the existing format gives them no advantage.
The need to coordinate signer or timestamp bytes may be a cost caused by that representation rather than a requirement of the problem.

These are examples, not an exhaustive menu or a decision to use reservations, scalar ordering, UUIDs or a particular row layout.
A stronger alternative should be expressible in the same terms: state, authority, transitions, assumptions and counterexamples.

## Recommended order for the next rounds

A review round on 2026-09-06 assessed the discussion's trajectory rather than the design.
Its recommendations follow; they are recommendations, not decisions, and the committee may reorder or reject them.
The supporting argument is in [notes.md](notes.md).

1. Answer question 1 and freeze the vocabulary.
   Five concepts are currently tangled: allocation identity, selected route content, succession, attestation identity and delivery.
   Naming them and stating which change produces which decides nothing, costs little, and shortens every argument that follows.
   This is the current blocker.
2. Re-adopt a defended candidate.
   The instruction that opened this discussion was that the existing implementation gets no weight, which was right.
   The present agenda generalized that into neutrality toward every candidate, which is a different move and was not argued for.
   A discussion with no defended position offers nothing to attack, and it converges slowly.
   Separating announcement identity from route succession is the strongest candidate on the table.
   State it as proposed with its obligations open, then let the counterexamples try to kill it.
3. Run one witness against that candidate: the delayed-signing schedule, two devices, both delivery orders, adversarial clocks.
   It motivated the branch and is the cheapest schedule to model.
   No witness has run in four rounds.
4. Then take question 3, content binding.
   It depends on actual Splice Merge behavior through the cross-row endpoint counterexample, so it likely needs a probe rather than argument.

The three branch documents currently restate the same open questions in three places.
Consider narrowing each to one job before the next round expands them again.

## Evidence that would advance the discussion

Use concrete schedules to challenge a candidate before expanding it into an implementation checklist.
Start with two devices, explicit pause points and both delivery orders.
Add devices or other cases where they expose a distinct assumption.

| Question or schedule | What it should establish |
| --- | --- |
| A selects X, B selects successor Y, A resumes | Delayed signing and delivery cannot promote X above Y, regardless of clocks. |
| Competing candidates and uncertain publication | Which state gains authority, how uncertainty is resolved, and why a refused candidate cannot escape. |
| Publication finds a descendant | Historical inclusion is distinguished from current selection; consider both related and unrelated changes. |
| Interrupted work and sibling repair | The durable evidence needed for repair, without unnecessary reallocation or coordinated reuse of signed identity. |
| Equivalent and contradictory attestations | Same-selection duplicates have defined behavior; contradictory authority is not hidden by an arbitrary tie-break. |
| Account-only change or differing provider locators | Authority binds complete route content, including values that might otherwise be read from separately changing state. |
| Human chooses a previously refused candidate | Resolution establishes appropriate succession instead of reusing a competing candidate's obsolete priority. |
| X, then Y, then an intentional return to X | The final choice outranks delayed Y even when an older matching X announcement already exists. |
| Merge, restore or generic publication | These operations cannot silently turn old or refused state into a newly authorized route. |
| Signer unavailable or no longer trusted | Explain sibling repair and peer trust behavior without claiming reachability from signature validity. |
| Local work succeeds but delivery fails | State and retry semantics remain honest at each boundary. |

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
