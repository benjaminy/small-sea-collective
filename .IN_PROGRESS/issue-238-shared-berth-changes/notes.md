# Discussion notes: shared berth allocation and routes

## Current framing

The user asked to turn the branch planning documents into an open discussion and give the existing implementation no weight in choosing a design.
This is a research project with no production compatibility obligation.
Earlier recommendations are hypotheses and discussion history, not a preferred baseline or accepted protocol.
The agenda is in [plan.md](plan.md); possible later work is in [follow-up.md](follow-up.md).

Compare conceptual mechanisms, their assumptions and their failure cases.
Existing code is useful for discovering counterexamples and checking the feasibility of using a component.
Its presence does not justify preserving its schema, wire format, API, tests or behavior.
Deliberate architectural decisions have reasons to examine; they must not be conflated with implementation accidents.

## Discussion so far

The original investigation focused on settling finalized allocation state through NoteToSelf before announcing its route.
It identified a delayed-signing race and proposed reserving ordering in shared state.
It then preferred reusing the existing announcement ID and explored intended-signer reservations and composite primary keys to make sibling repair possible.
That preference was not supported by a simplicity argument independent of the implementation.

The committee proposed separating announcement identity from route succession.
This has a substantive attraction: siblings could issue distinct signed attestations of one settled route without coordinating one primary key's payload or reserving a particular signer.
The associated schema and wire changes are not arguments against the design.
Neither the separation nor any specific succession representation has been accepted.

The distinction also changes which questions are fundamental.
One authorized route may have several signed attestations with different identities, signers and timestamps.
Requiring exactly one signed payload for that route was a consequence of reusing the ordering value as announcement identity.
Immutable identity still matters for each individual attestation and its replay.

A public succession value need not expose allocation-generation identity or let peers verify private NoteToSelf history.
Those are separate design choices when considering the earlier #224 decision.

## Counterexamples and unresolved questions

### Delayed signing

A settles route X and pauses before signing.
B settles successor Y and announces it.
A resumes with a later clock and signs X.
If priority is assigned at signing time, X can displace Y despite being the earlier selection.
A last-minute remote read merely moves the pause point.
The question is what durable authority constrains every delayed action, including one that cannot yet know about its successor.

### Complete route binding

A signs succession 8 using endpoint P.
Account metadata changes to Q while an allocation's succession remains 8.
B repairs by joining that allocation to current account metadata and signs Q at 8.
The signed rows have distinct identities and valid signatures but disagree on route content.

Whole-row merge cannot prevent that schedule if relevant values reside on independently changing rows.
A frozen route projection is one candidate binding; other representations must explain equally precise authorization.
There is no accepted requirement to put the reservation on today's allocation row.
Separate state is not inherently invalid, and one row is not inherently sufficient if signing reads other mutable values.

### Resolution and restoration

X at succession 8 was authorized; a competing Y also proposed 8 but was refused.
A later human choice of Y cannot simply publish Y unchanged at 8 if X can still be announced.
Returning to a previously used location similarly expresses a new choice, not restoration of that location's old priority.
The source and preservation of predecessor evidence remain open.
A scalar counter is a candidate representation, not an explanation of those transitions by itself.

Generic publication after integration or restoration must be included in this argument.
It must not silently grant authority to old content under obsolete ordering.
Exactly where this responsibility belongs depends on the protocol; it cannot be assigned solely by following today's signing helper boundaries.

### Equivalent and contradictory attestations

Several attestations of one route can be harmless even when their signatures and identities differ.
For a candidate using succession values, an identity tie-break can choose a representative among equivalent valid attestations.
Different routes at equal succession need explicit semantics; an arbitrary identity winner would conceal the contradiction.
State the limits of what peers can establish when they trust current devices without inspecting private settlement history.

### Repair and repeated operations

Separating identities could avoid a NoteToSelf publication solely to reserve a new signer after a sibling adopts settled state.
That does not establish that repair needs no communication: evidence adoption and team delivery are distinct steps.
Fresh identities do not automatically make repeated repair row-idempotent.
Clarify the desired behavior of replaying an existing attestation, issuing a sibling attestation and selecting a successor.

### Returning to the same endpoint

X is selected at succession 7, Y at 8, and then X again at 9.
Y's announcement is delayed; an old X announcement is already present.
A shortcut that treats endpoint equality as sufficient can suppress X at 9 and allow delayed Y to win.
This is a semantic distinction between route content and selection history, independent of whether the future implementation has a deduplication helper.

### Provider finalization

A provider may return a locator different from the requested one.
If only one device knows the final locator, a sibling cannot derive the same route from shared state and may materialize again.
What must be durable before repair is possible, and what authorizes announcing those exact values?
This observation does not by itself establish a required number of publications or a fixed materialize/publish sequence.

## Repository observations, with limited evidentiary roles

These observations describe the inspected implementation and explain the examples above.
They carry no preference for retaining it.

- `wrasse_trust/transport.py::select_effective_teammate_berth_storage` is the shared selector used by Manager's individual and batched queries and Hub's routing.
  Row loading and SQL ordering are duplicated; selection policy is not independently implemented twice.
  The committee's suspected duplicated-policy defect was not found.
- `announcement_id` is currently the announcement primary key and sole selection ordering key.
  `_uuid7_after` assigns order immediately before signing and returns a fresh UUID when it exceeds the observed bound.
  Re-verified at `provisioning.py:3415` for the helper and `provisioning.py:3519` for the call site, which sits inside the signing block and takes its lower bound from the predecessor this device last observed.
  This supplies a concrete delayed-signing counterexample, not a constraint on future identities or ordering.
- Canonical announcement bytes include `announced_at` and `signer_key_id`.
  Siblings reusing one reserved ID therefore produce different rows under one primary key.
  This is a cost of that candidate, not a reason all sibling repair needs coordination of signed bytes.
- No CLI/web timestamp display or routing use of `announced_at` was found in repository-wide searches.
  Publication results and courier sidecars carry it; verification, import validation and same-ID row comparison consume it.
  The earlier statement that nothing reads it was inaccurate.
  Whether to retain a timestamp depends on a real product or protocol purpose; distinct announcement identities remove the collision argument for deleting it.
  If retained for audit, signing it preserves attribution.
- `splice_merge/core.py::reconcile_deltas` compares whole rows keyed by primary key and reports conflicts.
  The current allocation schema's unique berth index and row conflicts expose competing replacements and locator writebacks.
  These facts establish neither a mandatory row layout nor a general content-binding proof.
  `cloud_storage.protocol` and `cloud_storage.url` currently live separately from `berth_cloud_allocation`.
- Cod Sync's `already_present` includes a stored descendant of the attempted head.
  Manager's `push_note_to_self` currently discards `PublishResult`, including attempted and observed heads.
  Historical inclusion and present selection are different facts regardless of the future publication API.
- `_publish_core_route` signs and commits without publishing NoteToSelf, and its final allocation mismatch check occurs after those effects.
  Creator setup and the public announcement wrapper offer other direct signing paths.
  These are examples for an eventual boundary audit, not prescribed future entry points.
- Current transport-only deduplication can return another sibling's announcement, while the shared route publication path requires the current signer for invitation delivery.
  Invitation requirements and established-team repair need separate justifications.
- The Hub spec's claims about independent sibling locations conflict with #224's shared ownership decision.
  Its newest-by-UUIDv7 safety claim fails the delayed-signing schedule.
  Those claims should not be carried into an accepted design merely because they appear in a spec.
  Located precisely: `packages/small-sea-hub/spec.md` lines 338-341 for the sibling-location claim, and lines 357-366 for the concurrency passage that calls cross-device first-use races recoverable clutter rather than a correctness failure.
  The Manager spec does not make the second claim; `packages/small-sea-manager/spec.md:1172` states that ordering across devices publishing concurrently is not settled by the newest-wins rule.
  The two specs therefore contradicted each other at the inspected branch revision `f03ee3d`, independently of this discussion.
  [doc-fixes.md](doc-fixes.md) tracks the separately planned correction on `main` and the checks needed when returning here.

## Deliberate choices and prior reasoning

The earlier investigation read #224 as choosing participant-owned shared allocations, equal trusted siblings and NoteToSelf coordination, rejecting a peer-verifiable route-selection DAG and deferring allocation-generation binding in announcements.
The discussion should name any proposed change to those choices and argue its merits.
It should not treat every wire change as an ownership or trust change.

`Archive/design-record-core-route-reconciliation.md` explains generation checks and serial route ordering.
Its concurrency question remains open; the record supplies no distributed ordering proof.
`Archive/design-record-issue-48-note-to-self-self-integration.md` explains applying row deltas onto live state rather than replacing a live database file.
That is relevant evidence about protecting local changes if that integration mechanism is used, not an exemption from evaluating a future design.

Manager policy ownership, the Hub provider boundary, device-local credentials and explicit semantic conflict handling remain applicable architectural context.
No production rollout, compatibility layer or speculative recovery system is called for by this discussion.

## Review round, 2026-09-06: branch trajectory

This round assessed how the discussion is proceeding rather than which design should win.

What is working.
The counterexample catalog above is the branch's most durable asset; those schedules survive whichever candidate is eventually chosen.
Separating announcement identity from route succession is a real insight rather than a restatement of the defect.
Reusing `announcement_id` as both primary key and sole ordering key is what makes sibling repair awkward:
canonical bytes include `announced_at` and `signer_key_id`, so two siblings cannot produce one row under one reserved ID.
The intended-signer and composite-key candidates were both working around a cost that representation created.
Two self-corrections also count in the branch's favor: the inaccurate claim that nothing reads `announced_at` was retracted, and the suspected duplicated-selector defect was dropped when the evidence did not support it.

Where it drifted.
The `discussion` commit removed the previous round's named recommendation together with the burden of proof attached to it, and replaced them with an unranked candidate list.
The originating instruction was that the existing implementation gets no weight.
Generalizing that to neutrality toward every candidate is a separate move, and it was not argued for.
It has a cost: after four commits the branch holds roughly 360 lines of planning prose, no decisions, and no executed witness.
A sizable share of the newest prose instructs how to discuss rather than stating substance.

Raw material for question 1, offered as input and not as an accepted vocabulary.
Five concepts are currently tangled:

- allocation identity, including generation
- selected route content: protocol, endpoint, final locator, and any account state a signature must bind
- succession, the ordering that decides which selection supersedes which
- attestation identity, together with its signer and timestamp
- delivery to teammates

The open questions are currently restated in all three branch documents.
[follow-up.md](follow-up.md) in particular presupposes a design that does not exist yet.

## Investigation and validation record

The 2026-09-06 narrow doc-fix review supports correcting only the two Hub passages on `main`.
It re-read #224's decision comment through `gh api`, the cited Hub/Manager passages, and `_uuid7_after` and its signing call site.
The delayed-signing example was qualified as a possible ordering reversal, not a guarantee across arbitrary device clocks.
The correction must preserve announcement-based sibling reads while distinguishing shared ownership from currently unresolved coordination.
No `main` correction has been verified in this round, and no #238 protocol has been accepted.
The return handoff is in [doc-fixes.md](doc-fixes.md); older claims and line references in these notes describe the pre-correction snapshot.
This round changes branch documents only and runs no executable witnesses or micro tests.

Earlier rounds read #238 and its comment, #224 and its decision, #139/#235, README, architecture, Manager/Hub specs, relevant archived reasoning and focused micro-test structure.
They inspected route publication, provider finalization, NoteToSelf publication/integration, the shared schema, Splice Merge and the shared transport selector.
The committee follow-up traced Manager and Hub selector callers, timestamp consumers and courier import, and identified the cross-row endpoint and transport-only deduplication cases.

The 2026-09-06 review round read the four branch commits, #238, #224 and its decision comment, and re-read the ordering helper, its call site, and the Hub and Manager spec passages cited above.
It ran searches and file reads only; it wrote no code and ran no tests.

No executable protocol witnesses or micro tests have run on this design branch.
The earlier discussion edit reorganized the three branch documents for open discussion and removed implementation-preservation preferences and premature mandates.
This branch has changed no runtime code or permanent specs; the separate `main` correction must be checked on return.
Future probes should record their actual commands, results, assumptions and limitations here.
