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
With fresh independently minted identities, two signing acts are already two rows.
The subsequent reused-ID control below narrows this finding: separate identities do not supply a general guarantee against evidence loss.
Both candidates still need payload comparison to tell equivalent duplicates from contradictions; that requirement is not a discriminator.

The model's assumptions are stated in its docstring and are obligations, not findings:
evidence is adopted as a unit, adoption is distinguishable from reachability, and predecessor evidence survives a selection change.
Its succession handling is deliberately minimal — it orders selections by which one no other retained selection names as predecessor — and it exercises only two devices and one berth.
Competing successors and unequal counters are Move 3, not modeled here.

The contract needed one correction, applied in this increment: its reserved-identity paragraph allowed a recipient to retain multiple payloads under one identity but did not say that failing to is a live failure mode with an ordering-dependent outcome.

## Post-Move 2 review, 2026-09-06: narrow retention claim and test succession next

The review reran the 26 model cases against `e6c1fb1`; all passed in 0.02 seconds using the command above.
Separate attestation identities remain the choice because they represent distinct signing acts directly and avoid sharing an identity during ordinary sibling repair.
The stronger claim that they need no special retention rule assumes fresh IDs, however, and does not cover deliberate reuse by a signer.

An ad hoc model control retained the same signer, selection and attestation ID while changing the route payload:

```sh
.venv/bin/python - <<'PY'
import dataclasses
import runpy
m = runpy.run_path('.IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py')
a, b, evidence = m['_interrupted_signing'](m['SeparateIdentities'])
first = a.attest(evidence.selection_id)
changed = dataclasses.replace(first, route=dataclasses.replace(first.route, location='contradictory-location'))
for order in ((first, changed), (changed, first)):
    recipient = m['Recipient'](m['IDENTITY_KEYED'])
    for att in order:
        recipient.deliver(att)
    view = recipient.view()
    print({'retained': len(recipient.retained), 'contradiction_detected': bool(view['contradictions']), 'routed_location': view['route'].location})
PY
```

Both orders retained one payload and reported no contradiction.
Delivering the changed payload last routed to `contradictory-location`; delivering the original last routed to `berth-core-x`.
This exercises the model's storage behavior, not actual signatures, cryptographic validation or runtime ingestion.
It establishes that minting separate IDs does not itself prevent overwrite when an ID is reused; it does not establish which runtime identity or retention design should handle that case.
The control has not been added to the executable model file.
Preserve it there in the next executable increment and keep conflicting-payload detection as an explicit obligation.
The ledger and contract now state the narrower claim.

The next main experiment remains succession under competing branches, with public scalar counters and private retained selection history as the preferred candidate.
Counter-only behavior supplies the comparison: a higher value may guide provisional routing but cannot establish that its author observed and replaced another branch.
Separate teammate and sibling evidence in the model rather than inheriting Move 2's public predecessor access.
This lets the experiment examine private retrospective checking without silently reversing the recorded rejection of a peer-verifiable selection DAG.
The plan specifies the P → X₁ → X₂ versus P → Y₁ schedule, initially withheld X₁, and a human choosing Y's location after observing both branches.
The experiment must identify the observer and evidence needed for detection, the response after discovery, and whether a fresh human selection survives delayed delivery and repeated attestation.
It need not establish eventual agreement.

The next deliverable is a bounded executable model with a counterexample, supported conclusions, and either a succession decision with a falsifier or a precise remaining discriminator.
Publication, adoption and evidence retention remain later runtime obligations once the candidate defines the guarantees needed.
This distillation edits branch documentation only; it accepts no succession representation and changes no model, runtime code or permanent spec.
Validation is documentation diff review and `git diff --check`; the model rerun and ad hoc control above occurred during the preceding analysis, not as new experiments in this documentation edit.

## Move 3 model result, 2026-09-06: the counter orders, private history detects

The executable model is [models/model_succession_branches.py](models/model_succession_branches.py).
Like the Move 2 model it restates the problem in its own terms, runs no runtime code and inherits no table boundaries.
It varies the public succession representation (a scalar counter alone, or a counter plus the predecessor selection ID) and the delivery order of every attestation in the schedule.
Teammates and siblings are separate classes: a `Teammate` sees only delivered attestations, and only a `Device` holds private selection history or adopts a sibling's.
No teammate is given private history anywhere in the model, so the experiment does not quietly reverse [#224's rejection](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) of a peer-verifiable selection DAG.

Command:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

60 cases pass on two consecutive runs against `815a4d4` with no runtime code changed: 32 succession cases and the 28 Move 2 cases, which now include the preserved reused-ID control.

The schedule is the plan's.
A and B adopt P, disconnect, and select X₁ and Y₁ from it; A advances to X₂ without observing Y₁, so the counters are X₂ 3, X₁ 2, Y₁ 2.

Step 3, X₂ and Y₁ delivered in both orders without X₁.
Both representations route to X₂ on its higher counter, and under both the teammate can conclude nothing about replacement: `observed_replacement("sel-x2", "sel-y1")` is false because no retained evidence connects the two.
The equal-counter check finds nothing, because this schedule produces 3 against 2.
That is the counterexample the move was for: the arithmetic that decides routing is silent about agreement, and the conflict check that would speak is not triggered.
The predecessor field adds exactly one thing here — the teammate sees that X₂'s chain to P has a missing link while Y₁'s is complete, so X₂ is higher but unverified rather than simply higher.

Step 4, detection.
Delivering X₁ makes the equal-counter check fire under both representations, but only the predecessor representation locates the branch point, reporting X₁ and Y₁ as competing children of P; the counter-only teammate has two selections at counter 2 and no way to relate them.
Under both, `observed_replacement` stays false, which is correct: nobody observed anything.
On the sibling side, B detects the fork in private history only after adopting A's history, and a control asserts that adopting the tip is not enough — a device holding P, Y₁ and X₂ but not X₁ finds no fork.
Detection therefore rests on retaining superseded selections, not on receiving the current one.

Step 5, human resolution.
After discovery a human on B reselects Y's location as Z, with counter 4 and X₂ as predecessor.
Across all six delivery orders of Z, a delayed X₂ and a repeated attestation of X₂ by the other sibling, and under both representations, the teammate routes to Y's location.
Repeated attestation restates a selection's counter rather than advancing it, which is the Move 1 ledger entry holding in a succession setting.
B's private history still shows the fork after resolution, so the disagreement is preserved rather than erased by the choice.

The Move 2 model gained the reused-ID control as `test_reused_identity_loses_a_contradiction`, as the previous round required.
It asserts what the ad hoc run showed: one payload survives, no contradiction is reported, and delivery order picks the survivor, while payload-keyed retention keeps both.

Limits.
Two devices, one teammate, one berth, one fork and no signatures, trust revocation or reachability.
Counters are assigned from each device's own greatest observed value, which the model asserts rather than establishes.
The model says nothing about how selection history is published, how much of it a sibling retains, or what it costs to keep superseded selections.
It also does not price the predecessor field's public exposure, which is the reason the representation question stays open below.

## Move 4 decision, 2026-09-06: the attestation carries an advisory predecessor link

The model is [models/model_public_payload.py](models/model_public_payload.py).
It reuses the Move 3 objects rather than restating them, because the question is about the same actors and the same schedule.
It compares three worlds at one teammate — a clean succession P → X₁ → X₂, the Move 3 fork with X₁ withheld, and a clean succession whose middle link is merely late — against three recipient policies: assume everything below the greatest counter is superseded, verify supersession along the predecessor chain, or refuse to route until that chain is complete.

Command:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_public_payload.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

71 cases pass on two consecutive runs against `1aa45c4`, 11 of them new, with no runtime code changed.

The decisive result is an indistinguishability.
Excluding opaque identifiers and route content, a counter-only teammate's view of the clean world and of the forked world is byte-for-byte the same: counters 1, 2, 3 and nothing that relates them.
One of those worlds is safe and the other conceals a competing branch, and no recipient policy can separate them, because the evidence does not differ.
With the predecessor link the two views differ, and the difference is exactly the one that matters: X₂'s chain to P is complete in the clean world and has a gap in the forked one.
The policies show what that buys.
Assume-superseded routes to X₂ and reports no unresolved rival in both worlds, which is the confident answer a counter-only payload forces.
Verify-chain is quiet in the clean world and, in the forked world, still routes to X₂ while reporting Y₁ as unresolved.
Under a counter-only payload the verifying policy degenerates: every other selection is unresolved in every world, so it reports nothing a recipient can act on.

The link must be advisory.
The `delayed` world is a correct, uncontested succession whose middle link has not arrived, and a recipient that demands a complete chain refuses to route there while the verifying recipient routes normally.
A gap is evidence that the recipient's own evidence is incomplete, never evidence of a conflict, and delivery the recipient does not control decides when the gap closes.

Decision.
The public attestation carries the selection ID, the succession counter, the predecessor selection ID and the frozen route content.
A teammate routes to the greatest counter it holds, provisionally and without waiting for any chain; it marks a selection whose chain is incomplete as unverified, reports the selections it cannot show were superseded, and resolves nothing by attestation identity, signer or time.

This is a narrow, deliberate change to [#224's rejection](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) of a peer-verifiable selection DAG, and it needs the issue owner's agreement rather than being read as compatible.
What #224 rejected is peers verifying a participant's selection history as a condition of accepting a route, and that stays rejected: no recipient may condition routing on chain completeness, and the model's `REQUIRE_CHAIN` case is the reason.
What is added is one opaque name per attestation.
The model asserts what that discloses: a link names a selection without disclosing where it pointed, and the teammate's known route content is exactly what was delivered to it.
The counter already publishes how many times a participant has reselected; the link adds which selection each one claims to follow, and nothing else.
Nothing obliges a participant to publish superseded selections, so peers cannot reconstruct the history — they can only sometimes notice that they do not have it.

Retention.
Detection rests entirely on retaining superseded selections, per the Move 3 control where a device holding P, Y₁ and X₂ but not X₁ finds no fork.
A device therefore retains its own private selection records per berth without pruning at this stage: the records are small and route changes are human-rare.
Falsifier for the retention rule: a workload where per-berth selection history grows without a human-scale bound.

Assumptions and limits.
Selection IDs are opaque and carry no route content; a teammate learns route content only from attestations delivered to it; everything Move 3 assumes about counters and private history still applies.
The model prices exposure only as disclosure of route content, not as traffic analysis or correlation across berths and teams.
It exercises one teammate, one berth and one fork, models no signatures or trust changes, and says nothing about how attestations are published or merged.

## Post-Move 4 review, 2026-09-06: keep the ordering result, narrow the payload decision

The review of `9b6695e` reran the three models with the command in the Move 4 entry above: 71 cases passed in 0.04 seconds.
Move 3 earns its main conclusion: counters establish provisional routing priority, retained intermediate selections reveal competing branches, and reattestation preserves succession.
These remain bounded model results, not runtime guarantees.

Move 4's advisory link remains a reasonable preference for diagnosis, but its comparison does not earn the claim that counter-only recipients must assume supersession.
A teammate can route provisionally, make no supersession claim, and leave retrospective checking to siblings holding private history.
That is the alternative already under discussion, and the model does not evaluate it fairly.
The link adds information; whether teammates need that information enough to justify revising #224 is a separate argument.
The earlier "Move 4 decision" entry records the original conclusion; this review supersedes its claim that the public payload choice is closed.
The attempt to retrieve the original #224 comment failed through both `gh api` and the web tool, so this review relies on the branch's recorded account of that decision rather than claiming a fresh verification.

Two additional controls were run against the existing model objects:

```sh
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, '.IN_PROGRESS/issue-238-shared-berth-changes/models')
from model_succession_branches import *
from model_succession_branches import _diverged
from model_public_payload import report, VERIFY_CHAIN

a,b,x1,y1,x2 = _diverged(COUNTER_AND_PREDECESSOR)
b.adopt_history(a)
b.select('sel-z', ROUTE_Y1, predecessor_id='sel-x2')
t = Teammate(COUNTER_AND_PREDECESSOR)
for sid in ('sel-p','sel-x1','sel-x2','sel-y1','sel-z'):
    t.deliver(b.attest(sid))
print('After human resolution with every record delivered:', report(t, VERIFY_CHAIN))

import dataclasses
first = b.attest('sel-z')
changed = dataclasses.replace(first, attestation_id='different-signing-act', counter=99, route=ROUTE_X1)
for order in ((first, changed), (changed, first)):
    recipient = Teammate(COUNTER_AND_PREDECESSOR)
    for att in order:
        recipient.deliver(att)
    print('Contradictory same-selection attestations:', {'retained_attestations':len(recipient.attestations), 'projected_selections':len(recipient.selections), 'route':recipient.route()})
PY
```

The human-resolution control routes to `berth-y1` with `chain_complete=True`, but still reports `unresolved_rivals={'sel-y1'}` after every record arrives.
Z names X₂ as its single predecessor, so no chain from Z reaches Y₁ even though the human chose Y's location after observing both branches.
That does not invalidate the routing result or require erasing the fork.
It requires distinguishing historical divergence, missing evidence and disagreement still requiring action, with claims limited to what each observer can establish.
The public recipient does not automatically inherit the human's private evidence of resolution.

The contradiction control retains two distinct attestation IDs but projects them into one selection.
Delivering the changed attestation last routes to `berth-x1`; reversing delivery routes to `berth-y1`.
The changed claim differs in both counter and route under the same selection ID.
`Teammate.selections` silently collapses those claims, so the newer model does not carry forward Move 2's contradiction behavior.
This is a model composition gap, not a demonstrated defect in runtime ingestion or cryptographic validation.
Neither ad hoc control has been preserved in the model files yet.

Next increment.
Make one bounded executable correction: compare against honest counter-only provisional routing, preserve both controls, carry contradictory-claim handling into the newer model and clarify the post-resolution report.
Advisory links remain preferred, but the recommendation must defend teammate diagnostics and the change to the recorded #224 decision rather than treating model success as sufficient approval.
Then take uncertain publication first in Move 5: publication succeeds, acknowledgment is lost, a sibling adopts and replaces the selection, and the original device retries.
Compare definite refusal and already-superseded outcomes, stating what each device may use, attest to and report from its actual evidence.
Follow with merge and restore schedules that challenge silent promotion of refused or obsolete state and loss of the highest observed counter, keeping deliberate human reselection distinct from retry.
Before runtime implementation, settle exact content binding across provider finalization and account changes, then probe the real publication and adoption boundaries.

This distillation changes branch documentation only; it accepts no new protocol decision and changes no model, runtime code or permanent spec.
The model rerun and ad hoc controls above occurred during the preceding review, not as new experiments in this documentation edit.
Validation of the distillation: documentation diff review and `git diff --check`, which reported no whitespace errors.

## Move 4 correction, 2026-09-06: the fair comparison, and two controls made explicit

The bounded correction the post-Move 4 review asked for is now in the models.
81 cases pass in 0.06 seconds:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_public_payload.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

The honest counter-only policy.
`PROVISIONAL_ONLY` routes to the greatest counter held and asserts nothing about supersession, leaving retrospective checking to a sibling with private history.
It is wrong in no world, and it routes identically to `VERIFY_CHAIN` in all three.
So the advisory link is not defended as routing correctness, and the earlier claim that a counter-only recipient must assume supersession is retired: `ASSUME_SUPERSEDED` is now described as a policy a counter-only payload permits rather than one it forces.
What the link buys is diagnosis — naming which rival is unaccounted for and why — and that benefit still has to be argued against the cost of revising #224.

The report now classifies rivals instead of flagging them.
`missing_evidence` is a rival whose relation cannot be established because a link is not held.
`historical_divergence` is a rival whose branch point and branches are all held and which is strictly older than the routed selection.
`open_disagreement` is a rival that is not older and still competes.
Only the first and third are unresolved.

The post-resolution control is preserved as `test_resolution_leaves_a_divergence_of_record_not_an_open_rival`.
With every record delivered after the human reselects at Z, the teammate routes to `berth-y1` with a complete chain and reports Y₁ as historical divergence, not unfinished business.
That is the reporting fix the review asked for: retaining a superseded branch does not require presenting it permanently as an open item.
A second control, `test_the_resolution_itself_is_not_public_evidence`, states whose evidence supports what.
A device that never adopted Y₁ produces an identical claim for Z, so the public record cannot distinguish deliberate resolution from accidental, and the teammate must not report the human's choice as established.
Only B's private history holds that evidence.

The contradiction control is preserved in both delivery orders as `test_contradictory_attestations_for_one_selection_are_not_collapsed`.
Move 2's behavior is carried into the newer model: `Attestation.claim` excludes attestation identity and signer, `Teammate.claims` groups retained attestations by selection, `Teammate.contradictions` reports selections whose claims disagree, `Teammate.selections` omits a contradicted selection rather than picking one by delivery order, and `Teammate.route` refuses when a contradicted selection holds the greatest counter.
A fresh signing identity over the same selection ID with a different counter and route no longer lets delivery order choose the route.

Limits unchanged.
These remain bounded model results about one teammate, one berth and one fork, with no signatures, no trust changes and no account of how attestations are published or merged.
Metadata exposure is still unpriced: the model prices disclosure of route content only, not traffic analysis or correlation across berths and teams.
The correction closes the composition and reporting gaps the review found; it does not by itself justify changing the recorded #224 decision, which still needs the issue owner's agreement and the diagnostic argument.

## Move 5 result, 2026-09-06: uncertain publication, retry, merge and restore

The model is [models/model_publication_outcomes.py](models/model_publication_outcomes.py).
15 cases; 96 across all four models, in 0.06 seconds:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_publication_outcomes.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_public_payload.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

### The discriminator is observed conflict, not degree of knowledge

The four publication outcomes do not sit on a scale from certain to uncertain.
An unknown outcome carries no information about a competing selection, so it is ordinary incomplete evidence, which the ledger already permits a device to act on provisionally.
A definite refusal and an already-superseded result are observations of a conflict, which the ledger already requires a device to surface rather than proceed past.
That is why unknown is the only one of the three that permits the device to keep using and attesting to its own selection.
It also bounds the reports: publication outcome constrains what a device may claim about *publication*, not what it routes to.
A device that adopts a greater selection after an unknown outcome routes to that selection and still cannot say whether its own was published.

### The device never has to learn the outcome

`test_the_unknown_outcome_needs_no_ground_truth` runs the lost acknowledgment with the write landing and with it not landing.
The device's standing is identical, and republishing the same record converges both worlds.
The ground-truth flag is recorded in the attempt and read by no device method.

### Retry must republish a record, never make a selection

The Move 5 schedule — publication lands, acknowledgment lost, sibling adopts and deliberately replaces the selection, original device retries — separates the two retry policies cleanly.
Republishing the identical record is idempotent and leaves B's replacement on top.
Making a fresh selection for the same intent takes a counter above everything the device has observed, so after A adopts Y the retry outranks Y and resurrects the route B chose against.
That is the Move 1 delayed-signing failure in another costume, reached without any delay in signing.
Retrying before A learns of Y is not better in kind, only in visibility: the fresh selection lands on B's counter and produces a tie nobody chose.

`test_deliberate_reselection_is_mechanically_a_retry_and_must_not_be_one` is the sharp version.
A human on A who has adopted Y and deliberately chooses X's location again produces exactly the record the bad retry policy produced: same route, same counter, outranking Y.
The protocol cannot distinguish them after the fact, and should not try.
The rule belongs at the retry call site — a retry replays bytes — rather than in a later check.

### Merge needs no new rule; the two dangerous boundaries are elsewhere

Merging published stores is union, and the counters and predecessor links the ledger already has decide the result in either merge order.
Restoring an old snapshot of the store revives nothing, because an old record carries its old counter.
So the answer to Move 5's second question is that ordering suffices for the records.

Two things it does not cover, both of which are about devices rather than records.

A refused candidate is in no store, so no merge of stores can promote it — but it is in the device's private history.
A restore that treats private history as publishable puts a definitely refused selection on top.
The rule is that the private-to-published boundary is crossed only by an explicit publication, never by a restore.

A restore that *replaces* a device's state discards the counters it had observed.
A human choosing a route on that device then lands below the selection it was chosen over and is refused as superseded, so the deliberate choice becomes unpublishable and no retry can fix it.
Keeping the greatest observed counter as a monotone high-water mark, and treating restore as union with local state, is the one rule that does not follow from record ordering, because it constrains the device rather than the records.

### Limits

One berth, one participant with two devices, no teammate delivery and no signatures.
The store is a set merged by union; nothing here tests a real git merge, conflicting concurrent writes to the same object, or the Hub's actual publication interface.
Provider effects are assumed already done, so the model says nothing about a refusal that arrives with storage half-materialized, and nothing about route-content binding across provider finalization or account changes.
`REFUSED` is modeled as a definite "nothing was written"; a runtime refusal that is itself unreliable would fold back into `UNKNOWN`, which the model handles but does not exercise from that direction.
The high-water mark is modeled as a per-device integer; where it durably lives, and what happens when it is lost rather than replaced, is not modeled.

## Review of `769c03f` and `010dd4d`, 2026-09-06

The honest counter-only comparison and the identical-record retry discriminator are useful results.
The stronger claims that the reporting gaps are closed and Move 5 is complete are premature.
These findings concern the research models and their conclusions, not newly demonstrated runtime regressions.

The four-model command recorded above was rerun: 96 cases passed in 0.05 seconds.
Additional local controls were run with `.venv/bin/python -` from the repository root, adding `.IN_PROGRESS/issue-238-shared-berth-changes/models` to `sys.path` and importing the existing model objects.
No provider or network calls were made.
The observed counterexamples were:

1. Start with `_lost_acknowledgment()`, run `_sibling_replaces(store)`, then `a.retry(store, RETRY_SAME_RECORD)`.
   X is still in `store.published`, but `standing(a)["publication_claim"]` is `"not published"`.
   A refusal of this attempt does not establish that an earlier attempt never published the record.
   Publication reports must distinguish an attempt's effect, historical inclusion and current routing priority.
2. Starting with published P, have A select X and receive `REJECTED`, then select Y and receive `REJECTED`.
   Only P is published, but `standing(a)` routes to X and returns `may_attest="x"`.
   The fallback examines every other private selection and forgets earlier refusals.
   Eligibility needs to survive across attempts; private history alone is insufficient evidence for fallback.
3. Let the store hold a selection at counter 9 while A knows only P at counter 1, then publish A's counter-2 X.
   The superseded reply names counter 9, and `standing(a)` permits attesting to it, but A's history still holds only P and X and its high-water mark remains 2.
   A deliberate next selection gets counter 3.
   Receiving the reply must either adopt the evidence and advance the counter or explicitly require adoption before further selection and attestation.
4. Deliver P and distinct X/Y successors at counter 2 to a teammate, in both X/Y delivery orders.
   `report(teammate, VERIFY_CHAIN)` returns no route, empty `open_disagreement`, empty `unresolved_rivals` and empty `contradicted`.
   The early return on no route drops the tie's evidence.
   In fact, `open_disagreement` is unreachable: a routable unique maximum makes every other counter smaller, while a tied maximum returns before classification.
5. Deliver all four records P, X1, X2 and Y1 from `_diverged(COUNTER_AND_PREDECESSOR)`, without any human resolution at Z.
   The report calls Y1 `historical_divergence` and reports no unresolved rivals.
   The original unequal-counter disagreement becomes a matter of record merely because its missing intermediate link arrived.
   A complete chain does not establish that the author of X2 observed Y1 or that anyone resolved their disagreement.
   Public diagnosis should distinguish a known branch relationship from evidence of resolution; the existing indistinguishable-worlds control already limits the latter claim.

Recommended sequence: preserve these controls as micro tests and correct or narrow the affected ledger claims, then take Move 6's route-content binding question.
The public-link choice still needs an explicit judgment about diagnostic value and metadata exposure.
Once the candidate states its guarantees, probe actual publication, adoption and provider-finalization boundaries with local services before preparing a runtime implementation handoff.
No model or runtime behavior was changed during this review.

## Move 5 correction, 2026-09-06: five controls preserved, two reporting claims narrowed

The five counterexamples from the review above are now controls in the models.
102 cases pass in 0.07 seconds:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_publication_outcomes.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_public_payload.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

Each control was checked against the code it was found in.
Copies of the four model files were made in a scratch directory, the five fixes were reverted there one group at a time, and the reverted copies were run with the same interpreter.
The three publication controls failed and the other 15 cases passed; the two payload controls failed in both delivery orders and the other 21 passed.
So none of the five is an assertion that would have held anyway.
No provider or network calls were made, and no runtime code was touched.

### An outcome describes an attempt; a claim describes a record

`standing` reported the last attempt's outcome as the record's publication status.
After the lost acknowledgment landed and the queued retry was refused as superseded, the device claimed "not published" about a record sitting in the store.
`publication_claim` is now taken over every attempt on a selection and never weakens: an acknowledged attempt makes it "published", an unacknowledged one leaves it "unknown" however many later attempts are refused.
The report now carries three separate answers — `attempt_effect` for what this attempt did, `publication_claim` for the record, `routes_to` for current priority — because they were three questions sharing one field.

### Refusals outlive the attempt that observed them

The fallback took the greatest selection in private history other than the one just attempted, so two refusals in a row promoted the first refusal.
Private history records what a device chose, not what the store accepted, so eligibility needs its own evidence: the device now keeps the set of selections it has seen refused and excludes them as fallback candidates.

### A superseding refusal is adopted, not quoted

A refusal naming a selection at counter 9 left the device's history and high-water mark at 2 while `standing` offered that selection as the one it may attest to.
A deliberate next selection then took counter 3.
Receiving such a refusal now adopts the named selection, so attesting to it means holding it and the next deliberate choice takes counter 10 — above what refused it.

### A tie is evidence, and the report was dropping it

`report` returned empty sets whenever there was no route, so a teammate holding two distinct successors at the same counter reported no route, no disagreement and no rivals.
The same early return made `open_disagreement` unreachable by construction: a unique leader makes every rival strictly older, and a tie returned before classification.
Classification is now relative to the leading selections rather than to a route, a rival is superseded only when *every* leader's chain reaches it, and tied leaders are rivals of each other.
The tie control asserts both delivery orders: no route, `open_disagreement` of both branches, and P still claimed superseded because both leaders build on it.

### Locating a branch is not resolving it

`historical_divergence` described a fully linked older rival as "a matter of record, not an open action item", and `unresolved_rivals` excluded it.
Delivering a missing intermediate link is not a resolution: the new control runs the Move 3 divergence with every record delivered and no human choice anywhere, and it produces exactly the classification the resolved world produces.
The categories are renamed to say only what they establish — `located_divergence` for a branch whose parting point is visible and which lost routing priority, `unaccounted_rivals` for what the observer cannot place or that still competes — and the word "resolved" no longer appears in any report key.
This composes with the existing indistinguishable-worlds control rather than replacing it: that one shows the public claim for Z is identical whether or not Y₁ was ever adopted, and this one shows the same for the rival's classification.

### What this does and does not change

The Move 5 conclusions stand: the four outcomes are still not degrees of certainty, retry still must replay a record, and merge and restore still need nothing beyond ordering plus the two device-level rules.
Move 4's routing result stands: `PROVISIONAL_ONLY` and `VERIFY_CHAIN` still route identically in all three worlds.
What changed is what a device and a teammate may *say*, which is where both reviews found the defects.
That is now twice that this reporting surface has been corrected by review rather than by its own cases, which is a reason to treat the next reporting claim as unproven until a control exercises it, not a reason to expand the models further.

### Limits

Unchanged from Move 4 and Move 5, and none of the five corrections touches them.
Still one berth, one participant with two devices, one teammate, one fork, no signatures, no trust changes, and a store that is a set merged by union.
The refused set and the high-water mark are per-device in-memory values; where they durably live, and what a device may claim after losing them, is not modeled.
Adoption of a superseding refusal assumes the refusal names an authentic record, which nothing here checks.

## Move 6 result, 2026-09-06: route content is frozen into the selection record

The model is [models/model_route_binding.py](models/model_route_binding.py).
15 cases; 117 across all five models, in 0.07 seconds:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_route_binding.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_publication_outcomes.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_public_payload.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_sibling_repair.py -q
```

The three bindings differ in exactly one thing: what a signer reads.
`FROZEN_PROJECTION` reads the record.
`REFERENCE_BINDING` reads whatever the mutable rows say at signing time.
`CONTENT_DIGEST` reads content the device holds and refuses unless its digest matches the record.
No binding is defined in terms of a table, so nothing here rests on where the values live today.

### The account change falsifies reference binding

A signs the selection while the account row says `us-west`; the row moves to `eu-central`; B repairs the same selection.
Under reference binding B signs `eu-central`, so the repair contradicts the selection it was repairing.
Both signatures are authentic and neither device did anything wrong, so no recipient rule can assign fault, and the teammate ends with a contradicted selection and no route at all.
Under the other two bindings the repair restates the claim field for field, which is the equivalent duplicate Move 2 already showed is harmless.

Merge makes this worse rather than better.
Composing A's record set with a peer's account row is a union of two individually valid states, and the derived route is a third thing that neither device ever chose or signed.
That is the evidence-table observation — whole-row merge cannot prevent the schedule when relevant values sit on independently changing rows — as an executable case.
A frozen record is closed under the same merge because it reads nothing.

### The digest is a more expensive frozen projection, under this branch's assumptions

`CONTENT_DIGEST` gives the same correctness as freezing: a sibling cannot sign substituted content, because substituted content does not match the digest.
What it does not give is availability.
The record alone does not carry the content, so a sibling holding the published record and nothing else cannot attest at all, and repair waits for a second delivery on a path the model does not supply.
Since the content has to reach siblings for repair to be possible, and the frozen record is that delivery, the digest costs a delivery path and buys nothing over freezing.
This conclusion is conditional: it assumes route content may be published to siblings in the clear.
If it may not, the digest's separation of naming from content is exactly what earns its cost, and that argument has not been made here.

### Finalization is an ordering rule, not a representation

A record minted before the provider settles the locator fixes the requested value, and the provider then materializes a different one.
Freezing and digesting both preserve a value that was never true, and the attested route names storage that does not exist.
Reference binding is the only one that tracks the correction — and only because it is willing to change what a selection means later, which is the property the account schedule just ruled out.
So the two cannot be had at once, and the cheaper side is a rule about when a record may be created: a selection is not minted until every field it freezes is final.
No choice of representation substitutes for that rule.

### Repair from the published record alone

With B's allocation row stale and no content delivered, the frozen record is sufficient: B's repair matches A's claim field for field and names storage that exists.
Reference binding produces a route pointing at the requested locator, which was never materialized.
The digest produces nothing, which is correct and unavailable.
This is the ledger's sibling-repair property meeting question 3: freezing is what makes the published record self-sufficient.
The model states what each binding lets B sign; whether a blocked sibling would then materialize again is runtime policy it does not decide.

### Binding runs one way

X at counter 2, Y at 3, then a deliberate return to X's exact route at 4, with Y delivered last.
The first and third records are field-for-field identical in route content and are different selections.
All three are retained, the return routes on its counter, and nothing is folded into anything else.
A selection fixes content; content never identifies a selection, so endpoint or route equality is not a deduplication key.

### Limits

One participant with two devices, one allocation, one account field and one teammate.
The provider is a set of materialized locators: no partial materialization, no failure during finalization, no deletion, and no second provider.
Nothing here models a refusal arriving with storage half-materialized, which Move 5 also left open and which the finalize-before-mint rule now makes the obvious next boundary.
Account state is one scalar; a real account has several fields with different change rates, and the model does not say which of them a route depends on.
Signatures are assumed authentic and trust is assumed unchanged, so this says nothing about a signer that is no longer trusted.
Most importantly, every guarantee above is a claim about a model, and the finalize-before-mint rule in particular is a claim about a real provider's behavior that no probe here has checked.

### Status review, 2026-09-06

Reran the five-model command recorded above: 117 micro tests passed in 0.06 seconds, including Move 6's 15 cases.
Move 6 is complete at the model level: freeze complete route content, and finalize the provider locator before minting the selection.
The remaining evidence gap is actual interrupted finalization, publication and sibling adoption; this review ran no runtime probe.
The next-step sequence is recorded in [plan.md](plan.md#move-6-complete-for-the-model-the-probe-is-the-next-move), starting with interrupted provider finalization against local services.
Passing models alone do not justify an implementation handoff or closing #238.
