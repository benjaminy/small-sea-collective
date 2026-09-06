# Research evidence: shared berth allocation and routes

Condensed during the review of `2f5ff00` on 2026-09-06.
[plan.md](plan.md) owns the current decisions, open questions and next work.
These notes retain results, counterexamples, corrections and limits rather than completed agendas and repeated handoffs.
The full earlier chronology and original ad hoc commands remain in Git at `2f5ff00` (`git show 2f5ff00:.IN_PROGRESS/issue-238-shared-berth-changes/notes.md`).
All results below are research evidence; no runtime implementation or complete protocol was accepted.

## Repository observations

- `wrasse_trust/transport.py::select_effective_teammate_berth_storage` is shared by Manager and Hub.
  Row loading is duplicated; no duplicated selection-policy defect was demonstrated.
  `announcement_id` is the primary key and sole ordering key, minted by `_uuid7_after` immediately before signing.
- Canonical announcement bytes include `announced_at` and `signer_key_id`.
  No timestamp display or routing use was found, but verification, import validation, same-ID comparison, publication results and courier sidecars consume it.
  The earlier claim that nothing reads it was retracted; retaining a timestamp needs a real purpose rather than a collision argument.
- Splice Merge compares whole rows by primary key and exposes allocation index/update conflicts.
  That does not bind independently changing account and allocation rows into one immutable route.
  Current allocation replacement deletes the old row, so it retains no predecessor selection history.
- Cod Sync's `already_present` includes a stored descendant of the attempted head; Manager's `push_note_to_self` discards `PublishResult`, including attempted and observed heads.
  Historical inclusion and current selection are different facts.
- `_publish_core_route` signs and commits without publishing NoteToSelf, and its final allocation mismatch check follows those effects.
  Creator setup and the public announcement wrapper have other direct signing paths.
  Transport-only deduplication can return another sibling's announcement, while invitation delivery requires the current signer.
- `Archive/design-record-core-route-reconciliation.md` supports generation checks and serial ordering, not a distributed ordering proof.
  `Archive/design-record-issue-48-note-to-self-self-integration.md` explains applying deltas onto live state instead of replacing the database, not a guarantee for a future design.
  The separate Hub spec corrections are recorded in [doc-fixes.md](doc-fixes.md).

## Move 1 result and recipient control: delayed signing reverses observed replacement

Source: [probes/probe_delayed_signing.py](probes/probe_delayed_signing.py), introduced in `98c28ae`; recipient control in `e7e811c`.
Two installations of one identity reach trusted-device status through the real linked-device team join and share a Core berth and predecessor announcement.
A materializes X, then publication is patched to raise before signing, taking the operation's retryable failure path.
A pushes NoteToSelf; B refreshes and adopts X field for field, deliberately replaces it with Y, signs Y and pushes.
A never refreshes and later signs X.
Authentic signed rows are inserted into two recipient database copies in both orders and resolved through the real shared selector.
Y alone selects Y; after both arrive X wins in both orders.
That control rules out Y failing recipient validation as the explanation.

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_delayed_signing.py -x -q
```

The original probe passed on three consecutive runs against `fcac0a2`; the added control passed on two runs against `11c87ba`.
In a recorded run, Y was signed at 20:04:19.997295Z and X at 20:04:20.888088Z.
A's ordinary resume-time UUID already exceeded Y's; no injected clock, millisecond tie or lower-bound forcing was needed.
A signing timestamp would reverse the same relationship.
This rejects signing-time priority without choosing an attestation representation or rejecting every timestamp use.

Limits: one process and machine, sequential devices, no skewed-clock claim.
Provider materialization, NoteToSelf publication/adoption and signing are real; teammate delivery is direct insertion, not fetch/merge.
The passing probe asserts the defect and fixes nothing.

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
The reused-ID control narrows this finding: separate identities do not supply a general guarantee against evidence loss.
Both candidates still need payload comparison to tell equivalent duplicates from contradictions; that requirement is not a discriminator.

The model's assumptions are stated in its docstring and are obligations, not findings:
evidence is adopted as a unit, adoption is distinguishable from reachability, and predecessor evidence survives a selection change.
Its succession handling is deliberately minimal — it orders selections by which one no other retained selection names as predecessor — and it exercises only two devices and one berth.
Competing successors and unequal counters are Move 3, not modeled here.

The post-Move 2 review reused one signer and attestation ID while changing the route.
Identity-keyed retention kept one claim with no contradiction, and delivery order chose the routed location; payload-keyed retention kept both.
Move 3 preserved this as `test_reused_identity_loses_a_contradiction`, bringing the Move 2 model to 28 cases.
The choice of separate identities therefore concerns ordinary fresh-ID repair, not a general evidence-retention guarantee.

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

## Move 4 result and corrections: advisory links buy diagnosis

Source: [models/model_public_payload.py](models/model_public_payload.py), using Move 3's actors.
The initial 11 cases compared clean succession, a concealed fork and a merely delayed middle link under assume-superseded, verify-chain and require-chain policies.
A counter-only view can be identical across clean and forked worlds; a predecessor link reveals a missing chain segment.
Requiring the complete chain stalls uncontested routing on ordinary delay, so links must be advisory if included.

Review of `9b6695e` rejected the inference that counter-only recipients must assume supersession.
The corrected `PROVISIONAL_ONLY` policy routes by greatest counter, makes no supersession claim and leaves retrospective checking to siblings.
It routes identically to `VERIFY_CHAIN` in all three worlds.
The public-link decision was reopened: diagnostic value must justify metadata exposure and the change to #224, with the issue owner's agreement.
The model prices route-content disclosure only, not traffic analysis or cross-berth/team correlation.

Review also found that the model projected contradictory attestations into one selection, letting delivery order choose the route.
`test_contradictory_attestations_for_one_selection_are_not_collapsed` now preserves both orders: claims exclude signer/attestation identity, contradictions stay visible, and a contradicted greatest selection prevents routing.
The post-resolution controls establish that a complete chain does not prove a human resolved a competing branch: an author who never saw Y₁ can produce the same public Z claim as a deliberate resolution.
The first report correction called a known older branch `historical_divergence`; Move 5's review corrected that overclaim to `located_divergence`, as recorded below.

The initial three-model run passed 71 cases twice against `1aa45c4`; the first correction passed 81 cases in 0.06 seconds.
The final correction command and sensitivity checks are recorded with Move 5.
The reporting surface has twice needed review controls beyond its own cases; passing cases alone should not close a new reporting claim.
Limits: one teammate, berth and fork, no signatures or trust changes, and no runtime publication or merge.

## Move 5 result: publication outcomes, retry, merge and restore

Source: [models/model_publication_outcomes.py](models/model_publication_outcomes.py).
The initial run passed 15 new cases, 96 across four models, in 0.06 seconds.
An unknown publication outcome supplies incomplete evidence; a definite refusal or superseding result supplies observed conflict.
`test_the_unknown_outcome_needs_no_ground_truth` runs with the lost write landing and not landing: the device's standing and identical-record retry are the same in both worlds.
Publication claims are separate from routing priority after a device adopts greater evidence.

In the lost-acknowledgment schedule, B adopts and deliberately replaces A's selection before A retries.
Replaying the identical record preserves B's priority.
Retrying as a fresh selection after learning B's choice resurrects A's old route; doing so before learning it manufactures an equal-counter tie.
`test_deliberate_reselection_is_mechanically_a_retry_and_must_not_be_one` shows that deliberate human reselection creates the same record as the bad retry, so the distinction must be enforced at the retry call site.

Union-merging published records and restoring old published records preserve their counters.
Two device rules are additional: restore must not publish private history (which can contain refused candidates), and the greatest observed counter must only increase.
A restore that replaces device state can lose the counter and place a deliberate choice below the selection it was chosen over.
These are model obligations, not established Git/NoteToSelf properties.

## Move 5 correction, 2026-09-06: five controls preserved, two reporting claims narrowed

Review of `769c03f` and `010dd4d` found the five counterexamples below, now preserved as controls.
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

### Limits

One berth, two devices and one teammate; no signatures or trust changes, and the store is a set merged by union.
This does not exercise Git conflicts, Hub publication, partial provider effects or runtime content binding.
A refusal means this attempt wrote nothing; an unreliable refusal would need to be treated as unknown.
Refusal adoption assumes an authentic named record without validating it.
The refused set and greatest observed counter are in-memory device values; durable storage and loss of those values remain unmodeled.

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
Every guarantee above is a model claim; Move 7 below supplies the first bounded runtime check of provider completion.

## Move 7 result, 2026-09-06: interrupted provider finalization

The first probe the plan asked for ran against a local MinIO server.
Probe source is `probes/probe_interrupted_finalization.py` with the existing `probes/conftest.py`; command:

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_interrupted_finalization.py -q
```

Four cases passed on three consecutive runs against `0f1fab1` with no runtime code changed.
`probe_delayed_signing.py` was not touched, so its recorded witness still runs unchanged.

Schedule as staged.
Two installations of one identity reach trusted-device status through the real linked-device team join, exactly as in Move 1.
Interruption is staged by patching `SmallSeaS3Adapter.ensure_bucket_public` to raise a real `ClientError`, so the Hub takes its own `CloudMaterializationFailedExn` path rather than a stub's.
Three pause points were exercised, each through `reconcile_team_route(new_location=True)`:

- **P0, before storage exists.** The patched method raises before `create_bucket`.
- **P1, after storage exists.** The patched method creates the bucket and raises before `put_bucket_policy`.
- **P2, after finalization completes.** Materialization runs for real and `publish_teammate_berth_storage_announcement` raises, as in Move 1.

### The allocation retains no completion marker

The three pauses leave durable allocation rows that differ only in the generation ID and the location string.
Every other field — berth, account, protocol, URL, client ID, path metadata — is identical, and no field records whether the provider finished.
Observed values from one run:

| Pause | route | route_reason | bucket exists | anonymous GET |
| --- | --- | --- | --- | --- |
| P0 before storage exists | pending | `materialization_failed` | no | 403 |
| P1 after storage exists | pending | `materialization_failed` | yes | 403 |
| P2 before signing | pending | `route_preparation_error` | yes | 404 |

The route reason separates P2 from the other two and does not separate P0 from P1:
a failed bucket creation and a half-applied bucket policy are the same reason.
So the answer to the Move 6 ledger entry's falsifier is split.
Finalization is observable as a completed call at the moment a device makes it, which is what the ordering rule needs.
Neither the retained allocation nor the local route report records completion for a later reader; this does not mean no durable evidence survives, since the bucket and policy are provider state.
The only way to observe it for S3 is to ask the provider, and the runtime's only ask — `materialize()` — is the same call that performs the action.

### Partial materialization exists inside what the design treats as one step

`ensure_bucket_public` is two provider calls, `create_bucket` then `put_bucket_policy`.
The gap between them is a state the models never represented: storage exists, and no teammate can read it.
An unauthenticated GET returns 403 there and 404 after a successful materialization, so the difference is real from outside.
The outsider's view still conflates the two failures: a bucket that was never created and a bucket with no public-read policy both answer 403.

### A sibling sees nothing wrong and repairs by redoing

Device A pushed NoteToSelf after the P1 pause and device B refreshed.
B adopted the half-materialized allocation field for field.
The observed `core_storage_allocation` report has three keys and reports `route="pending"`, which does not distinguish this from a finished but unsigned route.
Nothing distinguishes them.

B then called `reconcile_team_route()` with no change arguments.
Materialization re-ran under B's own credentials, `create_bucket` returned `BucketAlreadyOwnedByYou`, the policy was applied, the anonymous GET became 404, and B signed an announcement for the same allocation ID and the same location.
So a partially materialized allocation is recoverable by a sibling without reallocation, which is what the design assumed.
The mechanism is not what the design assumed: B repairs by repeating the action, not by learning that it was incomplete.
Recovery here rests on materialization being idempotent-create, not on any evidence B holds.

### The probed attempts materialize before signing

No pause produced an announcement for its interrupted allocation.
On this sequential path, `_publish_core_route` calls `ensure_cloud_ready` before signing a reread of the same allocation generation.
That supports call-site ordering for the observed attempts; it does not establish atomicity with concurrent allocation/account changes, durable completion evidence for siblings, or the proposed selection-record protocol.

### The provider-issued locator has no producer

`_handle_materialization_outcome` persists a provider-issued locator only on `materialized_with_locator`, and no adapter in the repository returns that status.
The S3, Google Drive, Dropbox and base adapters return `materialized`, `needs_user_action` or `failed`.
The exercised reconciliation path generates locations locally through `_bucket_safe_generated_location`; allocation provisioning can also accept an explicit location.
No current adapter settles a different berth locator during materialization, so that part of Move 6 remains a modeled case.
The writeback branch, the lost-race recovery beside it and the `_same_allocation` comment that excludes location from the generation check are all reachable only from test stubs.
That does not make the rule wrong; it means the case that motivates it is unimplemented, and a future adapter that issues locators will be the first thing to test it.

### Limits

One provider, through one adapter, on one machine.
Google Drive and Dropbox were not probed, and their `materialize` is a status check rather than a create, so nothing here describes them.
No process was killed: interruption is a raised `ClientError` at a chosen point inside `ensure_bucket_public`, which takes the real failure path but does not model a torn write or a crashed provider call whose effect lands later.
An anonymous HTTP GET stands in for a teammate's read; the Hub's `download_from_peer` path was not exercised, and no teammate exists in this schedule.
Nothing concurrent was staged: the two devices act in sequence, so a materialization racing a replacement is still unprobed.
The probe fixes nothing, and four passing cases are evidence about this code path rather than a proof about the runtime.

## Review and condensation of `2f5ff00`, 2026-09-06

The new probe has no identified correctness defect within its sequential S3/MinIO scope.
The recorded command reran successfully: **4 passed in 12.77 seconds**.
The initial sandboxed run could not bind localhost ports (three setup failures and one static case passed); rerunning with permission to start local MinIO resolved that environment restriction.
No broader runtime suite or earlier model/probe was rerun for this documentation edit.

The conclusion is narrowed to the evidence actually inspected: retained allocations and local route reports have no completion marker, while provider bucket/policy state survives.
The old sibling-repair contract's marker remains an unvalidated design assumption, not a runtime finding or a requirement silently removed by this probe.
The provider-issued-locator absence is a source inspection; it does not show that all locators are generated, since provisioning accepts explicit locations too.
Concurrent materialization/replacement, process crashes, provider-issued locators and teammate downloads remain unprobed.
The older housekeeping status was also stale: `git merge-base --is-ancestor 2ebe983 HEAD` succeeds, and `git diff 2ebe983 HEAD -- packages/small-sea-hub/spec.md` is empty.
The Hub spec correction is already incorporated; no merge remains for it.

Condensation retains the decision ledger with reasons and reopening conditions, the schedule catalog, model/probe evidence and limitations, corrected review findings and issue disposition.
Completed agendas, superseded conclusions, repeated handoffs and now-preserved ad hoc control scripts were removed from the working documents.
No executable artifact, runtime code or permanent spec changed.
