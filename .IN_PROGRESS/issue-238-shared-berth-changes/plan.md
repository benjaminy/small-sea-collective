# Shared berth placement: evidence gathering and human resolution

Issue: [#238](https://github.com/benjaminy/small-sea-collective/issues/238).
Branch: `issue-238-shared-berth-changes`.
Updated: 2026-09-08 after the latest-commit review fixes.
Status: the connected resolution path is implemented; the remaining verification work is listed in the [implementer checklist](handoff-sibling-disagreement.md#remaining-verification-after-the-latest-commit-review).
The inspection/ref-publication race and frozen-account-route defects found in review are fixed, with focused regression coverage.
Steps 1-3 are done: the runtime path map, the read-side probe and the stop/replay model.
Step 4's handoff is [handoff-sibling-disagreement.md](handoff-sibling-disagreement.md).
It adopts removing allocation uniqueness and deleting losing live candidates while retaining evidence locally.
Step 5 is done except for the three schedules listed under it.
Results, and the three places the runtime contradicted the handoff, are in [notes.md](notes.md#step-5-result-2026-09-08-the-path-implemented-and-validated).

## Direction

When a device detects an unresolved question about which sources to use for a berth, its normal operations for that berth pause until a human decides.
The Hub and Manager may continue gathering, verifying, comparing and preserving relevant evidence from admissible sources, with all provider I/O through the Hub.
Further evidence does not automatically release the pause.
Treat locations as channels carrying device-authored publications.
Separate the locations a reader checks, the destinations a participant chooses for future writes, and a decision to stop using a location.
This narrows the previous multiple-location candidate to investigation and human repair during uncertainty.
It avoids requiring automatic multi-location operation or the public route-resolution machinery proposed by Move 10, which the runtime never implemented.
The primary #238 target remains the sibling allocation wedge: turn the refusal into a visible pause with an effective choice of either existing location, preserving unrelated work.

Device trust and publication verification remain enforced boundaries.
Stopping use of a cloud location does not revoke a device, invalidate an otherwise valid publication, or prove that stored data has been erased.
Users commonly do not control their cloud providers; stopping further uploads is the meaningful control they can exercise over future disclosure to that provider.
Retirement must explain possible missed work and discovery limits, but need not prove that an old location will never carry another publication.

The behavioral contract is [contract-multiple-locations.md](contract-multiple-locations.md).
The accepted simplification and its relationship to the earlier proposal are recorded in [notes.md](notes.md#accepted-simplification-2026-09-08-evidence-gathering-during-a-human-pause).

## What changes and what survives

| Earlier assumption or finding | Treatment in this plan |
| --- | --- |
| One participant owns the berth placement; trusted siblings are equals | Retain ownership and equal siblings; multiple locations do not assign ownership to a device. |
| Exactly one location must be selected before a teammate reads | Evidence gathering may inspect several admissible sources without choosing a winner; normal operations wait during detected uncertainty. |
| Incomplete route ancestry requires a teammate to pause | Do not require a public ancestry graph; detect unresolved source-use questions from available evidence without demanding proof of a complete inventory. |
| Different locations imply incompatible read choices | Several deliberately accepted sources need not be in dispute; automatic multi-location operation is deferred. |
| Teammates need public resolution ancestry to resume | Use explicit local human resolution; do not require a public selection DAG or separate retirement-resolution record. |
| Gated versus independent retirement is the next checkpoint | Suspend it; that experiment retires selection branches to establish one route, a different question from stopping channel use. |
| A delayed signature or retry must not undo a deliberate choice | Retain for write placement and an explicit local stop; rediscovering an old readable location is not itself a reversal of a write decision. |
| Two allocation rows block NoteToSelf integration | Make the disagreement visible and resolvable; neither the existing refusal nor read fan-out alone satisfies the contract. |
| Frozen route content, honest publication outcomes, preserved alternatives | Retain the properties; reassess each proposed representation against the new behavior. |
| A person can pause their device indefinitely | Retain; disclose any broader storage-imposed pause and preserve blocked work rather than requiring automatic progress or isolation machinery. |
| New evidence can clear a pause automatically | Replace for a source-use pause: evidence improves the explanation, but a human must explicitly authorize resumption. |
| A location must be proven permanently inactive before retirement | Reject as an obligation; an explicit stop can accept incomplete discovery and later manual recovery. |

The [historical plan](plan-single-route.md) preserves the prior decision ledger, completed sequence and schedule catalog.
Moves 1–9 and the corrected Move 10 models remain evidence under their stated assumptions.
The recorded historical model suite has 236 passing micro tests; this document edit does not extend that evidence to the accepted pause contract.
The historical actor obligations and contracts are labeled accordingly so their single-route pause rules are not inherited as current requirements.

## Boundaries

Manager owns allocation, write-placement and management decisions.
Hub performs all provider I/O and enforces the existing local authorization and peer-route trust boundaries.
Generic Cod Sync must not acquire placement policy or choose a location because its head looks newest.
Core and arbitrary app berths need the same explanation, with Core's role in distributing routes made explicit.

Multiple readable locations do not mean automatic write replication, automatic promotion of a backup, or a single merged provider head.
For the first complete resolution path, the device resumes publication to one explicitly chosen existing destination; disconnected siblings may hold different choices.
While paused, a retained destination does not authorize normal publication.
Receiving a route announcement is not permission to upload to it.
A change in read preference must not silently redirect writes.

Authenticate and retain complete route claims and their provenance before using them under existing routing policy.
A source's availability does not establish freshness, agreement or authority over other sources.
A valid signature alone does not establish authorization in the current team/berth context.
Fetching, validating, preserving and integrating publications remain distinct operations.
Content obtained through an old location receives the same checks as content obtained through a preferred location.
Investigation does not override an explicit read stop; a human may authorize deliberate inspection without resuming routine use.

Keep evidence outside live integration while paused.
Preserve divergent histories rather than replacing them with a winner chosen by provider response order.
Repeated data can be recognized using validated identities, while contradictory payloads under reused identities remain inspectable.
The runtime investigation must check the actual signing and context-binding boundary instead of assuming it from the conceptual model.

## Next work

Steps 1-3 are completed evidence from the previous candidate, not validation of the new pause contract.
Step 4 turns the accepted behavioral direction into a concrete runtime handoff; step 5 defines what must convince a reviewer that the path works.

### 1. Record the runtime path map — done

Recorded in [notes.md](notes.md#runtime-path-map-2026-09-08-where-multiple-locations-would-land); the probe in step 2 confirmed it and corrected the claim about the divergent head.

Write the map as a notes.md section from the sites already identified there.
It must name the single-route choice point, the two Hub consumers, Cod Sync's source association and its fetch-divergence gap, what the announcement signature binds, and the allocation uniqueness wedge.
Keep it to about a page; it is a map for the probe, not a design.

### 2. Probe the read side against the runtime — done

`probes/probe_multiple_locations.py`, ten passing tests; results in [notes.md](notes.md#multiple-location-probe-result-2026-09-08-the-runtime-with-two-readable-locations), corrected by [the review follow-through](notes.md#multiple-location-review-follow-through-2026-09-08-source-binding-fallback-and-the-sender-key).
A chain walk turned out not to be bound to the location it started at, the fallback schedule was rewritten to keep the alternative announcement installed, and the sender-key ordering is now a test rather than a setup step.
The reader is device A rather than a third participant, and the probe records why.
It also found a boundary the map missed: a sibling's location is unreadable unless that sibling's sender key was redistributed before it published.

Write it reusing the two-installation fixtures from the Move 7–9 probes.
Sibling A announces X and sibling B announces Y for one berth; teammate T selects and reads.
Assert what the runtime does today: T's selection is the announcement with the higher id, and T never attempts the other location.
That is the "latest signature wins" control the plan requires to be visible.

Then fetch both stores through Cod Sync as two `CodSync` instances with a pin ref and record what divergence leaves behind: the exception's SHAs, which objects were imported, and whether any ref points at the second head.
Cover identical history, divergent history, and a missing bundle at one location.
This records retrieval, divergence and missing-source behavior with real code and no new model.
Like the earlier probes, it asserts observed behavior, defect or not, and fixes nothing.

### 3. Model only the stop and replay schedules — done

`models/model_read_stops.py`, eleven passing tests; results and the list of dropped Move 10 obligations in [notes.md](notes.md#read-stop-model-result-2026-09-08-replay-missed-work-and-discovery).
The 236 pre-existing model tests were run unchanged and reported separately.

The modeled schedules concern a local read stop, its replay, an offline sibling publishing after the stop, and a reader knowing only X.
No runtime exists to probe them, so a small model is appropriate.
Represent only routes held, read stops held, and what each read attempt could observe; no simulator, polling or migration engine.
Do not compare it against the single-route models on shared deliveries.
Instead, list in notes.md which Move 10 obligations the new candidate drops and why, and leave the 236 existing tests running unchanged as evidence for their stated assumptions.

### 4. Prepare the complete human-resolution path — revised after review

The handoff is [handoff-sibling-disagreement.md](handoff-sibling-disagreement.md).
It names atomic pause detection, retained device-local evidence, Hub enforcement, investigation during the pause and resolution over the reviewed evidence.
The handoff removes `idx_berth_cloud_allocation_berth` and deletes losing live allocations on explicit resolution.
Old local choices never override new ambiguity, and shared deletion does not clear a sibling's held pause or erase its inspection evidence.
The committee response, rationale and four review fixes are recorded in [notes.md](notes.md#step-4-review-follow-through-2026-09-08).

#### Requirements the handoff had to meet

The user accepted pausing normal operations during detected uncertainty while investigation continues.
The previous checkpoint asking whether sibling disagreement may intentionally pause is settled at the behavioral level.
The reviewed handoff chooses multiple live candidates during disagreement and one live allocation after resolution.
Implement the minimum path that makes the pause visible and enforceable and lets either existing location be selected.

Use Move 8 and Move 9 as the starting evidence.
Today the refusal parks alternatives but does not explain the berth disagreement, blocks the whole NoteToSelf channel, and lacks an effective Manager choice of either existing allocation.
A generic refusal or a raw database edit is not the intended resolution interface.

The handoff must identify:

- The concrete observations that establish an unresolved source-use question, and where the Hub and Manager enforce the pause on normal reads, integration and publication within their existing responsibilities.
- How the Manager presents the question, the alternatives, unavailable evidence and the actual scope of blocked work.
- How investigation fetches and preserves relevant alternatives through the Hub without live integration, source switching within a chain walk, write redirection or cancellation of explicit stops.
- How the human chooses either existing location over the reviewed evidence, and how the choice is refused if newly relevant evidence changes the question before application.
- How resolution resumes the affected operations and preserves unrelated work from both siblings, without claiming agreement by devices that have not participated.

Evidence gathering and the operations needed to apply the human resolution are exceptions to the pause.
No continuous retries, automatic integration through disagreement, or new machinery to isolate unrelated blocked work are required.
If the storage design imposes a broader pause, name it and account for the preserved work when resolution completes.
Do not require every source to answer before a person can act with incomplete evidence explicitly reported.

### 5. Implement and validate the complete path -- done except where noted

Implemented across the Manager, the Hub and the NoteToSelf schema; the named runtime changes are listed in [notes.md](notes.md#what-landed).
Validated by `probes/probe_source_resolution.py` (nine connected scenarios against a real Manager, Hub and MinIO) and eleven new micro tests in `packages/small-sea-manager/tests/test_note_to_self_integration.py`, which replace the unique-index test the change removes.
The affected public specs are updated.

Three schedules in the table below have no runtime evidence and were not attempted: adoption interrupted between the shared rows and the projected pause, a locator writeback or new candidate racing resolution's own transaction, and a publication interrupted after resolution.
They are argued from the transaction structure, which is not the same as a test.
Restoration of older local state was not exercised either.
The follow-up inspection micro tests cover observation publication versus resolution, ref/report interruption, and changed account routes; they do not close those four remaining checks.
Use the [remaining verification checklist](handoff-sibling-disagreement.md#remaining-verification-after-the-latest-commit-review) for concrete schedules and acceptance observations.

Two results differ from what the table expected.
Choosing the sibling's location resumes publication only as far as the placement: that location holds the sibling's Core chain, so `push_team` reports the ordinary divergence and integration follows.
And investigation is not gated on an admissible announcement, because the sibling's announcement travels in a chain published to the location this device cannot reach yet; announcement status is recorded as evidence instead.

The original obligations follow, unchanged.

Extend the existing local runtime probes to demonstrate detection, visible pause, investigation, choice and resumption in one connected scenario.
An isolated model of a pause flag is not sufficient evidence that actual callers stop or that a human choice takes effect.
Use both NoteToSelf/Core delivery orders and choose each sibling's existing location in separate runs.
Record the user-visible question, held alternatives, attempted I/O, live state, pause state and write destination at the relevant transitions.

| Schedule | Required observation |
| --- | --- |
| Siblings make competing choices and also have unrelated NoteToSelf changes | The disagreement becomes visible; normal operations covered by the pause stop; both alternatives and unrelated work survive. |
| Inspect X and Y while paused, with unique work at each | Both remain inspectable through the Hub; evidence is retained without live integration or publication. |
| X fails or is incomplete while Y is usable, then the reverse | The other source remains inspectable; missing evidence is reported; success elsewhere does not resume normal operations. |
| A new announcement arrives during a chain walk | The walk remains associated with its intended source; a later source is a separate observation. |
| Invalid signature, wrong context, or contradictory payload | Existing verification rejects or isolates invalid claims and preserves contradictory evidence without bypassing authorization. |
| New evidence shows identical history or removes the apparent disagreement | The explanation may change; the pause stays held until a human explicitly decides. |
| Human chooses X, then a separate run chooses Y | Either existing location can be selected through the Manager without raw database edits or a third allocation; resolution preserves unrelated work and resumes only when other blocks permit. |
| Newly relevant evidence arrives between review and application | The stale choice is refused and the new alternative becomes visible. |
| An earlier choice names X, then adoption adds Z; interrupt before or after the adoption transaction commits | Shared candidates and the held pause become visible together; no ordinary Hub call uses the old choice to bypass the new disagreement. |
| A new candidate, changed locator/account route, or investigation head races resolution | Comparison and mutation are serialized; an earlier change invalidates the reviewed digest, while a later change is processed after resolution and cannot be silently deleted by it. |
| A paused sibling adopts deletion of one candidate, then restarts | The pause stays held; both saved routes remain inspectable through the Hub, and the report distinguishes live from withdrawn candidates. |
| The human deliberately selects a retained, withdrawn candidate | Restore only the reviewed allocation with matching existing account context; refuse conflicting identity or missing account without clearing the pause or creating provider resources. |
| Inspect a withdrawn candidate while normal I/O is paused | Authorized inspection bypasses the pause and live-row lookup, retains credential and announcement checks, and never materializes, redirects the walk or integrates the fetched history. |
| Normal restart or replay while paused, and after resolution | Retained evidence keeps the pause or explicit choice effective; replay is not a new choice or permission to resume. |
| A fresh disagreement arrives after resolution | It is detected and may pause again; no global agreement is inferred from the prior local choice. |
| Publication is interrupted after resolution | Retry preserves the chosen content and destination rather than manufacturing another choice. |
| An explicit read stop is replayed or an old location is inspected deliberately | Replay does not cancel the stop; authorized inspection alone does not resume routine reads or writes there. |

Exercise restoration of older local state with precise statements of what evidence survives.
If restoration loses knowledge of a pause or stop, demonstrate and report the limitation instead of assuming indestructible state or adding a recovery system.
The existing stop/discovery model remains evidence that valid work can be missed at a stopped source and that an unknown location cannot be discovered without a delivery path.
Neither limitation requires proving global silence, complete discovery or provider erasure before a person can choose.

Use local mocks or MinIO, deterministic schedules, both relevant delivery orders and replay controls.
Run affected micro tests and the repository suite with `uv run pytest` for runtime changes, recording limitations and blocked checks.
Rerun relevant existing models if their assumptions or implementation change; do not present historical passing results as validation of this contract.
(That obligation is now discharged: this branch does carry runtime changes and new micro tests.)

## Design checks and limits

Several sources deliberately accepted by a human need not themselves be in dispute.
That semantic allowance does not require implementing normal multi-location operation in this branch.
Migration, primary/backup, copying, mirroring, automatic failover/failback, forwarding infrastructure and polling policy remain deferred.
Minimal-fuss provider migration remains an eventual goal; deleting allocation rows does not implement migration or provider erasure.
Additional security checks comparing old and incoming placement state during adoption, potentially involving a human, remain future work.

More sources cost I/O and retained evidence.
The bounded experiment uses two; the handoff must identify how existing trust and resource boundaries apply rather than silently accepting unlimited provider work from route announcements.
This is not a request for a new quota system.
Likewise, channel retirement does not replace signer revocation, encryption/key policy, or ordinary hostile-input validation.

The #224 ownership and trust principles remain: participant ownership, equal siblings, NoteToSelf coordination, and no assumed peer-verifiable selection DAG.
The single-route cardinality is explicitly reopened by this discussion.
Do not equate multiple locations with per-device ownership, and do not treat the old experiments as requiring public resolution disclosure under the new read behavior.
Any eventual issue update must explain exactly which part of #224 changes.

## Scope and handoff

The branch completion target -- detecting and visibly pausing, inspecting and preserving both sides, choosing either existing location, and resuming without losing unrelated work -- is met and has runtime evidence.
What remains is a human review of the implementation, the three unvalidated schedules, and the issue updates [follow-up.md](follow-up.md) proposes.
Automatic multi-location operation, global agreement, public resolution ancestry, elaborate retirement mechanisms, full provider migration, backup replication, app provisioning UX, the Files capstone and remote erasure remain outside scope.
Retain the distinction between stopping reads, stopping writes and revoking devices; honor explicit stops without requiring a general retirement workflow.
The affected public specs are updated: the Manager spec's one-allocation rule and a new "Berth placement disagreement" section, and the Hub spec's own-berth resolution passage.

Keep substantial discussion and experiment results in [notes.md](notes.md), and revise this plan briefly as steps finish or change.
[follow-up.md](follow-up.md) tracks local proposals for eventual issue updates; no issue messages are authorized by this edit.
A human handles the PR and branch-folder cleanup.
The completed Hub spec corrections remain recorded in [doc-fixes.md](doc-fixes.md).
