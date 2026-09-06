# Discussion notes: shared berth allocation and routes

## Current framing

The user asked to turn the branch planning documents into an open discussion and give the existing implementation no weight in choosing a design.
This is a research project with no production compatibility obligation.
Protocol recommendations remain hypotheses and discussion history, not accepted designs.
The return discussion below records the user's framing constraints and distinguishes them from proposed next steps.
The agenda is in [plan.md](plan.md); possible later work is in [follow-up.md](follow-up.md).

Compare conceptual mechanisms, their assumptions and their failure cases.
Existing code is useful for discovering counterexamples and checking the feasibility of using a component.
Its presence does not justify preserving its schema, wire format, API, tests or behavior.
Deliberate architectural decisions have reasons to examine; they must not be conflated with implementation accidents.

## Return discussion, 2026-09-06: provisional decisions and possible endless disagreement

The user challenged the assistant's question about what makes a selection "authoritative."
Small Sea is radically decentralized: a device must treat its view as at best probably true, decide what evidence is good enough to proceed, and support retrospective checking.
The assistant accepted this as a substantive correction because asking for a durable event that grants authority risked presupposing globally settled state.
Earlier references below to settlement and authority describe prior candidate reasoning; they do not establish global agreement or finality.
Enforced local authorization remains a separate concept.

The user also explicitly accepted that devices and/or users can enter a never-ending sync loop.
Human scale and engineering can make that less likely, but making it impossible is not a useful requirement.
The planning frame therefore permits persistent disagreement and does not require guaranteed convergence, automatic resolution or eventual termination of every sync loop.

The assistant proposed separating safety obligations from conditional progress claims.
Delayed signing must not recast an earlier choice as a successor, and mutable account data must not change what an earlier selection or attestation means.
The design must distinguish acting with incomplete evidence from disregarding a known contradiction.
Retrospective checks need explicit evidence and communication requirements: detection may remain impossible while evidence is unavailable, and later discovery cannot undo every effect.
Visible unresolved disagreement is an acceptable outcome; human intervention is available without being a promise of final agreement.
Accepting possible endless loops still leaves avoidable churn worth investigating, especially retries that create fresh selections without new evidence or a renewed choice.

The proposed next round uses two disconnected siblings making competing choices.
For each transition, write what the device knows, what provisional action it may take, what claim and evidence it retains, and what happens when contradictory evidence arrives.
Ask what may happen before discovery, what becomes visible afterward, and what conditions permit further progress.
A short working glossary should support this sketch rather than freeze substantive decisions into definitions.
Pair the delayed-signing witness with competing successors from one predecessor, then investigate content binding and repair.
Separating selection/succession from attestation identity remains the assistant's recommended starting hypothesis, not a user-accepted protocol.
No requirement to permit disconnected route selection, particular retrospective mechanism, or succession representation was decided in this session.

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
  [doc-fixes.md](doc-fixes.md) records the correction verified on `main`; this branch still contains the pre-correction passages.

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

## Planning round, 2026-09-06: next moves

This round chose what to do next rather than which design should win.
Its output is the ledger and the "Next moves" section in [plan.md](plan.md).

The neutrality generalization is why rounds cannot close questions.
The user's instruction was that the existing implementation gets no weight.
The `discussion` commit extended that into withholding a recommendation among candidates, which is a separate rule and was never argued for.
Under it no round can close anything, because closing a question requires ranking, and ranking had been made suspect.
The unranked candidate list and the growing share of prose about how to discuss both follow from it.
That generalization is now withdrawn in the agenda.

Harness evidence for the delayed-signing witness.
`_diverge_two_devices` at `packages/small-sea-manager/tests/test_note_to_self_refresh.py:466` builds two real installations of one identity against MinIO with a common ancestor and divergent heads, and `test_divergent_note_to_self_push_reports_integration_required` asserts against that scene.
Move 1 extends this rather than adding infrastructure, which makes the witness considerably cheaper than earlier rounds assumed.
This is a reading of that test file; no witness has been written or run.

The clock assumption is narrower than earlier rounds recorded.
Those rounds qualified the delayed-signing schedule as permitted rather than guaranteed, because UUIDv7 ordering across devices with skewed clocks is not settled.
Re-reading `_uuid7_after` and its call site suggests two installations running in sequence on one machine under monotone time should reproduce the reversal with no clock injection at all:
A's lower bound is the predecessor A last observed, so A's resume-time UUID exceeds B's successor.
That is an expectation for move 1 to test, not an observed result, and the general qualification remains correct.

## Review round, 2026-09-06: evidence for the next coding round

The review supports running a witness before expanding the design, with the tightened schedule now in [plan.md](plan.md).
B must observe durable finalized X before deliberately replacing it with Y; otherwise the experiment demonstrates competing choices rather than reversal of an observed succession relationship.
The two-installation harness supplies useful setup patterns, but its refused-publication endpoint does not establish this route history or both siblings' team-announcement readiness.
Sequential execution also does not guarantee distinct UUID milliseconds, so the witness must expose its clock and ordering-bound assumptions.

A reproduced delayed-signing defect would reject signing-time priority without choosing between separate attestation identities and an identity reserved at selection time.
Sibling repair is the stronger discriminator: compare distinct attestations of identical selected content with contradictory content claiming equal succession.
The recommendation remains separation, with the representation open and any decision conditional on the evidence.
Preserve the eventual executable probe in the branch research folder; no code is requested for this documentation round.

## Investigation and validation record

The return-session distillation updates only branch documents.
It revises the agenda around provisional local decisions and retrospective checking, accepts possible endless sync loops, and consolidates duplicated handoff questions into the agenda.
Validation is a documentation diff review and `git diff --check`; no executable witnesses or micro tests ran, and no protocol or runtime implementation was accepted.

The return check on 2026-09-06 inspected local `main` and `git diff 2ebe983^ 2ebe983 -- packages/small-sea-hub/spec.md`.
Both documentation corrections landed in `2ebe9833051e56a1549e16496c0adcca96fc439e`; the diff changes only the two reviewed passages.
The current branch at `a4e0854` has not incorporated that commit.
This check establishes documentation status only; no protocol decision or executable witness follows from it.

The 2026-09-06 narrow doc-fix review supports correcting only the two Hub passages on `main`.
It re-read #224's decision comment through `gh api`, the cited Hub/Manager passages, and `_uuid7_after` and its signing call site.
The delayed-signing example was qualified as a possible ordering reversal, not a guarantee across arbitrary device clocks.
The correction must preserve announcement-based sibling reads while distinguishing shared ownership from currently unresolved coordination.
That earlier narrow review had not yet verified the `main` correction; the return check above now records it.
No #238 protocol has been accepted.
The return handoff is in [doc-fixes.md](doc-fixes.md); older claims and line references in these notes describe the pre-correction snapshot.
This round changes branch documents only and runs no executable witnesses or micro tests.

Earlier rounds read #238 and its comment, #224 and its decision, #139/#235, README, architecture, Manager/Hub specs, relevant archived reasoning and focused micro-test structure.
They inspected route publication, provider finalization, NoteToSelf publication/integration, the shared schema, Splice Merge and the shared transport selector.
The committee follow-up traced Manager and Hub selector callers, timestamp consumers and courier import, and identified the cross-row endpoint and transport-only deduplication cases.

The 2026-09-06 review round read the four branch commits, #238, #224 and its decision comment, and re-read the ordering helper, its call site, and the Hub and Manager spec passages cited above.
It ran searches and file reads only; it wrote no code and ran no tests.

No executable protocol witnesses or micro tests have run on this design branch.
The earlier discussion edit reorganized the three branch documents for open discussion and removed implementation-preservation preferences and premature mandates.
This branch has changed no runtime code or permanent specs; the separate `main` correction is verified but has not been incorporated here.
Future probes should record their actual commands, results, assumptions and limitations here.

The 2026-09-06 next-move planning round read the four branch documents, #238, the ordering helper and its call site, and the two-installation harness in `test_note_to_self_refresh.py`.
It ran searches and file reads only.
It wrote no code, ran no tests and executed no witness; the delayed-signing expectation it records is untested.

The subsequent review inspected the latest commit, branch documents, repository architecture, installation harness, route-publication path and shared selector.
This distillation revises the next-round agenda and records the reasoning above.
Validation is a documentation diff review and `git diff --check`; no executable witness or micro test has run, and no protocol has been accepted.

## Move 1 result, 2026-09-06: the delayed-signing witness reproduces

The witness ran and reproduced the defect.
Probe source is `probes/probe_delayed_signing.py` with `probes/conftest.py`; command:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_delayed_signing.py -x -q
```

It passed on three consecutive runs against `fcac0a2` with no runtime code changed.
The probe asserts the defect, so a passing run means the defect is present.

Schedule as staged.
Two installations of one identity share a team clone and a Core berth.
Device B reaches trusted-device status through the real linked-device team join
(`prepare_linked_device_team_join`, `create_linked_device_bootstrap`, `finalize_linked_device_bootstrap`),
so both devices sign as the same teammate with different device keys, each `derive_team_join_state` reporting `admission == "finalized"`.
`create_team` already announces a first route, so both devices start from a common predecessor announcement rather than from nothing.

1. A calls `reconcile_team_route(new_location=True)` with `provisioning.publish_teammate_berth_storage_announcement` patched to raise.
   The replacement allocation X is durable in shared NoteToSelf and its bucket is materialized at MinIO, and the call returns `route="pending"`, `route_reason="route_preparation_error"`.
   That is the pause before signing, produced by the operation's own retryable failure path rather than by a stub.
   A then pushes NoteToSelf.
2. B refreshes NoteToSelf and the probe asserts B's `derive_team_join_state` allocation equals X field for field.
   This is the load-bearing observation: without it the schedule is competing selections, not reversal of an observed succession.
   B then calls `reconcile_team_route(new_location=True)`, which replaces X with Y and signs Y, and pushes NoteToSelf.
3. A, which never refreshed, still reads X, and `reconcile_team_route()` signs X.
4. Both authentic signed rows are loaded into two copies of A's team `core.db`, inserted in both delivery orders,
   and resolved through `provisioning.selected_teammate_berth_storage_announcement`, which calls `select_effective_teammate_berth_storage`.

Result.
In both delivery orders the selector returns `status="announced"` with X's announcement ID and X's location.
A recipient therefore routes to the location B observed and deliberately replaced.
Observed values from one run:

| Row | announcement_id | announced_at |
| --- | --- | --- |
| predecessor | `01a077c113e5774580c208ae8faa6a53` | 17:25:37.381094Z |
| Y, B's successor | `01a077c1208076b48a303cd00edf131d` | 17:25:40.608861Z |
| X, A signed last | `01a077c1245f77a590945477457fd912` | 17:25:41.599152Z |

Clock assumption, narrowed by the result.
Earlier rounds worried that the reversal might need same-millisecond ties or `_uuid7_after`'s forcing branch.
It needs neither.
A resumed roughly 900 ms after B signed Y, so A's plain `uuid7()` already exceeded Y's ID and the lower-bound branch never ran.
`announced_at` orders the same way, so a timestamp-based rule would not help either.
The mechanism is simply that announcement ordering is minted from the signer's own clock at signing time, and A's signing is later in real time than the successor it never saw.
No clock was injected; both installations ran sequentially in one process on one machine.
The witness therefore does not establish anything about skewed clocks across machines, and it does not need to: monotone real time is enough to reverse the relationship.

Limits.
Delivery is staged by inserting the peer's authentic signed row into a copy of the recipient's team database.
The rows and signatures are real and the selector is the one Manager and Hub use, but the runtime's fetch and merge path did not carry them.
NoteToSelf publication and adoption, provider materialization and both signings are real.
One reproduced schedule is evidence about this code path, not a proof about the runtime.
The witness fixes nothing and no design follows from it by itself;
it rejects signing-time priority as an ordering rule and leaves the choice between separate attestation identities and an identity reserved at selection time to move 2.

## Post-witness planning, 2026-09-06

Review of `98c28ae` supports closing the signing-time question in the plan's ledger.
The interrupted operation takes its real retryable failure path, and direct insertion of authentic announcements limits the delivery claim without invalidating the selector counterexample.
The conclusion concerns priority assigned at signing time, including a signing timestamp; it does not reject every timestamp use or select a succession representation.

One recipient-side control remains useful.
`select_effective_teammate_berth_storage` returns after validating the highest-ranked acceptable row, so selecting X from both rows does not exercise Y's acceptance in that recipient.
Assert that Y alone selects Y there, then add X and observe the reversal.
B's finalized admission in its own installation supports the setup but does not replace this check.
Full fetch/merge delivery can wait for integration validation.

The next discriminator is sibling repair from durable evidence of one selection and its exact finalized route.
Distinct attestation identities remain the preferred candidate because another trusted sibling can sign without creating a successor, reallocating storage or coordinating one signed payload.
Compare equivalent sibling attestations with a changed route field under the same claimed selection/succession; the latter must remain visibly contradictory once both arrive.
A small executable model should state its evidence assumptions and recipient behavior before and after discovery.
Its success would not establish NoteToSelf's publication or adoption guarantees.

The subsequent counter experiment must include competing successors followed by another advance on one branch.
The surviving announcements can have unequal counters, so equal-counter conflict detection alone is insufficient.
The candidate must distinguish provisional route ordering from evidence of competing history and identify who can inspect that evidence.
Public succession claims and private retrospective checking need separate explanations, particularly given #224's rejection of a peer-verifiable selection DAG.

This documentation round records the signing-time conclusion and revises the next-round agenda.
It changes no probe or runtime code, reruns no witness, and accepts neither separation nor a particular succession representation.
Validation consists of reviewing the documentation diff and running `git diff --check`.

## Move 1 control, 2026-09-06: Y alone is acceptable in the recipient

The recipient-side gap named in "Post-witness planning" is closed.
`select_effective_teammate_berth_storage` returns after validating its highest-ranked acceptable row,
so the two-row assertions never showed that Y would have been accepted by that recipient at all.
The probe now selects from each single-row database before inserting the second row,
and asserts the returned announcement is the one just inserted.
In the `y_then_x` order that is the control the plan asked for: Y alone selects Y, then X is added and the selection flips to X.
The `x_then_y` order gets the same single-row check, which costs nothing and keeps the two orders symmetric.

Command, unchanged:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_delayed_signing.py -x -q
```

It passed on two consecutive runs against `11c87ba` with the added control and no runtime code changed.
Both insertion-order assertions and the announcement ID comparison are retained.
Observed values from the first run:

| Row | announcement_id | announced_at |
| --- | --- | --- |
| predecessor | `01a07852541b77a8b644920422bb97bd` | 20:04:16.539108Z |
| Y, B's successor | `01a07852619d7ca3871714aed9b32999` | 20:04:19.997295Z |
| X, A signed last | `01a07852651876afa1135ee15e639631` | 20:04:20.888088Z |

The reversal reproduces exactly as recorded in "Move 1 result", with the same ordering mechanism and the same roughly 900 ms resume gap.
The control adds one fact: Y's rejection is not what causes the recipient to prefer X.
The recipient accepts Y when it is the only row, and stops preferring it as soon as a higher-ranked acceptable row exists.
Ranking, not validation, produces the defect.

Limits are unchanged.
Delivery is still staged by direct insertion of authentic signed rows, so this says nothing about the runtime's fetch and merge path.
The probe still fixes nothing and still selects no design.

## Review of `e7e811c` and next increment, 2026-09-06

The control closes Move 1's remaining recipient-validation gap.
Inspection of the probe and selector supports the recorded interpretation: Y is acceptable on its own, and X wins once both are present because of announcement ranking.
This strengthens the delayed-signing counterexample without changing runtime behavior or the existing signing-time conclusion.
It supplies no new reason to choose separate attestation identities over an identity reserved at selection time.
More reproductions of this schedule would not settle that comparison.

The recommended next increment is a short sibling-repair candidate contract, followed by the executable comparison already specified in Move 2.
The contract should name the durable selection evidence, its binding to exact finalized route content, the evidence a trusted sibling needs to attest, and the recipient's conclusions from one or multiple attestations.
Compare the meaning and coordination required by separate and reserved identities on their own merits.
Treat the model's durability assumptions as obligations for later runtime validation rather than claims already established about NoteToSelf.

The two model cases remain equivalent sibling attestations after interrupted signing and contradictory route content under the same claimed selection/succession, each in both delivery orders.
Record a decision about separation only if that comparison earns one, including reasons, limits and a condition that would reopen it.
Then challenge succession with competing successors followed by another advance on one branch, where unequal counters can conceal disagreement.
Runtime changes remain later work.

This session updates the plan to mark the control complete and separate contract drafting from the subsequent model.
It drafts neither the contract nor the model and accepts no new protocol design.
The review inspected code and previously recorded results; it did not rerun the probe.
Documentation validation consists of reviewing the diff and running `git diff --check`.

## Move 2 contract draft, 2026-09-06

The sibling-repair candidate contract requested by the previous round is drafted in [contract-sibling-repair.md](contract-sibling-repair.md).
It names the durable evidence (selection ID, predecessor, allocation ID, frozen route content, materialization marker),
states that adoption alone entitles a trusted sibling to attest,
and separates a recipient's conclusions for one attestation, equivalent duplicates, contradictory content under one selection, and different selections.

The contract identifies the discriminator the model must exercise.
Separate attestation identities let duplicates coexist, so a contradiction under one selection stays representable, and ordering must come from payload fields alone.
A reserved identity makes two siblings' attestations collide on one identity instead of coexisting;
fixing the signer as well reintroduces the specific-device dependence repair exists to remove,
and not fixing it leaves the reserved identity unable to distinguish equivalent attestations from contradictory ones.
That is an argument to test, not a decision, and the contract accepts neither candidate.

Two current-code facts were checked while drafting rather than assumed.
`resolve_berth_cloud_allocation_intent` deletes the berth's allocation row before inserting the replacement,
so no predecessor selection evidence survives a change today.
`TeammateBerthStorageAnnouncement` carries no selection or succession field, and `select_effective_teammate_berth_storage` ranks solely on `announcement_id`.
The contract therefore assumes evidence the runtime does not yet produce, and records that as an obligation rather than a claim.

This round wrote documentation only.
It builds no model, changes no probe or runtime code, reruns nothing, and earns no ledger entry.
Validation is review of the diff plus `git diff --check`, which reports nothing.

## Review of `10232be`: make the executable comparison fair

The latest commit supplies a testable contract but no new executable evidence.
Its argument against reserved identities is too strong: sharing an identity does not inherently prevent a recipient from retaining multiple signed payloads and exposing their differences.
Separate identities also require payload comparison to detect contradiction.
Assuming collision overwrite would build the preferred result into the model rather than test the candidates.
This corrects the comparison in the preceding contract-draft entry.

The plan and contract now compare independent attestation IDs with a reserved ID that permits multiple signers and preserves conflicting evidence.
Separate IDs remain preferred because each signing act can be represented independently, but both candidates may satisfy the required properties.
If they do, the choice needs an argument about conceptual simplicity, not a claim that reservation cannot work.

The next increment should run the interrupted-signing and contradictory-content schedules for both candidates in both delivery orders, including a differing-predecessor variant under one selection ID.
It should deliver the model, recorded results, any contract corrections, and a decision with assumptions and a falsifier or a precise remaining discriminator.
No additional documentation-only increment is needed before that experiment.
The next succession experiment remains competing successors followed by another advance on one branch, where unequal counters can conceal disagreement.
The contract's predecessor field is explicitly a modeling hypothesis, not an accepted representation.

This distillation changes branch documentation only and accepts no protocol design.
Model success would still leave NoteToSelf publication, adoption and evidence retention to validate in the runtime.
Validation is documentation diff review and `git diff --check`; no model or probe was run.

## Move 2 model result, 2026-09-06: both candidates work; reservation costs a recipient rule

The executable model is [models/model_sibling_repair.py](models/model_sibling_repair.py).
It restates the contract's objects in its own terms and runs no runtime code, so today's tables, selectors and helper boundaries do not constrain it.
It varies three things independently: the identity scheme (independently minted attestation IDs, or one ID reserved at selection time and shared by all signers),
the delivery order of the two attestations, and the recipient's retention key (attestation identity alone, or identity plus signer and payload).

Command:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

26 cases pass on two consecutive runs against `f8d65a6`, with no runtime code changed.

Schedule 1, interrupted signing.
A finalizes the evidence for one selection and is interrupted before signing; B adopts the whole record and attests; A resumes and attests.
Under both schemes, both delivery orders and both retention keys, the recipient sees one selection, no contradiction, and routes to the frozen content.
B's selection and allocation counters stay at zero, so repair creates neither a successor nor another allocation, and B needs nothing from A beyond the adopted record.
The model also asserts the negative side of the contract: a device that has not adopted the evidence raises rather than attesting.

Schedule 2, contradictory claims under one selection ID.
B attests to a changed route field, or to a changed predecessor, while claiming the same selection.
With independently minted identities the contradiction stays visible in both delivery orders under both retention keys, and the recipient routes nowhere.
With a reserved identity it stays visible only when the recipient keys retention by identity *and* payload.
Keyed by identity alone the second delivery displaces the first, one payload survives, and which claim survives is decided by delivery order.
That is the model's one disconfirming case, and it is asserted explicitly rather than left as an absence.

This does not show that reservation cannot work.
It locates the cost precisely: the reserved candidate's correctness depends on a recipient rule that an identity-keyed store does not supply by default, and that rule has to be stated and enforced everywhere attestations are stored, merged or restored.
Independent identities need no such rule, because two signing acts are already two rows.
Both candidates still need payload comparison to tell equivalent duplicates from contradictions; that requirement is not a discriminator.

The model's assumptions are stated in its docstring and are obligations, not findings:
evidence is adopted as a unit, adoption is distinguishable from reachability, and predecessor evidence survives a selection change.
Its succession handling is deliberately minimal — it orders selections by which one no other retained selection names as predecessor — and it exercises only two devices and one berth.
Competing successors and unequal counters are Move 3, not modeled here.

The contract needed one correction, applied in this increment: its reserved-identity paragraph allowed a recipient to retain multiple payloads under one identity but did not say that failing to is a live failure mode with an ordering-dependent outcome.
