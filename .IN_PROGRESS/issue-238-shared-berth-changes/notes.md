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

The model's assumptions are stated in its docstring and are obligations, not findings: evidence is adopted as a unit, adoption is distinguishable from reachability, and predecessor evidence survives a selection change.
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

The route reason separates P2 from the other two and does not separate P0 from P1: a failed bucket creation and a half-applied bucket policy are the same reason.
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

## Move 8 result, 2026-09-06: publication and sibling adoption at the real boundary

Source: [probes/probe_publication_adoption.py](probes/probe_publication_adoption.py), which imports Move 7's two-installation setup rather than copying it, so `probe_interrupted_finalization.py` is untouched and its recorded witness still runs.
Eight cases; twelve across both probes, on two consecutive runs against `589150e`:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_publication_adoption.py .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_interrupted_finalization.py -q
```

No runtime code was changed.
`push_note_to_self` discards the `PublishResult`, so a pass-through recorder around `CodSync.publish` observes dispositions without altering what crosses the boundary.

### Two siblings rotating one berth cannot merge, and the loser is wedged

This is the move's main result and it was not predicted by any model.
A and B adopt a common allocation, disconnect, and each call `reconcile_team_route(new_location=True)`.
A publishes first.
B's publication is refused as `PublicationIntegrationRequiredError`, which is the well-formed part: the refusal names the observed head and the merge base, parks the observed head under an immutable ref, and `note_to_self_conflict_status` lists exactly that ref.
That is the "a superseding refusal is adopted, not quoted" evidence the Move 5 ledger asks for, present in the runtime already.

The integration that evidence exists for cannot run.
`integrate_note_to_self` returns `constraint_refused` with `UNIQUE constraint failed: berth_cloud_allocation.berth_id` and records no head.
Replacement inserts a new allocation row rather than updating one, `idx_berth_cloud_allocation_berth` in `shared_schema.sql` says a berth has at most one, and Splice Merge unions rows by primary key.
So the union of two deliberate selections is not a state the database will hold.

Move 6 warned that union could derive a route nobody chose.
The observed failure is the opposite and worse: union produces nothing, because no rule in the merge decides between two competing rows.
Deciding between them is exactly what the branch's succession counter is for, and no counter exists in this schema.

The control asserts the wedge is permanent under every ordinary next step.
Refreshing reports the same constraint instead of adopting; integrating repeats it; reconciling with no change argument reports `route: ready, route_reason: None` because from B's side nothing is broken; and rotating again — the deliberate human resolution Move 3 modeled — succeeds locally, mints a third allocation and is refused for the same reason.
Throughout, `core_storage_allocation` reports `ready`, so the device that can never publish again is the one that looks healthy.
This is an independent defect in current code, not a property of the proposed protocol; it is recorded in [follow-up.md](follow-up.md).

### Generic publication carries a refused candidate into a sibling's current selection

A rotates into a location the provider never creates (Move 7's P0 pause), leaving a durable allocation with `route_reason: materialization_failed` and no bucket.
A then does something unrelated — registers a second account — and calls the generic `push_note_to_self`, which commits whatever `core.db` holds.
B refreshes for the unrelated change, integration reports `integrated`, and B's current allocation is now A's failed one.
The working allocation is gone rather than retained, because replacement deletes the row it replaces.

So the plan's question is answered: generic publication can silently publish a refused candidate, because nothing between a failed materialization and the shared chain distinguishes a candidate from a selection.
B's own report calls the result `pending`, the same word Move 7 showed does not separate a half-materialized allocation from a finished unsigned one.

The case also exposes a split the models did not represent.
Allocations travel over NoteToSelf; announcements are written into the team Core chain and travel by `push_team`.
B's signed evidence names neither the location it just stopped selecting nor the one it adopted, and nothing orders the two publications against each other.

### Publication finding a descendant tells the device nothing

A publishes, B adopts and deliberately replaces the route, B publishes.
A — which never refreshed — pushes again with nothing outstanding.
Cod Sync reports `already_present` with `observed_head != attempted_head`, which is true and says only that the store's head *contains* A's.
`push_note_to_self` returns `None` for that and for a plain `published`, so the disposition is discarded at the Manager boundary, and every local report A can read still names A's replaced location.
A second case runs the same schedule with B's descendant holding an unrelated change and gets an identical result, so the disposition does not depend on what the descendant contains.
Historical inclusion and current selection are two facts and the runtime surfaces one of them, to a caller that throws it away.

### A lost acknowledgment settles further than the model assumed

Staged by patching `SmallSeaStore.put_latest_link` to perform the real write and then raise the `PublicationOutcomeUnknownError` that method already raises on an unreadable Hub response, so the runtime takes its own path rather than a stub's.
Cod Sync's settlement pass rereads the store, finds this device's own head, and — because the landed write spent the conditional etag — closes the write and returns `already_present`.
The Move 5 ledger treats an unacknowledged publication as incomplete evidence; on this path the evidence is not incomplete, because the etag distinguishes a landed write from a lost one.

The control stages the identical error over a write that did not land.
The etag is unspent, settlement cannot close the write, and `PublicationOutcomeUnresolvedError` propagates uncaught through `push_note_to_self`, which is the typed unknown the ledger describes.
A plain retry then publishes.

What the runtime does not do is keep either answer.
`push_team` writes `.ss_last_push` beside a team's Sync repo for both ordinary dispositions; the NoteToSelf path writes no marker, so the settled claim exists only for the duration of the call.
The landed-but-unacknowledged case also leaves the adopted signal count behind: the write carried `notify=True` and the Hub counted a self-update, while the `already_present` early return in `push_note_to_self` does not advance the baseline.

### What complete route content a sibling actually receives

Two different routes are at stake and the Move 6 ledger entry holds for one of them.
What a teammate consumes is `TransportEndpoint(protocol, url, location)`, and the signed announcement carries exactly those three plus `announcement_id`, `teammate_id`, `berth_id`, `announced_at`, `signer_key_id` and `signature`.
For teammate routing, route content is frozen in the record, as the ledger requires.

What a sibling needs in order to repair is more, and none of the extra arrives frozen.
`get_berth_cloud_allocation_for_berth` is a read-time JOIN of `berth_cloud_allocation` onto `cloud_storage`, so `protocol`, `url`, `client_id` and `path_metadata` are read at repair time from an independently mutable row; `client_id` and `path_metadata` are in that view and in no announcement.
The credentials a repair materializes with are device-local and published nowhere: B's account row reports `credentials_on_this_device` and carries no secret.
So Move 6's reference-binding counterexample has its precondition present in current code for the repair path.
It has no shipped producer: accounts support add, connect-credentials and disconnect-credentials, and no code path updates `protocol`, `url`, `client_id` or `path_metadata` on an existing row.
That is the same shape as Move 7's provider-issued locator — the exposure is structural and the mutation that would exercise it is unimplemented.

### Limits

One participant, two installations, one berth, one provider through one adapter, one machine, no teammate.
Devices act in sequence: nothing here is concurrent, and no process was killed.
The lost acknowledgment is a raised error at a real call site, not a torn write or a delayed effect.
Restore was not probed at all, so the Move 5 restore rules remain model obligations.
The wedge is established for `berth_cloud_allocation`; whether other shared tables refuse the same way was not checked.
Eight passing cases are evidence about these code paths, and the probe fixes nothing.

## Review of Move 8, 2026-09-07

Reviewed `97898a3` against the runtime and the earlier models.
The sibling-rotation constraint failure is a useful new witness.
The review initially recommended a durable-state contract with automatic adoption of competing selections; the subsequent human-resolution discussion below replaces that recommendation.

### Corrections needed before an implementation handoff

- **A counter alone does not remove the wedge.**
  Siblings selecting from the same observed history can mint equal counters; `Store.head()` in `model_publication_outcomes.py` deliberately returns no selection for that tie.
  Both choices must remain inspectable, but the subsequent discussion rejects the review's assumption that this requires adopting both into live state before a human can act.
  The initial plan's one-row-per-berth or ordering-aware-merge alternatives omitted another option: immutable selection records keyed by selection identity, with current routing derived separately.
  Choosing one row during merge must not erase the history required for retrospective checking, and generic sync must not acquire route-selection policy.
- **Provider failure, Git divergence and selection refusal are different evidence.**
  The generic-publication probe demonstrates promotion of an unmaterialized candidate, not promotion of a selection previously refused publication.
  The latter remains unprobed through the runtime.
  Likewise, a parked divergent Git head supplies inspectable history, not a finding that another route has a greater succession counter.
  Manager must interpret retained selection records before making that domain claim; an unrelated Git conflict must not automatically disqualify a selection.
- **Freezing an announcement is weaker than freezing a selection before signing.**
  The field-shape probe establishes that signed announcements contain the three transport fields.
  It does not establish the Move 6 requirement that later signing reads an immutable selection rather than account state joined at signing or repair time.
  Treat that requirement as still unimplemented even for fields a teammate eventually receives.
- **Settlement requires an observation, not merely a landed write.**
  The plan's claim that only a lost write remains unresolved is too broad.
  `CodSync._settle` leaves an inconclusive write unresolved when its observation fails, including when the write actually landed; a changed etag closes the attempted write but alone does not identify whose write landed.
  The probe establishes settlement when the reread succeeds and finds the attempted head.
  The wedge is likewise established for the exercised Manager operations, not as proof that no possible manual repair exists.

### Human-resolution direction from this session

The user challenged the assumption that blocked integration needs an algorithmic remedy: if a person's own device is blocked, they can deal with it if and when they want.
That argument is persuasive because the branch already permits indefinite disagreement, yet the review was still requiring automatic progress in the representation.
Preserving both alternatives under parked Git heads and refusing integration may be the intended result.
The issue to investigate is whether the disagreement is visible and a human can make a meaningful choice, rather than whether the device can merge automatically.
The current `route: ready` report does not explain the blocked integration, and another rotation merely reproduces the constraint failure.

The next move is a walkthrough from that refusal: what B shows, what pauses, what evidence survives, and how choosing A's allocation, choosing B's, or leaving the device paused would work.
Find the smallest operation that makes a choice effective while accounting for unrelated changes and a later disagreement.
Do not require automatic integration before a human acts, continued retries while paused, or eventual convergence.
Reuse the existing parked histories where sufficient; immutable selection tables and a larger durable-state protocol remain options rather than prerequisites.

This reopens automatic counter-based priority, fallback and union adoption, without discarding the earlier witnesses or selecting a replacement protocol yet.
A pause must be triggered by detectable evidence: delayed signing must not silently reverse an observed replacement, and generic publication must not silently promote unfinished or refused work.
Compare pausing on conflicting route evidence with the counter-based candidate, including the cost of asking a human about legitimate route changes.
Keep publication uncertainty distinct from observed disagreement, and distinguish provider repair, another signing act and replaying signed bytes.
Restore and the two NoteToSelf/Core delivery orders remain validation obligations for whichever design is chosen; a full loss of local evidence cannot be assumed to preserve an observed-counter high-water mark.

The top-level docs already allowed human repair when automatic convergence becomes difficult.
The missing guidance was to evaluate detection and pause early, identify whose work is blocked and what harm waiting causes, and allow an indefinite pause on a person's own device as an intended outcome.
README and architecture now state that principle, and AGENTS asks future design work to evaluate it before introducing automatic correction.

### Review validation

The publication-outcome and succession-branch models pass: 50 cases with `.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_publication_outcomes.py .IN_PROGRESS/issue-238-shared-berth-changes/models/model_succession_branches.py -q --tb=short`.
The initial combined Move 7/8 probe run was blocked by sandbox restrictions on binding localhost ports, before the 11 MinIO-backed cases could execute; the non-provider case passed.
Rerunning with localhost access allowed passed all 12 cases in 61.34 seconds: `.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_publication_adoption.py .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_interrupted_finalization.py -q --tb=short`.
No runtime code or probe assertions changed; `git diff --check` passes.

## Move 9 result, 2026-09-07: the human-resolution walkthrough

Source: [probes/probe_human_resolution.py](probes/probe_human_resolution.py), which imports Move 7's setup and Move 8's `_both_siblings_rotate` schedule rather than copying either.
Six cases, on two consecutive runs against `b4d9318`; 19 across all four probes:

```sh
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_human_resolution.py -q
```

No runtime code was changed.
The probe starts from Move 8's refused publication and asks what a person can see, what stops, and what the smallest effective choice is.

### What B shows, and where the evidence actually is

The competing selections all survive, and none of them is reachable through the Manager API.
`note_to_self_conflict_status` reports one parked ref: a name and a commit SHA, with nothing about a berth, an allocation or a disagreement.
`core_storage_allocation` reports B's own allocation and `route: ready`.
Reading the parked commit's `core.db` blob recovers A's allocation, and reading the merge base recovers the predecessor both devices rotated away from, so base, A's choice and B's choice are all inspectable — by opening Git objects and querying a SQLite blob, which is what a human's tooling would have to do.
So the pause preserves the evidence a decision needs and reports none of it.
That is the gap to close before an explicit pause is a design rather than an accident: detection already works, presentation does not exist.

### The pause is not scoped to the disputed berth

One berth's disagreement stops the entire NoteToSelf channel in both directions.
B registers a second cloud account, an unrelated change nobody contests, and `push_note_to_self` is refused before that change is considered.
A registers its own second account and publishes; B's refresh returns `constraint_refused` and applies no rows, so the unrelated change does not arrive either.
This is a real cost of pausing that the human-resolution direction has to price.
Waiting is cheap for the disputed route, because B keeps using its own storage; it is not cheap for everything else the participant's devices need to tell each other, and the blast radius is the channel, not the conflict.

### Choosing the sibling's allocation takes three operations, one of which the Manager lacks

Withdrawing B's competing row is enough to unblock adoption: with that row deleted, the parked head applies, the outcome is `integrated`, A's unrelated account arrives in the same operation, and the outstanding-ref list empties.
That delete has no Manager operation behind it.
`reconcile_team_route` replaces one selection with another and has no way to withdraw one, so the probe does the delete directly against the NoteToSelf database.
Withdrawal is sufficient in this schedule; it does not establish which operation the eventual interface should expose.

Adopting the row is also not the whole choice.
B's own signed announcement still names the location it just gave up, so immediately after integration the route reports `pending`, not `ready`.
A no-argument `reconcile_team_route` repairs that by signing for the adopted allocation, and B publishes normally afterwards.
The demonstrated resolution is three operations — withdraw, integrate, reconcile — of which the runtime offers two.

### Choosing this device's allocation is not expressible at all

There is no way for B to keep the location it chose.
Reinstating it means naming a location that already exists and is already materialized, and `reconcile_team_route` deliberately has no location parameter.
After clearing the block, the only thing B's human can do is rotate again: a third location, a third bucket, and B's materialized storage abandoned.
So of the two choices the walkthrough was asked to evaluate, one costs a hidden row delete and one cannot be made.
The Move 3 model's "deliberate human reselection" has no runtime expression, which is why the plan's requirement that a resolution establish appropriate succession cannot be checked against this code.

### A fresh disagreement pauses again

The same schedule run again from the resolved state wedges again on the same constraint, parks a second ref, and leaves B holding its second rotation.
Both devices deliberately select again, so this is a new conflict and another pause is consistent with the accepted design.
Integration already records a merge commit containing both histories; the result does not establish that the first resolution lacks durable meaning.
Whether replaying the resolved disagreement or delayed announcements can undo that choice remains unprobed.

### What does not pause

The allocation channel stops and the signed channel does not.
While B is refused on `push_note_to_self`, its announcement for its own location is already written in the team Core database, and `push_team` succeeds.
Allocations travel over NoteToSelf and announcements travel over the team Core chain, so a teammate can be told about a route that B's own sibling will never learn it selected.
This is the delivery split Move 8 identified, now with the failure visible from one side: pausing the sibling channel does not pause what teammates are told.

### Limits

One participant, two installations, one berth, one provider, one machine, sequential devices, no teammate, no killed process.
The row delete standing in for a withdrawal is a probe action, not a proposed API.
`push_team` succeeding is established for this setup; no teammate fetched the pushed chain, so teammate-visible routing is not demonstrated here.
Restore is still unprobed.
Six passing cases are evidence about these code paths, and the probe fixes nothing.

## Review of Move 9, 2026-09-07

Reviewed `57c6ada` against the Manager integration and publication paths.
The six new probes passed in 49.08 seconds with `.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_human_resolution.py -q --tb=short` after allowing localhost access for MinIO.
The initial sandboxed run failed before setup could bind its ports.
No runtime code or probe assertions changed.

The substantive correction above separates a fresh disagreement from replay of a resolved one.
The probe's "nothing structural" interpretation was too strong: a new conflict does not show that the earlier choice was forgotten.
The next deliverable should define what a person chooses and demonstrate that the choice remains effective, before selecting its storage representation.

### Debatable choices and the strongest alternatives

- **A choice over reviewed evidence, or separate withdrawal and integration.**
  Prefer an operation that chooses either preserved allocation for the named disagreement, retaining unrelated work from both sides.
  The strongest case for withdrawal is economy: the existing integrator already makes it effective in Move 9, and the primitive could be useful beyond this conflict.
  But withdrawal alone names neither the accepted alternative nor the evidence the person reviewed; integration considers outstanding heads and may encounter additional evidence.
  Show that a changed disagreement remains visible and cannot silently substitute another choice before treating this sequence as the interface.
  This requires a meaningful operation boundary, not necessarily a new transaction framework or resolution table.
- **Channel-wide or berth-scoped pause.**
  Start with a visible channel-wide pause as the simplest candidate: it preserves the existing refusal boundary without requiring partial integration of a shared database.
  The strongest case for narrowing it is that an unrelated account or device-management change should not have to wait for a storage dispute the person chooses to leave unresolved.
  Move 9 demonstrates blocked account propagation, but does not establish harm from that delay sufficient to require a narrower boundary.
  Identify the blocked workflow and its consequence; if waiting is unacceptable, justify how partial integration preserves dependencies and conflicting evidence.
- **Continue or pause team publication.**
  Continuing preserves useful local work and avoids coupling two channels merely because one is blocked.
  The strongest case for stopping disputed-route publication is containment: a local pause is insufficient if teammates silently adopt a route that resolution later rejects.
  Neither safety nor harm at the receiving teammate is demonstrated by `push_team` succeeding.
  Keep this choice open until a teammate consumes both delivery orders; distinguish replay of signed bytes, new attestation and route selection when defining the paused operations.
- **Human pause or automatic counter-based priority.**
  Prefer human resolution for observed incompatible choices under the accepted allowance for indefinite disagreement.
  The strongest case for a counter is ordinary succession: delayed signing or delivery should not require a human to rediscover that Y deliberately replaced X, and Move 1 already shows why signing time cannot decide that.
  A pause candidate must explain what detectable evidence prevents that reversal, including what the teammate can see; the current uniqueness refusal only detects one sibling-integration case.
  Counters may satisfy that ordering obligation, but equal counters still conflict and unequal counters do not establish agreement.
  Retain them if their ordering benefit justifies the machinery, without treating them as the resolution mechanism or automatically restoring fallback and union adoption.
- **Existing Git evidence or explicit resolution records.**
  First test whether retained histories and the integration commit suffice to preserve the selected state and recognize replay.
  The strongest case for an explicit record is semantic evidence: ancestry proves incorporation, not that a person reviewed these alternatives and deliberately selected one.
  Add such a record if a required report or replay rule cannot be derived from the retained evidence; a second fresh conflict is not that justification.
  A record would describe a local choice and its reviewed evidence, not establish global agreement among siblings.

### Focused validation before a handoff

The first new schedule should choose B's existing location with unrelated changes on both A and B, then check that the selected location and both changes survive publication and sibling adoption.
Restart and replay the already-resolved histories separately from making new selections; the former must not silently reverse the choice, and the latter may produce another visible pause.
Introduce additional conflicting evidence between inspection and application to check that the operation still means what the person approved.
Then exercise delayed announcements at a receiving teammate, both NoteToSelf/Core delivery orders and restore against the proposed contract.
These checks supplement the earlier obligations against publication of unfinished or refused work and retries that manufacture new selections.
None requires automatic convergence, a narrower pause, public predecessor links or a new durable-state subsystem in advance of evidence that it is needed.

## Review of Move 10 and next research step, 2026-09-07

Reviewed `5b18218`, which adds `contract-human-resolution.md` and changes no runtime code.
The review identified two counterexamples in the proposed semantics.
A small in-memory Python check reproduced both during review; it was not saved as a branch model and is not runtime validation.
Preserving these controls is the first step in the revised [plan](plan-single-route.md#next-work).

### Counterexamples to preserve

Let A and B be selections with predecessor P and different route content.
Resolution R chooses B's content and names A as its single predecessor.
R and B are temporarily noncompeting under the candidate because their content matches.
An ordinary successor S of R with new content competes with old B, despite complete retained history and no fresh disagreement.
Naming B as R's predecessor instead leaves A competing immediately.
A single predecessor and content equality therefore do not by themselves account durably for both alternatives.

For the clean history X → Y → Z, a recipient holding X and Z but missing Y is classified as observing a conflict.
Receiving Y alone establishes succession without a human choosing anything.
A pause can be appropriate while evidence is missing, but the report must distinguish uncertainty from demonstrated incompatible choices.

Multi-parent ancestry adds a further control.
X names P; Z names P and Y; Y names X, but the recipient does not yet hold Y.
The recipient holds paths from a common ancestor P to X and Z, yet obtaining Y proves Z supersedes X.
A held common ancestor is therefore insufficient to establish that tips remain incompatible when missing ancestry could connect them.

### Committee proposal and limits

The committee proposed multi-parent selections, a three-way evidence classification, and automatic resumption when retrieved history establishes succession.
Multi-parent supersession is a useful candidate for the first counterexample, not an accepted representation of every human resolution.
Evaluate separate choice evidence and retained Git evidence as alternatives, while making each candidate's teammate-visible ordering evidence explicit.
The scalar counter remains an ordering baseline, not proof of agreement or human review.

A signed merge-shaped selection can assert supersession of both branches without proving that a person reviewed them.
That distinction already appears in the earlier public-payload controls and must survive this revision.
Likewise, naming the reviewed predecessors does not replace checking revisions and newly observed conflicting alternatives at application time.
A third selection may arrive while both original IDs and payloads remain unchanged.

Separate currently effective tips from retained historical forks so that preserving evidence does not itself keep a resolved disagreement active.
Resuming after missing evidence establishes succession is a candidate policy, provided no other pause cause or explicit human hold remains.
Human-Scale Coordination permits this policy; it does not mandate automatic progress or require a person to resolve an unavailable-history case.

Public multi-parent ancestry exposes that a signer observed several branches and revisits the recorded #224 decision against a peer-verifiable selection DAG.
The cost is a change to the public evidence boundary as well as payload size or diagnostic metadata.
The model can explore it before any decision to change that boundary.

### Direction from the user and next deliverable

The user asked for concrete next steps while keeping later options open, prioritizing a durable and flexible foundation over shipping quickly.
The plan now separates a bounded model comparison from later durability experiments, runtime probes and an eventual implementation handoff.
Its next deliverable is executable counterexamples, actor-specific claims and a comparison that states reasons to prefer or reject each candidate.
An inconclusive comparison should identify the next discriminating experiment rather than force a representation choice.
Existing pause choices remain the starting candidate; the predecessor correction does not silently settle publication policy, restore behavior or implementation scope.

The contract is marked under revision, with its reviewed body preserved for reference.
No merge-node definition, wire format or runtime change is accepted by this planning update.
No new model or runtime probe was run for this update.

## Move 10 step 1, 2026-09-07: the counterexamples are executable

`models/model_human_resolution.py` preserves the three counterexamples from the [review](#counterexamples-to-preserve) as passing controls.
It runs no runtime code and proposes no replacement representation.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_human_resolution.py -q
```

Nine cases pass; the five earlier models still pass alongside it, 126 cases in total.

The candidate under test is the reviewed contract's: at most one predecessor, and two selections compete when a view holds both, neither reaches the other through held links, and their route content differs.
Each control asserts that classification and, separately, what the complete evidence shows.

### Neither single-predecessor choice retires both branches

With P, A and B held and complete, a resolution R choosing B's content and naming A is not competing with B only because their route content matches.
An ordinary successor S of R with new content then competes with B, on complete history and with no fresh disagreement.
Naming B instead leaves A competing from the moment R exists.
`test_no_single_predecessor_accounts_for_both_alternatives` holds both failures together, which is the form the counterexample should keep: it is not an argument for multi-parent links, only a demonstration that one link plus content equality is not enough.

### Incomplete ancestry is the fact the candidate discards

Both remaining controls report a conflict where none exists, and in both the view holds a selection whose named parent it does not have.
For X → Y → Z with Y missing, delivering Y alone removes the reported conflict; nobody chose anything.
For the DAG variant — X names P, Z names P and Y, Y names X — the view holds paths from P to both tips, so a held branch point is present and the report still says conflict; delivering Y proves Z supersedes X while P remains a common ancestor throughout.
`test_incomplete_ancestry_accompanies_every_misreported_conflict` states the shared fact directly: incomplete ancestry accompanies both misreports, and a held common ancestor does not distinguish them.

That is a necessary condition observed on two schedules, not a proposed classification rule.
Whether incomplete ancestry is the right thing to report, and to whom, is step 2's question.

### Limits

Selections carry a parent set because the DAG control cannot be written otherwise; the candidate never mints more than one parent, and no multi-parent representation is accepted by this step.
The model has one actor type — a view is a held set of selections — so it does not yet distinguish the selecting device, its sibling and a receiving teammate, which is step 2's deliverable.
Delivery only adds, so nothing is forgotten, retained forks are not separated from current tips, and no signature, human review or pause behavior is modeled.

## Move 10 step 2, 2026-09-07: what each actor may conclude

[obligations-actor-claims.md](obligations-actor-claims.md) records the per-actor claims, and the second half of `models/model_human_resolution.py` makes them executable on the step 1 schedules.
Twenty-two cases pass; the five earlier models still pass alongside it, 139 in total.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_human_resolution.py -q
```

These are obligations for step 3 to compare candidates against, not a representation.
The step 1 controls are untouched, so the reviewed candidate's failures still stand next to the corrected claims.

### Unknown ancestry is not an empty ancestry

Modeling the teammate exposed a distinction the vocabulary did not have.
A selection naming no parents is a root with complete ancestry; an announcement that publishes no links leaves ancestry unknown.
Collapsing them lets an actor claim incompatibility from an absence of information, which is the second counterexample in a new costume.
`Selection.parents` is now `None` for unknown, and completeness is false there.

### The teammate row is the sharp one

With no ancestry published — today's runtime — every pair a teammate holds classifies as incomplete ancestry, so it can separate neither an ordinary rotation nor a disagreement and may claim neither.
Publishing links is what buys the supersession claim.
That prices the #224 boundary for step 3 rather than deciding it: it is what each representation must pay, not a decision to publish links.

### Causes are facts, not flags

A pause cause is an incompatible or incomplete pair among the tips, or a contradicted identity, so it disappears exactly when the evidence that produced it changes.
The candidate resume rule — no cause remains and no human hold — promises no progress, since missing history may never arrive.
Separating tips from retained history is what keeps a preserved fork from pausing anything after it is resolved, and matching route content still leaves two distinct selection identities.

### Limits

A merge-shaped claim asserts supersession of several branches and remains no evidence that a person reviewed anything; nothing here records human involvement, and step 3 must keep that gap visible per candidate.
The classification is pairwise, delivery only adds, and no restart, replay, restore, competing human resolutions or multiple disputed berths are modeled.
The resume rule is exercised only on the preserved schedules.

## Move 10 steps 3–5, 2026-09-07: comparing resolution representations

`models/model_resolution_candidates.py` runs four representations against the step 2 obligations on the preserved schedules.
Forty-three cases pass; all seven models pass together, 182 in total.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_resolution_candidates.py -q
```

The oracle is not candidate-supplied.
`classify`, `tips` and `pause_causes` come from step 2 and are identical for every candidate; a candidate decides only what records exist and which actor holds which links.
So a candidate passes because of the evidence it puts in an actor's hands, which is what a reviewer can check.

### What was compared

| Candidate | Where retirement lives | What a teammate receives |
| --- | --- | --- |
| Multi-parent | Both reviewed alternatives named as parents of the resolution selection | The links, inseparable from the selection |
| Separate record | A resolution record beside a single-predecessor selection | Selection plus record, published together |
| Retained local | The same record, never published | Selection only; public succession stays single-parent |
| Counter | Nowhere; scalar ordering only | A rank |

### Results

All three link-bearing candidates repair the first counterexample for the resolving device, on either side of the fork and after an ordinary rotation: one tip, no current incompatibility, both alternatives retained as a historical fork, and the pause cleared.
None of them turns missing ancestry into a disagreement, in either arrival order, on either the missing-link or the missing-second-parent schedule.
Delivering the absent link clears the uncertainty; on a genuine fork, delivering the branch point instead confirms the disagreement, and the model asserts both directions from the same act of delivery.

The separate record makes the choice that broke the reviewed candidate stop mattering: the selection may name the chosen alternative, and the record retires the other, so no single-predecessor choice has to carry both obligations.

The discriminating result is at the teammate.
Under retained-local, public succession alone never retires the alternative, so a teammate that witnessed the fork holds two current tips forever: it classifies the resolution's descendant and the rejected branch as an incompatible choice and cannot resume, no matter how often the old selections are redelivered.
That is not a bug in the candidate; it is the #224 boundary applied honestly.
Deriving resolution from retained private evidence resolves the device and leaves every teammate paused.

The counter remains an ordering baseline and nothing more.
It yields a route for an unresolved fork with equal counters — it cannot report what it cannot represent — and a teammate holding counter-only announcements has unknown ancestry for every selection, so it may claim neither rotation nor disagreement.

Two candidate-independent results came out of the operation boundary.
A third conflicting selection arriving between report and application makes the operation refuse under every candidate, with no selection changed, nothing published, all alternatives preserved, and a new report containing the arrived evidence; naming the reviewed predecessors is not the same check.
And a person-reviewed multi-parent resolution and an automatically minted one produce byte-identical teammate evidence, so no report drawn from that evidence may claim a person reviewed anything.

### Preference, weakness and the next discriminating experiment

The comparison rules out two of the four.
Retained-local fails the teammate obligation permanently, and the counter never detects.

Between multi-parent and the separate record the evidence is close, and the honest reading is that it does not settle the representation.
The weak preference is for the **separate record**, because it decouples three things the multi-parent shape fuses: which predecessor the selection names, what the resolution retires, and how much of that is disclosed.
Retained-local is the same mechanism with publication off, so disclosure becomes a policy choice rather than a representation change — and that is itself a result, not a convenience.

Its weakness is real and is the strongest argument for multi-parent: two artifacts must travel together.
A teammate holding the selection but not the record is in exactly the retained-local state, and concludes a disagreement that was settled.
Multi-parent cannot have that failure mode, because holding the selection is holding the retirement.

The next discriminating experiment follows directly.
If a selection names the identity of its resolution record without naming what that record retires, then a missing record reads as incomplete ancestry rather than as a disagreement — uncertainty instead of a false conflict — and the separate record's failure mode becomes a pause that later evidence can clear.
Whether that holds under partial and interrupted delivery of the two artifacts is the experiment that would settle the choice.
It is recorded as the next step, not implemented.

### Which facts need durable representation

- What a resolution retires. Every candidate that lacks it fails, wherever it is stored.
- Whether a selection's ancestry is known or simply unpublished. Collapsing the two lets an actor claim incompatibility from missing information.
- Selection identity, independent of route content, so a resolution is not hidden behind matching locations.

These can remain Manager policy or local interpretation: whether to publish the resolution record, the pause scope, the resume rule and the human hold, and how the report and its binding are expressed at the operation boundary.
The binding is behavior, not a wire format; the model checks it identically for every candidate.

### The #224 boundary

Both surviving candidates publish ancestry, and step 2 priced why: with nothing published, every pair a teammate holds is incomplete ancestry and it may claim neither a rotation nor a disagreement.
That is the cost of the recorded boundary, stated rather than argued against.

The extra disclosure a resolution adds is narrower than a peer-verifiable selection DAG but is real: publishing that one selection retires two branches tells a teammate that the signer held both.
The separate record is what makes that disclosure separable from succession, since the record can be withheld — at the cost recorded above.
Nothing here changes the boundary; #224's owner still has to agree before public resolution evidence is proposed, and [follow-up.md](follow-up.md) already carries that ask.

### Limits

Delivery only adds, so the partial-delivery case that decides the preference is described but not modeled; that is the named experiment.
No restart, replay, restore, competing human resolutions, or more than one disputed berth.
Signing is a claim with a signer and models no key, revocation or trust change, and nothing here records human involvement.
The classification is pairwise on small schedules, and no runtime code was run for this move.

## Move 10, 2026-09-07: the discriminating experiment

Run to make the step 5 checkpoint decidable, not to settle the representation.
Seven cases added to `models/model_resolution_candidates.py`, 50 in that file and 189 across all seven models; the review correction below brings those to 53 and 194.

The mechanism is the one the comparison named: the selection names the identity of its resolution record, and the record names what it retires.
The record is then an ancestry node carrying no route, so its absence is a missing named parent — which step 2 already classifies as incomplete ancestry.
One rule was needed after all, and the review correction below records it.

It works, in both interruption orders.
A teammate holding the selection but not the record claims nothing rather than claiming a disagreement, and the arriving record clears the pause and leaves one tip.
A record arriving before its selection is inert and pauses nothing, once a routeless node is excluded from the tips; the first version of the experiment only appeared to show this.
The unreferenced version is preserved as a control: with no reference in the selection, a settled disagreement is indistinguishable from a live one, which is the failure the reference removes.

It buys no progress, and the model says so.
Withholding a referenced record leaves a teammate paused on evidence a policy decision is choosing not to send, indefinitely.
So the reference converts a false conflict into honest uncertainty; it does not rescue keeping resolution evidence private.

### What this settles and what it does not

The separate record's one clear disadvantage against multi-parent — a holder of the selection alone concluding a settled disagreement is live — is removable, by a reference that needs no addition to the classification.
Multi-parent's advantage was that it has no partial-delivery state at all, and that remains true; the reference trades it for a pause that clears when the record arrives.

On that evidence the separate record with a reference is the better candidate, because it keeps disclosure separable from succession without the false-conflict cost, and because the same artifact is where evidence of human review would have to live if it is ever represented at all.
Its remaining weaknesses: two artifacts and their delivery to keep track of, a public reference that still discloses that a resolution happened even when the record is withheld, and no reduction in what a teammate must hold to conclude anything.

Reopened by: a delivery model where the record's absence is ordinary rather than exceptional, which would make the honest pause the common case; or a decision that resolution evidence must not be separable, which is the argument for folding it into the selection's parents.

This is evidence for the checkpoint, not a decision.
No representation is accepted, the contract's corrections are unchanged by it, and nothing here was probed against runtime code.

### Review correction, 2026-09-07

An outside review of the experiment found two defects in the models, both reproduced and both fixed in `a7ce749`.

**A record arriving first was not inert.**
The record was represented as a selection with no route, and the tips rule treated every held, unsuperseded node as a route candidate.
The record-first case only passed because the teammate was empty; delivered into a clean A → B history, a record retiring A became a second tip and the teammate paused on a false incompatibility.
The fix is one rule: a held node carrying no route is ancestry only, able to retire what it names but never a candidate itself.
So the claim above that the reference needed no addition to the classification was wrong; it needs this one, and the model now states it.
Three cases cover record-first delivery into populated teammate views, on both fork sides and on the reviewer's exact counterexample.

**Repeated announcements erased contradictory evidence.**
The teammate kept one announcement per signer and selection identity, so a second payload from the same signer overwrote the first before contradiction detection ran.
That broke the contradiction obligation in [obligations-actor-claims.md](obligations-actor-claims.md) and the standing assumption that delivery only adds, and it let one signer rewrite a teammate's held route by redelivery.
The teammate now retains every distinct announcement.
Two cases cover a single signer contradicting itself, which pauses and leaves the first-held route in place, and exact redelivery, which contradicts nothing.

Neither fix changes the checkpoint's reading: the separate record with a reference remains the better candidate on this evidence, at the cost of one explicit rule about routeless nodes.
No runtime code was run or changed.

### Checkpoint follow-up, 2026-09-07: when retirement takes effect

Reviewing the seven commits through `100a874` identifies a remaining semantic question, not a recurrence of either defect fixed in `a7ce749`.
Excluding a routeless record from the tips does not make its arrival inert: `tips` still collects its ancestors as superseded selections.
The populated-fork micro test explicitly expects the record alone to remove the rejected branch from the tips.
A direct model trace confirms the resulting change before any referencing selection arrives:

| Held evidence | Tips | Available route | May resume |
| --- | --- | --- | --- |
| P and its conflicting children A and B | A, B | None | No |
| The same history plus a routeless record retiring A | B | B's route | Yes |

This qualifies the earlier claims that record-first delivery is inert or leaves a populated view undisturbed.
The clean-lineage regression remains fixed, but the fork schedule allows independent retirement and resumption.
Whether that is intended must be decided before treating the partial-delivery experiment as support for the complete resolution contract.

The next experiment should compare independent retirement with retirement supported only through a held selection that references the record, retaining multi-parent selections as a control.
The recommendation is to try the latter because it keeps retirement attached to the selection whose resolution the record explains; this is a proposed semantic choice, not an established obligation or accepted representation.
Check both arrival orders and permanent withholding of either artifact against explicit expectations for tips, route availability, pause causes, resumption and evidence retention.
The separate-record preference must then be reassessed; separable disclosure still does not satisfy the teammate's evidence requirement when the record remains private.

After that checkpoint, restart, replay and restoration are the next bounded research scope already listed in `plan.md`.
Include interruption between persistence and publication steps, competing resolutions, multiple disputed berths, unrelated sibling work, stale review bindings and fresh deliberate disagreements.
Only then extend the runtime probe against the coherent candidate contract, distinguishing stand-ins from demonstrated runtime behavior.

Validation at `100a874`: all 194 model micro tests passed with the explicit file glob below.
The trace above used the existing model without editing it; no runtime behavior was tested.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_*.py -q
```

## Move 10 checkpoint, 2026-09-07: gating retirement on the referencing selection

Run to decide the checkpoint above, not to accept a representation.
`models/model_retirement_timing.py` adds 28 cases, 222 across all eight models.
It compares two semantics for the same artifacts, leaving `classify`, `tips`, `pause_causes` and the resume rule from step 2 untouched:

- **Independent**, what the model did before: a held record retires the selections it names, whether or not any held selection references it.
- **Gated**: a held record supplies ancestry only through a held selection that names it as a parent.
  An unreferenced record is retained evidence and nothing else.

Multi-parent selections are the control, and every expectation is written as literal tips, route, pause causes and resumption, so a candidate cannot pass by agreeing with the classifier about what it just did.

### The record-only state separates the tested schedules

A teammate holding the fork P → {A, B} and paused on it:

| Delivery | Independent | Gated |
| --- | --- | --- |
| Record only, ever | tips {chosen}, chosen's route, resume | tips {A, B}, no route, still paused on the fork |
| Record, then the selection | tips {sel-r}, resume | same |
| Selection, then the record | pause on incomplete ancestry, then tips {sel-r} | same |
| Selection only, ever | pause on incomplete ancestry, indefinitely | same |
| Multi-parent selection | tips {sel-r}, resume; nothing to withhold or reorder | — |

Both semantics keep the `a7ce749` regression fixed: a record retiring A, delivered into a clean A → B lineage, still changes nothing.
Both leave the resolving device undisturbed, because it holds the reference throughout.
All alternatives are retained in every case.

The first row exposes the difference, which also occurs transiently between deliveries in the record-then-selection row.
The final states agree once both artifacts arrive; the question is whether receiving a record whose selection may never arrive is enough to retire a branch and hand a teammate a route.

### Reading

The recommendation from the follow-up survives its own experiment: prefer gating.
The independent row is a teammate acting on an artifact that explains a resolution it does not hold, and the route it adopts is the chosen branch rather than the selection that resolution produced — a route no held selection asserts is current.
Gating adds a pause relative to independent retirement when only the record has arrived, lasting until the referencing selection arrives and indefinitely if it never does.
The earlier experiment already charged the converse cost when only the selection arrives; accepting that cost does not eliminate this additional delivery-order cost.

Gating also makes the two artifacts fail together rather than half-succeed.
Under independent retirement, the record is a second, weaker way to change a teammate's route, and it is the one an adversary or a partial sync would have to deliver rather than the pair.
That is an argument from the model's shape, not a threat analysis; nothing here models an attacker.

What this does not do: it does not reduce what a teammate must hold to conclude anything, it does not rescue keeping resolution evidence private, and it does not decide the recorded #224 boundary, which still has to be settled explicitly before public resolution evidence is accepted.
The separate record with a reference remains the better candidate on the accumulated evidence, now with retirement gated on that reference.
Nothing is accepted here; the contract still records the representation as open.

Reopened by: a delivery model where the referencing selection is routinely lost while its record survives, which would make gating a permanent pause where independent retirement made progress.

### A routeless record still appears in the history

Found while running this experiment, not fixed: `historical_forks` ranges over every held node and `classify` compares route content, so a routeless record pairs with the chosen selection as an incompatible historical fork.
The rule from `a7ce749` excluded routeless nodes from the tips only.
This affects the report `Actor.inspect` produces, not any tip, route, pause cause or resumption, so no result above depends on it.
Whether a record is ever a party to a fork is the same kind of semantic question this checkpoint asked, and `classify` is the shared oracle for all eight models, so the surplus pair is asserted where it appears rather than removed.
Corrected since; see [the follow-through](#fork-subjects-are-route-selections).

### Limits

No runtime code was run or changed, and no artifact is withheld or sent by any rule the model represents; withholding is a schedule here, not a policy.
Nothing in the model is evidence that a person reviewed anything.
Durability, restart, replay and restoration are untouched, and remain the next bounded scope in `plan.md`.

Validation: all 222 model micro tests pass.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_*.py -q
```

## Review of a71d129, 2026-09-07

### Judgment

The checkpoint is useful evidence for preferring gated retirement over independent retirement on the tested two-artifact schedules.
It is not ready to serve as the foundation for durability modeling: the new gate admits a counterexample to its stated rule, and the acknowledged history-report defect should be corrected before reports become persistence or review-binding inputs.
The next steps are the bounded corrections and validation in [historical plan](plan-single-route.md#review-follow-through-before-durability), then a semantic decision, then restart, replay and restore.
No runtime change or public representation is accepted by this review.

### P2: a routeless record can open another record's gate

In `models/model_retirement_timing.py:66–70`, `GatedView._referenced()` gathers parent identities from every held node, including routeless records.
The stated rule requires a held referencing selection, but another record alone can enable retirement.
The inherited `tips()` walks ancestry from every held node, so suppressing the outer record's links does not prevent the inner record from independently retiring a route selection once this global gate opens.

Reproduced without changing model code:

| Delivery into a held P → {A, B} fork | Tips | Route | May resume |
| --- | --- | --- | --- |
| No record | A, B | None | No |
| `rec-1` naming B | A, B | None | No |
| Then routeless `rec-2` naming `rec-1` | A | A's route | Yes |

No route selection references either record, all evidence is retained, and the graph is acyclic.
This shape is accepted by the model's constructors and is neither rejected nor excluded by its stated assumptions.
If record-to-record links are outside the intended vocabulary, make that restriction explicit and enforce it before applying their ancestry; otherwise activation must be rooted in a held route selection.
This is a model boundary defect, not a demonstrated runtime exploit.
Corrected since; the trace below no longer reproduces, and see [the follow-through](#the-gate-is-rooted-in-a-route-selection).

The following trace asserts the observed failure, not the desired behavior:

```python
import sys
sys.path.insert(0, '.IN_PROGRESS/issue-238-shared-berth-changes/models')
from model_retirement_timing import GatedView, actor, resolution_node, state
from model_human_resolution import ROUTE_P, ROUTE_A, ROUTE_B, selection

a = actor('T', 'teammate', GatedView)
a.view.receive(selection('sel-p', ROUTE_P),
               selection('sel-a', ROUTE_A, 'sel-p'),
               selection('sel-b', ROUTE_B, 'sel-p'))
before = state(a)
a.view.receive(resolution_node('rec-1', ['sel-b']))
assert state(a) == before
assert not a.may_resume()
a.view.receive(resolution_node('rec-2', ['rec-1']))
assert state(a)['tips'] == frozenset({'sel-a'})
assert a.may_resume()
assert all('rec-1' not in (s.parents or ())
           for s in a.view.held.values() if s.route is not None)
```

### Existing history defect: fix the report's subjects

The commit correctly discloses the surplus record/selection pair and preserves it as an assertion.
That defect predates this commit, so it is not a second newly introduced regression.
My recommendation is to exclude routeless nodes from fork subjects while retaining them as ancestry and inspectable evidence.
An incompatible route choice requires a route; this follows the existing actor obligations rather than requiring another representation experiment.
Check the full inspection report, retain the genuine A/B historical fork, and show that tips, routes and pauses are unchanged.
Do not retain the surplus-pair assertion as a desired result after that correction.

### Scope of the evidence and its cost

The original write-up overstated the result by saying that only one schedule differs and gating adds no cost.
The record-first schedule also differs between its two deliveries, even when the selection eventually arrives.
The checkpoint discussion above now states that interval and its additional pause explicitly.
Given the accepted human-scale pause design, I still prefer waiting for the selection whose resolution the record explains.
That is a semantic judgment supported by the trace, not a result proven by asserting two different expected outcomes.

This experiment compares retirement timing within the separate-record candidate.
The multi-parent control still has one artifact and no separable retirement to time; preference for a separate record continues to rest on the earlier disclosure and representation arguments.
The recorded #224 boundary remains an independent acceptance question.

### Validation

All 222 model micro tests pass at `a71d129` using the existing explicit file glob.
The counterexample above was also run with `.venv/bin/python` and reproduced the premature resume.
No runtime code was run or changed; this review updates branch documents only.
The passing suite establishes the existing bounded schedules, not the stronger activation rule that the counterexample violates.

## Move 10 review follow-through, 2026-09-07: the corrected gate and history report

Steps 1 and 2 of [historical plan](plan-single-route.md#review-follow-through-before-durability) are done.
Step 3, the semantic decision, is left to a human: nothing below accepts a representation, and the contract is unchanged.

### The gate is rooted in a route selection

`GatedView._referenced` now gathers named parents only from held nodes that carry a route.
A routeless node's own links are suppressed before they are read, so activation is rooted in a route selection and is not transitive.
Two orphan records can no longer open each other's gate: the review's `rec-1` / `rec-2` shape leaves the fork exactly as it was.

Record-to-record links are inert under this rule rather than rejected.
The shape is still received, retained and inspectable, which keeps the model's refusal to claim distinct from discarding evidence, and leaves the question of whether the vocabulary should carry that link at all to the checkpoint.
Rejecting the shape at receipt was the other option the plan named; it would have required the model to decide what a receiver does with an artifact it cannot interpret, which is a durability question rather than a retirement-timing one.

`test_records_alone_never_activate_each_other` exercises both delivery orders and redelivers each record, asserting literal tips, route, pause causes and permission to resume after every delivery, with both records and both alternatives retained.
Independent retirement stays as the control on the same schedule, and does retire on `rec-1` as it does for a lone record.
`test_gating_still_activates_through_the_referencing_selection` shows the correction does not close the gate the experiment was about: with both records already held, the referencing selection still settles the teammate on the chosen route.
The existing selection-first, record-first, withholding and post-resolution rotation cases are unchanged and still pass.

### Fork subjects are route selections

`historical_forks` now ranges over route-carrying held nodes only.
An incompatible choice is a choice of route, so a node with no route of its own is ancestry and inspectable evidence, never a party to a fork.
`classify`, `tips`, `pause_causes` and the resume rule are untouched, and a record keeps supplying ancestry to the pairs that are subjects.

`test_the_history_report_names_only_route_selections` checks the whole `Actor.inspect()` report at three points — the live disagreement, after resolution, and after ordinary rotation — under both semantics and both chosen sides.
The A/B fork is current before resolution and history after it, the record/selection surplus pair is gone, `incomplete` stays empty, and tips, route and pause behavior are asserted alongside the report.
The surplus-pair assertion in `test_gating_does_not_disturb_the_actor_that_made_the_resolution` is replaced by the corrected expectation, as the review asked.

### What this does not establish

Both corrections were checked against their absence: reverting either one fails the new tests and nothing else, so they are not passing by agreeing with the classifier.
Neither correction is evidence for gating over independent retirement.
It removes a counterexample to the gate's stated rule, which the earlier evidence had assumed rather than demonstrated.
The separate-record preference, the #224 disclosure boundary and the retirement semantics all remain open.

Validation: all 236 model micro tests pass; 14 fail with either correction reverted.

```
.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_*.py -q
```

## Review of 98e4bbf, 2026-09-07

No functional blocker found in this bounded increment.
The gate now requires a direct reference from a held route selection, and the history report excludes routeless subjects while preserving route ancestry and the real A/B fork.
The requested delivery, redelivery and inspection counterexamples are present.
An additional review probe referenced only the outer record (`sel-r → rec-2 → rec-1 → sel-b`): the inner retirement remained inactive, both route tips survived and the actor stayed paused, confirming non-transitive activation on that shape.

One P3 reporting correction remains in the validation paragraph above: reverting the gate alone fails 6 micro tests (230 pass), reverting the history correction alone fails 8 (228 pass), and reverting both fails 14 (222 pass).
The history-only failures include four existing cases whose surplus-pair assertion was corrected, so “the new tests and nothing else” should also say “the new or updated assertions.”
These results were reproduced in temporary model copies without changing the reviewed implementation.
The unmodified explicit model suite passes all 236 micro tests.

This review supports proceeding to the human semantic checkpoint, not accepting gated retirement, the separate-record representation or the #224 disclosure change on the human's behalf.
No runtime code was changed or tested.

## Multiple-location reframe, 2026-09-08

The user proposes treating uncertain cloud placement as multiple locations to try, with future migration and primary/backup needs as motivation.
This reopens the single-route assumption; it does not yet accept a replacement contract or authorize migration/backup implementation.

A promising candidate separates authenticated read locations from the participant's write-placement decision.
A reader can inspect several locations without claiming that their announcements are ordered, that their histories agree, or that a person resolved the placement dispute.
Keep complete route records and their provenance; retrieving data remains subject to existing verification and integration policy.
This could remove public succession classification from the prerequisite for reading, while leaving sibling write decisions and meaningful human resolution as separate obligations.
It also challenges the current model's assumption that incomplete route ancestry necessarily pauses a receiving teammate.

Availability, apparent freshness and a history containing another history do not establish that an offline sibling will never publish at the older location again.
Trying all known locations therefore needs an explicit account of retirement and discovery: what permits stopping reads, what late announcements do, and how a reader knowing only an old location learns about a new one.
Location sets alone also do not fix the demonstrated unique-allocation constraint that blocks NoteToSelf integration.
Multiple locations must not silently mean replicating writes, merging independent bundle-chain heads or treating the first successful response as the only source worth reading.

Recommended next comparison, before further retirement-representation detail: two sibling publishers use X and Y, a teammate reads both, one location is unavailable, and later a person chooses Y for future writes.
Deliver a delayed X announcement and a fresh publication from an offline sibling at X, separately; include a receiver initially knowing only X and unrelated NoteToSelf changes.
Compare the single-route candidate with multiple read locations on preserved work, visibility, discovery, write-choice replay, and the evidence needed to stop polling X.
Judge simplification by which existing obligations and state disappear, not by allowing multiple rows alone.
This is a proposed research direction; existing models remain evidence for their stated assumptions and no plan checkpoint or public representation is accepted here.

## Plan reframe, 2026-09-08: channels, write placement and retirement

### Direction from the discussion

The user proposes a small amount of multiple-location support as a way to simplify disputed berth placement, with migration and primary/backup as ordinary future uses of the same capability.
The subsequent discussion distinguishes device retirement from location retirement: devices author and sign, while cloud locations carry publications.
A compromised device can manufacture authorized-looking work until the applicable trust policy deals with it; leaving an old channel available does not itself confer that signing authority.
Authenticity, context binding and integration still need verification at their actual runtime boundaries.

The user also makes the common operating assumption explicit: users do not control cloud providers and can do little about retained copies beyond stopping further uploads.
Accordingly, location retirement must not require proof of deletion, a provider's cooperation in erasure or a key-revocation protocol.
A write stop describes future uploads by the actor honoring it, not cancellation of an already in-flight upload or control over an offline sibling.

I am persuaded that the earlier response put too much weight on establishing permanent silence at the old location.
An offline sibling's later publication is a possible discovery/completeness problem, not proof that retiring the channel was an invalid security decision.
A person can deliberately stop checking the channel, accept that work may be missed, and use manual inspection if they later learn of it and it is still available.
That choice need not establish global agreement or promise eventual recovery.

### Why this is a substantive change

The preceding Move 10 investigation required a receiving teammate to identify a single route, distinguish live disagreement from historical forks, and resume only once the route-selection ambiguity was gone.
Its public ancestry and retirement-record experiments answer that contract.
They do not establish that reading requires the contract in the first place.

The new candidate permits multiple authenticated locations to remain readable.
A teammate may retrieve work without knowing whether the participant's siblings agree about placement, and may refrain from making succession or human-review claims it cannot support.
This changes what the reader needs to conclude, rather than inventing a cheaper proof of the same conclusion.
In particular, the earlier result that private resolution evidence alone is insufficient is scoped to a teammate being required to recognize resolution and select one route.
It is not a proof that public resolution ancestry is necessary for the new behavior.

The current plan therefore replaces the retirement-gate acceptance checkpoint with a bounded comparison and a runtime path map.
The corrected gate and history-classification experiments are retained, not accepted or discarded.
They may be useful if a specific write-choice or reporting obligation later requires them, but no longer dictate the next step.
The complete preceding plan is preserved in [plan-single-route.md](plan-single-route.md).
Its supersession retirement must not be confused with a reader deciding to stop polling a transport.

### What the reframe does not solve by naming it differently

Multiple sources can reveal divergent data histories, which still need the existing integration decision.
A latest or reachable provider head cannot silently replace those histories.
Cod Sync's current format uses a per-remote mutable head, archived links and bundles, including prerequisite checks; the experiment must not pretend these form a single cross-provider chain or CAS domain.
The runtime path map should check source association throughout a chain traversal and preservation/deduplication of fetched evidence.
See [the format specification](../../packages/cod-sync/Documentation/format-spec.md) for the existing boundary, not a multiple-location implementation.

Move 8's allocation uniqueness refusal also survives the conceptual reframe.
A reader's ability to fetch two locations does not make two sibling allocation rows mergeable or supply a meaningful choice of future write destination.
The new plan requires an explicit comparison of retained locations versus write-choice state and a report of any remaining channel-wide blockage.
It does not prescribe merely dropping a unique constraint or teaching generic sync to decide allocation policy.

Finally, trying all known locations does not discover a location known to nobody reachable.
A reader knowing only X needs Y's announcement through X or some other available communication path.
Signed forwarding is a candidate convenience, not a new trusted cloud redirect or a guarantee of migration completion.
When X is unavailable and there is no other route-delivery path, acknowledge that limitation and permit human assistance.

### Recommended minimal defaults, still to test

Read both admissible locations in the bounded experiment, preserving separate outcomes and verifying retrieved work.
Do not stop after the first successful answer when the other may contain distinct work.
Keep a single local write destination per publication attempt, allow siblings to have different local views, and treat adopting a new write choice as distinct from hearing an announcement or retrying a publication.
Use a local explicit read stop as the first retirement candidate, with retained evidence that makes redelivery unable to silently undo it.
These defaults are proposals for comparing behavior, not final schema or API decisions.

There is some unavoidable local state if a stop must survive replay, but its purpose is modest: preserve the person's choice.
It need not certify that all siblings stopped, establish permanent silence, or describe a public route-selection DAG.
The model must not assume this state survives restoration when it does not; persistence work follows the smaller behavioral comparison.
Identity binding for an old route re-announced under a new attestation, or intentionally used again later, remains a concrete representation question.

The first validation set covers two siblings, one teammate and two locations; source failure, duplicate and divergent work, missing chain dependencies, invalid/context-mismatched claims, delayed announcements, an offline writer after a local stop, and a reader knowing only X.
It also preserves the sibling integration and stale-review-binding obligations.
Controls must show that first-success-only reads and write selection by signing time fail the intended assertions.
The comparison should count state removed and added, rather than declare simplification from supporting multiple rows.

### Scope, authority and document changes

This edit carries the discussion into the active plan and adds [contract-multiple-locations.md](contract-multiple-locations.md) as the proposed experimental behavior.
The prior human-resolution contract, sibling-repair contract and actor obligations have historical notices and precise links to the preserved plan.
The issue follow-up now distinguishes the reopened one-route cardinality from participant ownership and device trust, and stops treating public ancestry disclosure as inevitable.
No public spec, runtime code, model implementation or issue has been changed by this edit.

Migration and primary/backup justify testing whether the vocabulary supports overlapping or persistently useful locations.
They do not expand this branch into mirroring, automated failover, provider cleanup, erasure or migration UX.
Practical decisions to stop reading and stop writing are in the new behavioral scope.
The old blanket exclusion of old-location work is narrowed accordingly, without importing provider-object deletion.


### Validation of this document edit

Checked all 69 local Markdown links in the branch's top-level documents, including heading anchors; no missing targets were found.
`git diff --check` passes.
Reviewed the companion documents for stale active-plan references and redirected historical checkpoint links to the preserved plan.
The pre-existing review addition in `notes.md` is retained.
No model or runtime micro tests were run because this change only revises documentation; the 236-test result above is historical evidence, not new validation of multiple locations.

## Runtime path map, 2026-09-08: where multiple locations would land

Reviewed after the reframe commit `3b0a964`, from reading the runtime rather than running it.
The probe in plan step 2 should confirm each item; none is accepted as a finding until it does.

### The single-route choice point is one function

`select_effective_teammate_berth_storage` in `packages/wrasse-trust/wrasse_trust/transport.py` filters a teammate's announcements for one berth to those signed by a device trusted for that teammate in that team, sorts by `announcement_id` descending, and returns the first that verifies.
There is no succession, ancestry or pause anywhere in the runtime.
The uuid7 ordering means the newest signature wins, which is exactly the control the plan requires to be shown failing.
A multiple-read variant would return every trusted, verified announcement with a distinct `(protocol, url, location)` rather than the first; that is the whole read-side interface change at this layer.

### The Hub has two consumers, and the write side already tolerates disagreement

`_download_peer_file` in `packages/small-sea-hub/small_sea_hub/backend.py` calls the selector and reads from the single transport it returns.
This is the read path that would fan out.

`_require_own_storage_announcement` in the same file calls the selector for the publishing device's own participant.
If the selected transport does not match the allocation being written to, it falls back to `_has_current_device_storage_announcement`, accepting the current device's own signed announcement instead.
So a device already publishes to its own choice when a sibling's newer announcement differs.
The runtime is closer to the "one local write destination per device" default than the earlier plan assumed.

### Cod Sync keeps sources separate for free, but fetch does not preserve a divergent head

A `CodSync` instance owns one store, so a chain walk (`_resolve`) never mixes endpoints; reading X and Y is two instances, not a new feature.
Parked refs are named by link uid, so two stores cannot collide on a parked ref.

Gap: `fetch` with `pin_to_ref` imports the remote chain's objects, then raises `PinIntegrationRequiredError` when the pin cannot advance.
Unlike `publish`, it does not park the observed head.
The divergent head's objects are in the object store with no ref pointing at them, recoverable only from the SHA in the exception.
Preserving alternatives on the read side needs that closed, or a caller that parks itself.
The probe should record exactly this.

### What the announcement signature binds

The canonical bytes cover announcement id, announced-at, berth id, location, teammate id, protocol, signer key id and url.
Team id is not in the signed bytes.
Trust is resolved per team from certificates before the signature is checked, so a device not trusted in a team is filtered before verification.
Whether that suffices when one device key is trusted in two teams that share a berth id is a question for the probe, not a claim.

### The sibling wedge is a schema constraint the reframe does not touch

`idx_berth_cloud_allocation_berth` in `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql` is a unique index on `berth_id`, and `add_berth_cloud_allocation_by_berth_id` refuses a second row before insert.
Two siblings each holding one row for the same berth cannot integrate NoteToSelf, as Moves 8 and 9 demonstrated.
Reading both siblings' locations from a teammate's side changes nothing here.
This is the primary #238 defect and is the subject of the human decision in plan step 4.

### Consequence for the plan

The reframe removes a proposal, not code.
Counting state against the runtime, multiple reads add a read set, per-source observations and read stops, and remove nothing.
The plan was resequenced accordingly: path map and probe first, a thin model only for stop/replay, the wedge decision before any Hub read-path change.
The "compare against the single-route candidate on the same deliveries" requirement was dropped in favor of listing which Move 10 obligations the new candidate no longer carries.

### Validation of this document edit

Line numbers were checked by reading the named functions and the schema file; no code was run.
`git diff --check` and the local link check are recorded in the commit that carries this edit.

## Multiple-location probe result, 2026-09-08: the runtime with two readable locations

Plan step 2, in `probes/probe_multiple_locations.py`.
Eight tests, all passing, asserting observed behavior rather than a design.
The probe confirms most of the path map above and corrects one item.

### What is real and what is staged

Real: MinIO, the Hub's own endpoints, `reconcile_team_route`, `push_team`, `fetch_teammate_core`, Cod Sync's publish and fetch, and one sender-key redistribution.
Device A publishes at its first location L0, replaces it with X and publishes again, so X's head descends L0's.
Device B, whose team clone predates both replacements, selects Y and publishes its own divergent head there.
A spare location Z is materialized by a further rotation and never written.

Staged, because no runtime path carries it: the sibling's authentic signed announcement row is inserted into the reader's team DB, as `probe_delayed_signing.py` does.
Two provider-level stagings support single schedules: one bucket-to-bucket copy for identical content at two locations, and deletion of X's bundle objects for the incomplete-store schedule.

The reader is device A, not a third participant.
`_download_peer_file` selects by teammate and berth and reads anonymously, so nothing on that path depends on the reader being someone else.
A real teammate would need its membership certificate, the sibling's device certificate and the announcements staged into the other devices anyway, because none of them travels over the fetch path that #185 built.
That is a departure from the plan's wording and it is recorded here rather than hidden; it does not change the code under test.

### Reading a sibling's location is not only a routing question

Getting device B's location readable at all required B to redistribute its team sender key to A **before** B's first upload.
A key redistributed afterwards arrives at a later ratchet iteration, and the already-uploaded objects fail to decrypt with `No skipped key for iteration 2 (current iteration: 3)`.
So each additional publishing device is a decryption prerequisite for every reader of its location, ordered before that device publishes.
The path map treated multiple locations as a selection and transport question; this is a third boundary, and a reader-side fan-out design has to say where those keys come from.
Scoped since to the ratchet state the probe exercises; see [the follow-through](#multiple-location-review-follow-through-2026-09-08-source-binding-fallback-and-the-sender-key).

### Selection, and the absence of any fallback

`test_the_reader_uses_the_newest_announcement_and_reaches_one_location`: with X and Y both admissible, the peer store returns Y's head, the higher `announcement_id`, and `fetch_teammate_core` pins Y.
The Hub exposes no way to ask for the other one.
This is the "latest signature wins" control the plan required to be visible in the runtime rather than argued from the model.

`test_an_empty_selected_location_is_not_backed_up_by_a_published_one`: with the newest announcement naming Z, which exists at the provider and holds no chain, the reader reports that the teammate publishes no Core chain while X and Y both hold complete, readable histories.
One announcement is chosen and one store is asked; there is no fan-out and no fallback.

### Divergence: the protocol drops the second head, the caller catches it

`test_two_fetches_import_both_heads_and_leave_the_second_unreferenced`: two `CodSync` instances over two stores need no new feature, and a chain walk never mixes endpoints.
The second half of that sentence is wrong outside the fixed-announcement schedule; corrected since, see [the follow-through](#multiple-location-review-follow-through-2026-09-08-source-binding-fallback-and-the-sender-key).
The second fetch imports Y's objects and then refuses the pin; afterwards `repo.has_commit(head_y)` is true and no ref in the repository names it.
The path map called that a gap to close.

`test_the_manager_parks_the_head_the_protocol_leaves_unreferenced` corrects the consequence: `fetch_teammate_core` already catches `PinIntegrationRequiredError` and writes an immutable observation ref at the divergent head, and `list_core_source_heads` then reports both heads.
Preserving alternatives on the read side is existing caller behavior, not something a fan-out design would have to invent.
The gap is confined to Cod Sync's own `fetch`, which is worth stating precisely: a second caller written against `fetch` alone would silently lose the observation.

### Ancestry, duplicates and an incomplete store

`test_an_older_location_does_not_roll_the_pin_back`: reading L0 after X reports `stale` and leaves the pin at X; the reverse order reports `created` then `advanced`.
Held work is not rolled back, and the ancestry describes the two stored histories only.

`test_the_same_publication_at_two_locations_settles_on_one_head`: with X's objects copied to a second location, both delivery orders give `created` then `unchanged`.
Deduplication here is ordinary chain validation, not a comparison between sources.

`test_a_missing_bundle_at_one_location_is_reported_not_spliced`: with X's bundles deleted and its links intact, Cod Sync raises `ObjectNotFoundError` and the Manager reports `CoreFetchRemoteError`, which is distinct from the missing-publication error above.
The pin does not move, and Y stays readable — but only because a separate announcement names it.
Nothing in the runtime turns X's failure into an attempt at Y.
That schedule left the reader holding X's announcement alone, so it could not have used Y; replaced since by `test_an_incomplete_selected_location_is_not_backed_up_by_a_known_one`, which keeps both installed, see [the follow-through](#multiple-location-review-follow-through-2026-09-08-source-binding-fallback-and-the-sender-key).

### The write side already keeps one destination per device

`test_a_siblings_newer_announcement_does_not_move_this_devices_writes`: with Y as the selected announcement and X as A's own allocation, A's `push_team` publishes to X.
`_require_own_storage_announcement` compares the selection against the allocation and falls back to this device's own signed announcement.
The experiment's proposed default — one local write destination per device, a sibling's announcement not redirecting writes — is what the runtime does today, so that part of the candidate costs nothing to adopt.

### What this does not establish

Nothing here integrates two retrieved histories; the divergence is preserved, not resolved.
Because the reader is device A, the objects at L0 and X are already local and only Y's head is a genuine import; what the fetches establish is which store was asked and where the refs ended up, not the cost of transferring a history.
Nothing here is evidence about a third participant's certificate and membership delivery, about persistence of any of this across restart, or about more than two publishing devices.
The allocation uniqueness wedge is untouched, as expected: it is a write-side schema constraint and the read path never reaches it.

### Validation of this document edit

`.venv/bin/python -m pytest` over the five probe files: 27 passed in 138 s, of which 8 are new.
The pre-existing model suite was run unchanged in the same session: 236 passed.
Every claim above names the test that produced it.

## Read-stop model result, 2026-09-08: replay, missed work and discovery

Plan step 3, in `models/model_read_stops.py`.
Eleven tests, standalone: it shares no vocabulary with the single-route models, because those represent route succession and a receiving teammate's pause and this candidate has neither.
Modeled state is what the plan allowed — locations known, read stops and write stops held, publications retrieved, one write destination — with no simulator, clock, polling or migration engine.

Results, one per schedule.

A read stop survives replay of the announcement that named the location, whether the same announcement is redelivered or a fresh one for the same location is minted.
The control `ReplayForgetsStop` shows the assertion has teeth: with the stop cleared on hearing the location again, nothing distinguishes the resumed location from one that was never stopped.
Reading again is not resuming: only a deliberate `resume_reading` returns the location to the read set.

Work published at a stopped location by an offline sibling is missed and stays valid.
The read outcome never claims completeness, the stop report says `work_may_be_missed`, and a deliberate `inspect` recovers the work while the location is still available without resuming it or making it a write destination.
When the location is unavailable, `inspect` reports `unavailable` — a limitation, not a recovery protocol.

A reader knowing only X does not discover Y by trying everything it knows.
Y is learned through a delivery or not at all; the model gives the forwarding-through-X and out-of-band paths as labeled candidates and reports the third case as undiscoverable rather than enumerating the world.
Learning Y neither stops X nor moves writes to Y.

Read stops and write placement stay separate: a delayed announcement for X does not move writes away from Y, stopping reads at X leaves the write destination alone, and a write stop describes this actor's uploads only — the provider keeps every byte and a sibling can still publish there.
The stop report claims no erasure, no signer revocation and no permanent silence.

The plan's other required control is also here: `FirstSuccessReader` returns after the first location that answers, reports an outcome that looks successful, and loses the other location's unique work.

### Which Move 10 obligations this candidate drops

Each of these was an obligation of the single-route contract preserved in `plan-single-route.md`, and none of them survives into the current candidate.

- **Identify one current route before reading.**
   Dropped: reads are per-location and the read set never asks which location won.
   The runtime never implemented the pause this obligation existed to lift, and the probe shows the selector ignoring succession entirely.
- **Pause on incomplete route ancestry.**
   Dropped as a read prerequisite.
   What remains is the narrower limit the plan retained: incomplete evidence still cannot justify a succession claim.
- **Public succession links, so a teammate can separate live disagreement from a historical fork.**
   Dropped: no such classification is needed to fetch, retain and report.
- **A retirement record whose effect is gated on a referencing selection.**
   Dropped: a read stop is local, retires nothing publicly and asserts nothing about other devices.
   The Move 10 checkpoint comparing independent and gated retirement answers a question this candidate no longer asks.
- **Establishing that an old location will never carry another publication.**
   Dropped explicitly; the model reports possible missed work instead.
- **Permission to resume once route ambiguity is gone.**
   Dropped: resuming is a deliberate local request with no ambiguity precondition.

What the candidate keeps: verification before use, preserved alternatives, no rollback of held work, honest reports of what was and was not observed, a deliberate choice not reversed by replay or retry, and a write destination that changes only by decision.

What it adds, counted against the runtime rather than against Move 10: a read set, per-source outcomes, read stops with enough evidence to recognize replay, and the sender-key prerequisite the probe found.
What it leaves open: discovery of a location nobody has told the reader about, integration of divergent retrieved histories, and persistence of a stop across restart or restoration, which is plan step 7.

### Validation of this document edit

`.venv/bin/python -m pytest models/model_read_stops.py -q`: 11 passed.
The eight pre-existing model files were run unchanged in the same session: 236 passed, reported separately as the plan requires.

## Review of b81e2af, 2026-09-08

Two corrections are needed before treating step 2 as sufficient evidence for a runtime handoff.

- The claim that a chain walk never mixes endpoints is too strong.
  `PeerSmallSeaStore` retains a teammate id, not a selected location, and Hub `_download_peer_file` selects again for each object request.
  These probes replace announcements between fetches and hold them fixed during each fetch.
  A new announcement arriving after the latest-link read can redirect the bundle or predecessor reads to another location.
  Narrow the conclusion to the fixed-announcement schedule and add a controlled mid-fetch announcement change with recorded provider requests.
  Source binding remains a Hub/store design obligation; separate Cod Sync instances alone do not provide it.
- The missing-bundle probe removes every announcement except X before the failing fetch.
  Y being available at the provider cannot test fallback when the reader no longer knows Y.
  Keep both authenticated announcements installed, remove bundles from the selected location, and record which locations the runtime attempts.
  The empty-location probe already covers its distinct failure case with alternatives known.

The next design decision remains plan step 4.
Prefer separating retained locations from the device's explicit write choice: multiple valid locations need not block unrelated NoteToSelf integration.
This is a recommendation, not acceptance of schema changes.
The experiment must still show that sibling integration preserves unrelated work and does not redirect writes, and that a reviewed write choice is refused when relevant evidence changes.
Keeping the uniqueness refusal is viable only with a concrete visible resolution path and an explicit account of the unrelated work it blocks.

Keep the sender-key observation scoped to the exercised ratchet state.
Add a reproducible late-distribution case before using the reported setup failure to set a general historical-readability requirement.
The model establishes in-memory stop behavior only; restart/restoration and authenticated source binding remain later validation obligations.

Review validation: the 11 new model micro tests passed, and all eight new runtime probes passed in 45.76 seconds after granting localhost service access.
The first probe run was blocked at socket binding by the sandbox, before exercising the runtime.
`git diff --check` passed.
The 236 older model micro tests were not rerun for this review.

## Multiple-location review follow-through, 2026-09-08: source binding, fallback and the sender key

The review of `b81e2af` rejected two of the probe's conclusions and asked the sender-key observation to be scoped.
`probes/probe_multiple_locations.py` now holds ten tests: two new ones, one rewritten, and one narrowed docstring.

### A chain walk is not bound to one location

The claim that a chain walk never mixes endpoints was too strong, and only the fixed-announcement schedule supported it.
`PeerSmallSeaStore` retains a teammate id, not a selected location, and `_download_peer_file` runs the whole selection again for every object request.

`test_an_announcement_arriving_mid_fetch_redirects_the_rest_of_the_walk` installs a second announcement in the gap between the latest-link read and the bundle read, and records the location the Hub resolved for each request.
Redirected to a byte-identical copy, the fetch reads its head at X and its bundle at Z, pins X's head as `created`, and reports nothing unusual.
Redirected to the sibling's divergent chain it fails with `ObjectNotFoundError`, because the bundle the link names was never uploaded there.

A walk that stays at one location therefore does so because nothing changed underneath it.
Source binding is a Hub and store obligation, and separate `CodSync` instances do not supply it: a fan-out design has to say what a walk is bound to, and the runtime currently binds nothing.
The recorder patches `_download_peer_file` and `_select_teammate_berth_storage` on the backend class, which is the only place the location of a single object read is visible.

### The fallback question, asked with the alternative installed

The earlier incomplete-store schedule deleted X's bundles and left the reader holding X's announcement alone, so Y's availability at the provider was not something the reader could have used.
It tested nothing about fallback.

`test_an_incomplete_selected_location_is_not_backed_up_by_a_known_one` deletes the bundles from Y — the newest announcement and therefore the selection — and keeps both authentic announcements installed throughout, while X holds a complete chain.
Cod Sync raises `ObjectNotFoundError`, the Manager reports `CoreFetchRemoteError`, the pin and the latest ref stay where they were, and every recorded request names Y.
The conclusion the earlier test claimed now rests on a reader that had the alternative in hand.

### The sender-key ordering, scoped to one ratchet state

`test_a_sender_key_distributed_after_the_upload_cannot_read_it` holds the ordering fixed instead of meeting it once in setup.
With B's redistribution withheld, A holds no sender key for B's device and the Hub answers `peer_sender_key_unavailable`, a named prerequisite.
After redistribution, A holds B's chain at iteration 3 while B's own record still holds message keys for iterations 0, 1 and 2: a distribution message carries an iteration and a chain key and none of the skipped keys, so what B already uploaded stays unreadable.

That is a claim about this ratchet state, not about history in general.
A reader can read what B publishes from the distributed iteration onward and nothing earlier, and no runtime path delivers the earlier keys; whether one should exist is not asked here.

The second failure is also misreported twice.
`decrypt_group_payload` raises a bare `ValueError` rather than the `SenderKeyUnavailableExn` the Hub route classifies as a prerequisite, and the client store then reads it as a failed request.
In-process the exception reaches the store directly; over a real HTTP boundary it would arrive as a 500.
Either way the reader is told the request did not complete, when the bytes arrived and cannot be decrypted.

### What this follow-through does not establish

Nothing here binds a walk to a source or proposes how to; it shows that the runtime does not.
The mid-fetch schedule changes announcements at one chosen point, which shows the redirect is possible, not how likely it is under real delivery.
The sender-key result covers one distribution of one chain; multiple rotations, more than two publishing devices, and any recovery of earlier keys remain unexamined.

### Validation of this document edit

`.venv/bin/python -m pytest` over the five probe files: 29 passed in 157 s, ten of them in `probe_multiple_locations.py`.
No model file changed, so the 236 model micro tests were not rerun.
`git diff --check` is recorded in the commit that carries this edit.

## Accepted simplification, 2026-09-08: evidence gathering during a human pause

The user proposed allowing the Hub and Manager to read relevant sources to gather evidence whenever source use for a berth is uncertain, while other normal operations wait for a human decision.
The subsequent analysis was accepted for incorporation into the branch documents.
This supersedes the previous candidate's expectation of continued normal reading across multiple locations during a placement disagreement.
It does not undo the distinction between cloud locations as transport and devices as trusted authors.

The accepted boundary is investigation during a pause.
On detecting an unresolved source-use question, the device pauses normal reads, integration and publication for the affected berth.
The Hub and Manager may fetch, verify, compare and preserve alternatives, with provider I/O through the Hub and existing authorization, device trust and explicit stops enforced.
Evidence stays outside live integration until resolution; investigation neither redirects writes nor silently resumes routine use of a stopped source.
A human may authorize deliberate inspection of a stopped source without canceling the stop.
Investigation and the operations needed to apply the human resolution remain possible while paused.

Uncertainty means a detected unresolved question, not an inability to prove that every possible location is known.
Several sources deliberately accepted by a human need not themselves be in dispute.
New evidence can improve the explanation, including showing that the apparent disagreement has disappeared, but cannot automatically release this pause.
An explicit human choice is required, and any other independent block must still be honored.
The choice is local and may authorize one source or several; it does not prove global agreement or control offline siblings.
Normal multi-location operation remains deferred, and the first complete path only needs to select either existing location.

The pause is scoped to the affected berth on the detecting device.
Move 9 shows that today's allocation refusal also blocks unrelated NoteToSelf work.
That broader pause must be visible and the blocked work preserved, but this branch need not build isolation machinery merely to keep that work moving before the human acts.
Preserving unrelated work through resolution remains required.

This settles the previous step 4 question at the behavioral level: a visible human pause is the chosen direction.
It does not select a schema change or endorse the current uniqueness refusal as sufficient implementation.
The refusal preserves alternatives but does not present the disagreement meaningfully, and choosing either existing location is not fully expressible through the Manager.
The completion target is therefore one connected path: detect, visibly pause, inspect and preserve both sides, choose either existing location over the reviewed evidence, and resume without losing unrelated work.
Newly relevant evidence between review and application must invalidate the stale choice.
Restart and replay with retained evidence must not silently release the pause or reverse an explicit choice.

The simplification avoids requiring automatic progress through disagreement, a public route-resolution graph, global agreement, or proof that a retired location will never contain another valid publication.
It does not remove the need for concrete detection, source-associated retrieval, evidence preservation, pause enforcement and effective resolution.
Unknown sources and unavailable bytes remain discovery and recovery limitations that a human can accept; they do not mandate a new recovery protocol.
The earlier probes and models remain evidence under their recorded assumptions, not validation of this newly accepted behavior.

Updated plan.md, contract-multiple-locations.md and follow-up.md to use this boundary and defer automatic multi-location operation and elaborate retirement workflows.
This is a documentation update only; runtime code, historical experiments and public specifications are unchanged.

## Step 4 handoff, 2026-09-08: what the pause costs in the runtime

[handoff-sibling-disagreement.md](handoff-sibling-disagreement.md) is the plan step 4 deliverable.
Written from reading the runtime; no code was run and nothing is implemented.
Two questions in it are for the planning committee and are stated below rather than decided.

### The pause cannot be derived, and that is a contract requirement

The attractive design was no new durable state at all: derive the pause from the parked refs and the live rows the way `note_to_self_conflict_status` derives outstanding sources, so restart and replay are covered for free by ancestry.

It fails one clause.
The contract requires that evidence removing the apparent disagreement — a sibling that adopted this device's choice, or withdrew its own — must not release the pause without a human.
A predicate over current state releases exactly then, and a control that clears the pause on re-observing agreement is indistinguishable from never having paused.
So a held pause is a device-local row, and only `resolve_berth_source` deletes it.

Restart and replay still cost nothing beyond the row: replay is filtered by ancestry in `outstanding_sources`, and re-detection over an unchanged candidate set recomputes the same digest.

### Detection has to be a domain projection, not a classified error

Today the disagreement surfaces as `sqlite3.IntegrityError` text inside a `constraint_refused` outcome.
Reading a berth and two locations back out of that string would be the cheapest change and the worst one: it binds the Manager's report to SQLite's message format, and it can only fire where the constraint fires, which is one integration path.

The projection — more than one allocation row for one berth, with no local choice over exactly those rows — is available at integration, at refresh, at this device's own rotation and at status read time, and it carries the alternatives rather than a sentence about them.

### What no representation recovers: who chose what

`berth_cloud_allocation` has no author column, and Cod Sync proves a history's structure rather than its author (#190).
A report can name the head a row first appears under and its local `created_at`; it cannot say "device B chose this" without inventing evidence.
The human decides between two locations, not between two devices.
That is a limit to state in the report, not a gap to close in this branch.

### Choosing the sibling's location and choosing this device's own become the same operation

Move 9 found the first cost a raw row delete and the second inexpressible.
Under the handoff both are `resolve_berth_source(berth, allocation_id, digest)` followed by an argument-free `reconcile_team_route`, differing only in the id.
`reconcile_team_route` still needs no location parameter, because after resolution the chosen allocation is the berth's current one and the no-change branch of `resolve_berth_cloud_allocation_intent` returns it.

### For the planning committee

These are the original questions; the response and revised handoff are recorded in [the review follow-through](#step-4-review-follow-through-2026-09-08).

**A. Drop `idx_berth_cloud_allocation_berth`, or keep it.**
Dropping it lets a berth hold candidate rows while a question is open, and moves "one allocation per berth" from a schema constraint to a rule the pause enforces.
It is a shared Core schema change: `SHARED_SCHEMA_VERSION` bumps, existing playgrounds are replaced rather than migrated, and the Manager and Hub specs both state the one-row rule today.

Keeping it is the smaller diff to the schema and a larger one everywhere else.
Detection would have to run before `apply_delta`, over `ours_delta` and `theirs_delta`, because the constraint converts the disagreement into a refusal of the whole source.
The alternatives stay inside parked Git blobs, so presentation means extracting `core.db` from a commit and querying it — what `probes/probe_human_resolution.py:_allocations_at` does by hand.
Unrelated work from both siblings stays blocked for as long as the human declines to decide, which is the cost Move 9 measured and the contract asks to be made visible.
And "keep my own location" needs the resolution to suppress one row from `reconcile_deltas` output, which is a second merge policy living beside the first.

Recommended: drop it.
The pause is the thing that has to be visible and enforceable, and a constraint that turns the question into an error can neither present it nor be scoped to it.
Not recommended silently: it reopens #224's exactly-one-route cardinality in storage as well as in the read path, which [follow-up.md](follow-up.md) already flags as needing the issue owner.

**B. Does resolution delete the losing candidate rows from shared NoteToSelf?**
The handoff says yes, in the same transaction as the choice.
That makes the choice effective for siblings by ordinary adoption: a sibling fast-forwards, finds its signed announcement no longer matches the surviving allocation, reports `route: pending` and reconciles.
One participant, one location per berth, restored without any device agreeing.

The alternative is that resolution writes only the device-local choice and leaves both rows.
Then each sibling detects and resolves independently, which is the most literal reading of "does not claim agreement by devices that have not participated" — and it means the shared state holds two allocations indefinitely, every sibling pauses, and a participant with three devices decides three times.

Recommended: delete.
A participant's own management decision written to the participant's own shared state is not a claim about a device; it is what NoteToSelf is for.
But it does change a sibling's storage destination without that device's human being asked, and it orphans whatever that sibling materialized, so it is the committee's call and not a detail.

**C. Scope, folded into A and B.**
The handoff pauses this device's own-berth provider I/O and leaves teammate reads of the same berth id alone, since those resolve the teammate's placement and not the decision in dispute.
Publication pausing is settled by the contract rather than by the Move 9 review's request for teammate-side evidence, which remains unobtained.

### Validation of this document edit

No code was run and no runtime code changed.
Every runtime claim cites a file and line read in this session; the probe and model results it builds on were validated in their own sections above.

## Step 4 review follow-through, 2026-09-08

The user had no strong preference on A and invited arguments.
The review recommended removing allocation uniqueness because competing live candidates make the berth question directly inspectable without rejecting unrelated NoteToSelf changes or introducing a second merge policy.
The revised handoff adopts that recommendation under the user's request to fix the findings; it is not recorded as an earlier explicit user preference.

For B, the user chose deletion of losing candidates from shared NoteToSelf.
They also stated an eventual goal of minimal-fuss provider migration and deferred designing security checks that compare old and incoming placement state and may involve a human during adoption.
The revised handoff preserves that boundary: delete active allocation rows, retain evidence, and do not equate deletion with removing provider bytes, accounts or credentials.
A device that already holds a pause must still inspect its alternatives and explicitly decide; that is an existing contract requirement, not the deferred general adoption-time security review.

The review found four defects in the proposed handoff, corrected as follows.

1. Investigation previously retained the check that rejects paused berths.
   A Manager-only Hub path now explicitly exempts investigation from the ordinary pause and live-allocation lookup while preserving authorization, admissible announcements, credential checks and publication verification.
   It binds every request in a chain walk to the selected saved route and never materializes or integrates.
2. Detection previously ran after adoption committed, while the Hub could accept an old local choice among fresh candidates.
   Adoption and local allocation changes now project pause state in the same attached SQLite transaction as the shared rows.
   The Hub reads one committed snapshot and refuses all multiple-live-allocation states regardless of an earlier choice.
3. The digest previously bound allocation ids but not mutable route content, and resolution compared it before starting the mutation transaction.
   The revised digest binds complete reviewed route content, live or withdrawn status and source-associated evidence.
   Resolution compares and applies under one SQLite writer reservation; publication of relevant observation refs and outcomes participates in that same reservation.
   Provider fetching and verification stay outside the short reservation.
4. Shared deletion previously removed the only route lookup offered by the inspection interface.
   Versioned device-local report snapshots now retain routes and provenance through deletion and restart, and distinguish different content under a reused allocation id.
   An explicit choice can restore a withdrawn allocation only with matching existing account context and no conflicting live identity; missing prerequisites preserve the pause rather than creating accounts or provider objects.

With deletion on resolution, `berth_write_choice` is a decision record rather than a routing override.
The single surviving live allocation determines the destination, and clearing the pause still leaves ordinary credential, announcement and publication checks in force.
Status may refresh local evidence, so the handoff no longer calls it read-only.

Updated the handoff, plan and local issue follow-up proposals.
Step 5 is no longer blocked on A and B, but runtime implementation and validation are still outstanding.
Added validation schedules for adoption interruption, stale local choices, concurrent evidence changes, deleted-candidate inspection and deliberate restoration.
No runtime code or public specifications changed, and no issue messages were sent.

## Step 5 result, 2026-09-08: the path implemented and validated

The handoff is implemented and the connected path runs.
`probes/probe_source_resolution.py` has nine passing scenarios against a real Manager, Hub and MinIO;
`packages/small-sea-manager/tests/test_note_to_self_integration.py` gained eleven micro tests that pin the projection, the pause and the resolution down without a network.
The repository suite was run before and after.

### What landed

Allocation uniqueness is gone (`SHARED_SCHEMA_VERSION` 58 to 59) and the invariant it carried is now a projection plus an enforcement point.
`berth_source_pause` and `berth_write_choice` are device-local rows (`LOCAL_SCHEMA_VERSION` 11 to 12).
The representation lives in `small_sea_note_to_self/berth_source.py` rather than in the Manager, because the Hub reads it and must not import Manager code; the policy lives in `small_sea_manager/berth_source_decision.py`.
Detection runs inside adoption's own row transaction and inside the local allocation-change transaction, before and after each mutation.
The Hub enforces at `_resolve_berth_cloud_or_raise`, reading the pause and the allocations in one read transaction.
Investigation is a Manager-only `GET /berth_source/inspect` plus `CandidateInspectionStore`, which carries the candidate key on every object request so a walk cannot drift.
`berth_source_status`, `inspect_berth_source_candidate` and `resolve_berth_source` are the Manager operations.

### Three things the runtime said that the handoff did not

**The announcement gate had to come off the investigation path.**
The handoff kept "admissible announcement" as a check on investigation.
Against the runtime that makes the sibling's candidate unreadable, which is the candidate most worth reading:
A's announcement for its own location travels in A's team Core chain, published to the very bucket B cannot use yet, so B holds no announcement naming it.
The gate exists to stop a device *writing* where teammates will not look, and investigation never writes.
It is now recorded as evidence -- `observation.announcement` is `announced`, `missing` or `unknown` -- and does not refuse the read.
`test_both_candidates_are_inspectable_while_the_berth_is_paused` asserts both outcomes in the same run: B's own candidate is `announced`, A's is `missing`, and both are read.

**Resolving where to publish does not resolve what to publish.**
Choosing the sibling's location leaves that location holding the sibling's Core chain, which diverges from this device's local `main`.
`push_team` then raises `PublicationIntegrationRequiredError` naming the head already there, and the ordinary integration path takes over.
That is the correct separation and not a gap, but it means "resolution resumes publication" is true only for a device choosing its own location;
choosing the sibling's is resolution plus reconcile plus a Core integration.
The parametrized `test_either_existing_location_can_be_chosen_and_publication_resumes` asserts both halves.

**An open placement propagates further than the Hub.**
`derive_team_join_state` cannot derive a route from either of several disputed rows, so it gained `placement` and reports no allocation while the question is open.
`get_berth_cloud_allocation_for_berth` raises `BerthSourceAmbiguousError` rather than picking, which surfaces in ordinary management operations:
`create_invitation` raises it while a berth is ambiguous, because an invitation has to name a route and there is none chosen.
That is right, but it is a raised error rather than a reported reason, unlike `reconcile_team_route`, which reports `berth_source_paused`.

### What the pause costs, measured

The channel-wide block is gone.
Under the old refusal one disputed berth stopped the whole NoteToSelf channel in both directions;
`test_competing_choices_pause_the_berth_and_spare_unrelated_work` shows A's unrelated cloud account arriving on B in the same adoption that opens the pause, and B publishing NoteToSelf while the berth is stopped.
What stops is the berth: the team session's `ensure_cloud_ready` and a `push_team` with real outstanding work both refuse with `berth_source_paused`.

### Not covered

Three of the plan's schedules have no runtime evidence and were not attempted:
interrupting adoption between the shared rows and the projected pause;
racing a locator writeback or a new candidate against a resolution's own transaction;
and retrying a publication interrupted after resolution.
The first two are argued from the transaction structure -- one `BEGIN IMMEDIATE` covering comparison and mutation, detection inside adoption's own transaction -- and that argument is not the same as a test.
Restoration of older local state was also not exercised: nothing demonstrates what survives when a device-local database is rolled back to before a pause was recorded.

### Validation of this document edit

Ran `probe_source_resolution.py` (9 passed) and `test_note_to_self_integration.py` (41 passed, 11 of them new).
The repository suite with `uv run pytest` is 964 passed, 3 skipped, 0 failed.
The pre-change baseline was 952 passed with two failures that passed on rerun and did not recur;
the difference is the eleven new micro tests less the unique-index test they replace.
The probes are not collected by the suite -- they are named `probe_*.py` -- so they were run explicitly from their own directory.
No issue messages were sent.

## Latest-commit review fixes, 2026-09-08

The review of `fca4745` reproduced two gaps in investigation.
The Hub combined the retained location with current account route fields, so one candidate key could use different endpoints between object requests.
Inspection also published Git refs before acquiring the NoteToSelf writer reservation; resolution could accept the old digest in that window, and an interruption could leave a head absent from every subsequent report.

Inspection now checks the current account's protocol, URL, client ID and path metadata against the retained snapshot and refuses a mismatch before provider I/O.
Credential refresh remains possible without changing the route, including when inspecting a withdrawn candidate.
Fetching imports and verifies without publishing a ref.
The Manager then publishes an immutable ref for every verified observation and advances the convenience ref under the same NoteToSelf writer reservation used by resolution.
Status and resolution reconstruct source-associated heads from retained refs when report recording was interrupted.
If resolution finished before inspection completed, the later ref remains inspectable without rewriting that decision's evidence or reopening its pause.
The representation stays in NoteToSelf, management policy stays in Manager, and generic Cod Sync gains no placement policy or locking callback.

Validation used temporary-repository Git signing overrides (`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false`).
The initial combined run of `test_berth_source_inspection.py` and `test_note_to_self_integration.py` passed 53 micro tests; after adding the final two inspection schedules, the inspection file alone passed all 14 micro tests.
The existing NoteToSelf integration file accounts for 41 of the combined run.
Explicitly running `probes/probe_source_resolution.py` against local Hub/MinIO passed all nine connected scenarios, with five existing Pydantic settings warnings.
`git diff --check` passed.
The whole repository suite was not rerun for this fix.

The new micro tests exercise all four mutable account route fields with live and withdrawn candidates, permit credential refresh, and verify that a changed route never reaches an adapter.
They exercise resolution on both sides of inspection publication, interruption after the immutable ref and after the convenience-ref update, direct resolution after restart without a preceding status refresh, preservation across a later fetch, and divergent observed heads.
These use real SQLite, Git and Cod Sync with filesystem transport and substituted session/team lookup; account-route checks stub the provider adapter.
The interruption tests unwind a transaction by raising an exception; they do not simulate an operating-system process kill or power loss.

The [implementer checklist](handoff-sibling-disagreement.md#remaining-verification-after-the-latest-commit-review) identifies the remaining adoption interruption, allocation/locator writer race, publication-retry and older-state-restoration schedules.
Each item names its trigger, ordering and required observations.
These remain open and must not be inferred from the passing inspection checks.
