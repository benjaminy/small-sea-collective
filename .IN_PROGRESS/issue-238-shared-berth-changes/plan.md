# Shared berth placement with multiple readable locations

Issue: [#238](https://github.com/benjaminy/small-sea-collective/issues/238).
Branch: `issue-238-shared-berth-changes`.
Updated: 2026-09-08 following the multiple-location and retirement discussion, resequenced after a review of the runtime sites, then again as steps 1-3 finished.
Status: revised research direction and experiment plan; no runtime implementation or wire format is accepted.
Steps 1-3 are done: the runtime path map, the read-side probe and the stop/replay model.
The next work is the human decision on the sibling wedge in step 4, which gates every runtime change; it is not acceptance of the earlier retirement-record gate.

## Direction

A participant's berth can have several known, authenticated cloud locations without a reader first establishing which location won a placement disagreement.
Treat locations as channels carrying device-authored publications.
Separate the locations a reader checks, the destinations a participant chooses for future writes, and a decision to stop using a location.
Try this small amount of multiple-location support as the main candidate for simplifying #238.
Be explicit about what it simplifies: it avoids building the public route-resolution machinery that Move 10 proposed, which the runtime never implemented.
It does not touch the one reproduced defect, the sibling allocation wedge, which remains the primary #238 question and gets its own decision in step 4.
Migration and primary/backup are design checks, not authorization to build those workflows now.

Device trust and publication verification remain enforced boundaries.
Stopping use of a cloud location does not revoke a device, invalidate an otherwise valid publication, or prove that stored data has been erased.
Users commonly do not control their cloud providers; stopping further uploads is the meaningful control they can exercise over future disclosure to that provider.
Retirement must explain possible missed work and discovery limits, but need not prove that an old location will never carry another publication.

The working behavioral proposal is [contract-multiple-locations.md](contract-multiple-locations.md).
It distinguishes the direction agreed in discussion from the narrower defaults proposed for the experiment.
The reasoning and changes to prior assumptions are in [notes.md](notes.md#plan-reframe-2026-09-08-channels-write-placement-and-retirement).

## What changes and what survives

| Earlier assumption or finding | Treatment in this plan |
| --- | --- |
| One participant owns the berth placement; trusted siblings are equals | Retain ownership and equal siblings; multiple locations do not assign ownership to a device. |
| Exactly one location must be selected before a teammate reads | Replace as the working research assumption; read several admissible locations without asserting a winner. |
| Incomplete route ancestry requires a teammate to pause | Drop as a general read prerequisite; retain the limit on what succession claims that evidence can justify. |
| Different locations imply incompatible read choices | Replace; distinct locations can coexist, while their retrieved histories can still require integration or human choice. |
| Teammates need public resolution ancestry to resume | Reopen the need entirely; start the comparison without a public selection DAG or separate retirement-resolution record. |
| Gated versus independent retirement is the next checkpoint | Suspend it; that experiment retires selection branches to establish one route, a different question from stopping channel use. |
| A delayed signature or retry must not undo a deliberate choice | Retain for write placement and an explicit local stop; rediscovering an old readable location is not itself a reversal of a write decision. |
| Two allocation rows block NoteToSelf integration | Retain as a demonstrated runtime defect to account for; reader fan-out alone does not fix it. |
| Frozen route content, honest publication outcomes, preserved alternatives | Retain the properties; reassess each proposed representation against the new behavior. |
| A person can pause their device indefinitely | Retain; do not impose a channel-wide pause merely because several readable locations exist. |
| A location must be proven permanently inactive before retirement | Reject as an obligation; an explicit stop can accept incomplete discovery and later manual recovery. |

The [historical plan](plan-single-route.md) preserves the prior decision ledger, completed sequence and schedule catalog.
Moves 1–9 and the corrected Move 10 models remain evidence under their stated assumptions.
The latest recorded review passes 236 model micro tests; this document edit does not extend that evidence to the new candidate.
The historical actor obligations and contracts are labeled accordingly so their single-route pause rules are not inherited as current requirements.

## Boundaries for the candidate

Manager owns allocation, write-placement and management decisions.
Hub performs all provider I/O and enforces the existing local authorization and peer-route trust boundaries.
Generic Cod Sync must not acquire placement policy or choose a location because its head looks newest.
Core and arbitrary app berths need the same explanation, with Core's role in distributing routes made explicit.

Multiple readable locations do not mean automatic write replication, automatic promotion of a backup, or a single merged provider head.
For the first experiment, each device attempts publication to one locally chosen destination at a time; disconnected siblings may hold different choices.
That is a proposed experiment boundary, not a permanent ban on multiple write destinations.
Receiving a route announcement is not permission to upload to it.
A change in read preference must not silently redirect writes.

Authenticate and retain complete route claims and their provenance before using them under existing routing policy.
A source's availability does not establish freshness, agreement or authority over other sources.
A valid signature alone does not establish authorization in the current team/berth context.
Fetching, validating, preserving and integrating publications remain distinct operations.
Content obtained through an old location receives the same checks as content obtained through a preferred location.

Read-channel coexistence may remove a route disagreement without resolving a data disagreement.
Preserve divergent histories for the existing integration boundary rather than replacing them with a winner chosen by provider response order.
Repeated data can be recognized using validated identities, while contradictory payloads under reused identities remain inspectable.
The runtime investigation must check the actual signing and context-binding boundary instead of assuming it from the conceptual model.

## Next work

The order below puts the runtime trace and a probe before any new model.
A reader that never pauses trivially never pauses; a fresh model of that is predictable, while the runtime already answers most of the read-side questions.
Step 4 is the design decision the branch actually turns on, and it gates the first runtime change.

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
This covers the first, third, fifth, sixth and eighth schedules below with real code and no new model.
Like the earlier probes, it asserts observed behavior, defect or not, and fixes nothing.

### 3. Model only the stop and replay schedules — done

`models/model_read_stops.py`, eleven passing tests; results and the list of dropped Move 10 obligations in [notes.md](notes.md#read-stop-model-result-2026-09-08-replay-missed-work-and-discovery).
The 236 pre-existing model tests were run unchanged and reported separately.

The ninth through eleventh schedules concern a local read stop, its replay, an offline sibling publishing after the stop, and a reader knowing only X.
No runtime exists to probe them, so a small model is appropriate.
Represent only routes held, read stops held, and what each read attempt could observe; no simulator, polling or migration engine.
Do not compare it against the single-route models on shared deliveries.
Instead, list in notes.md which Move 10 obligations the new candidate drops and why, and leave the 236 existing tests running unchanged as evidence for their stated assumptions.

### 4. Decide the sibling wedge before any read fan-out change — open, needs a human

The one reproduced defect on this branch is the `berth_cloud_allocation.berth_id` uniqueness refusal between a participant's own devices.
Multiple readable locations help teammate T and do nothing for siblings A and B.
Before changing the Hub's read path, a human decides between two designs for the sibling side:
separating retained location records from the local write choice so the refusal disappears,
or keeping the refusal as an intended pause and adding a visible, resolvable choice at the write boundary.
The probe evidence from Move 8 and Move 9 is the input; do not assume dropping the constraint supplies choice semantics.
This decision determines whether read fan-out is the right first runtime change at all.

### 5. Evaluate practical retirement and discovery

In the read-side experiment, use an explicit local decision to stop reading X and a separate decision to stop future writes to X.
Keep enough local evidence that replay of the same old announcement does not silently cancel that read stop.
This does not select tombstones, counters, a DAG, shared retirement state or permanent retention as the representation.
A deliberate request to resume reading is distinct from an automatic retry.

Try an offline sibling publishing fresh work to X after T stops reading it.
Expected outcome: T can miss that work; the stop report must not promise completeness, global silence or device revocation.
Show a manual path to inspect X again when its locator and storage are still available, without automatically making X the write destination.
When X is inaccessible, record that recovery is unavailable through that channel; do not promise a recovery protocol.

For a reader initially knowing only X, identify how it could learn Y through already available communication.
Use a signed forwarding announcement or an independently delivered route only as explicitly labeled candidate paths.
If no such path exists, report the discovery limitation instead of assuming the reader knows every location.
A forwarding record need not command retirement, confer new trust or prove that X is permanently silent.
Old-location polling and forwarding are options to compare, not mandatory infrastructure.

### 6. Make a design judgment before expanding

Count state against the runtime as it stands, not against the Move 10 model.
The runtime never implemented a pause or succession ancestry; the selector already ignores succession.
Measured that way, multiple reads add a read set, per-source observations and read stops, and remove no existing code.
The claimed win is avoiding the public route-resolution machinery; state that plainly rather than as a reduction.
Name any work displaced into data integration and any new costs.
Prefer multiple reads if that trade holds while preserving verification, legitimate alternatives and meaningful control of writes.
Reject or narrow it if it recreates the machinery in another component.

Update the candidate contract from the results and recommend a concrete next step.
A material new trust rule, public ancestry requirement, automatic conflict-resolution policy or larger implementation scope requires an explicit argument and human decision.
Routine modeling choices within the stated boundaries do not.

### 7. Validate the surviving design across persistence and runtime

Once the behavioral results are coherent, add restart, replay and restoration of older local state, with precise statements of what evidence survives.
Check a retained read stop separately from a chosen write destination.
If restoration loses knowledge of a stop, show the resulting behavior and the limitation; do not assume an indestructible high-water mark or storage system.
A normal restart or redelivery with retained evidence must not silently undo either explicit choice.

Then extend local runtime probes with real route verification, per-source retrieval, divergent-history preservation, and both NoteToSelf/Core delivery orders.
Choose either sibling's existing write location while preserving unrelated work from both sides and refusing an application to changed reviewed evidence.
Exercise interrupted publication without turning a retry into a new choice.
Only after these checks propose the implementation handoff, schema changes and affected public documentation.
No runtime work is performed by this plan edit.

## Validation: initial schedule set

Each trace must show actor-held evidence, attempted I/O, verified publications, retained alternatives, write destination, stopped reads and user-visible claims after every step.
Check safety separately from conditional progress and record missing knowledge explicitly.

| Schedule | Required observation |
| --- | --- |
| A publishes at X, B at Y; T knows both | Both verified histories can be fetched and retained without choosing a winning route; integration follows its own policy. |
| X fails or stalls while Y is usable, then the reverse | One source's failure does not prevent the other from being attempted under the experiment's bounded I/O; report incomplete coverage. |
| Both answer, but each has unique work | First success does not exclude the other source; distinct work remains available. |
| Both carry the same publication; redelivery in both orders | No duplicate semantic effect; identities and verification justify any deduplication. |
| X contains a stale prefix, Y its descendant | Already-held work is not rolled back; ancestry describes held data, not future silence at X. |
| Missing link or bundle at X, valid chain at Y | Y remains inspectable; incomplete X is reported without splicing unrelated chain heads or claiming complete retrieval. |
| Invalid signature, wrong context, or contradictory payload at one source | Existing verification rejects or isolates the invalid claim; contradictory signed evidence is preserved; the other source is not implicitly trusted or suppressed. |
| Delayed X announcement arrives after choosing Y for writes | Y remains the chosen write destination; read discovery is assessed separately. |
| T explicitly stops reading X, then receives the same X announcement again | The retained local stop remains effective; no claim that other devices have stopped follows. |
| An offline sibling publishes new valid work at stopped X | Work can be missed without becoming invalid; manual inspection can recover it if X remains available. |
| T knows only X while future work moves to Y | Show an actual delivery path for Y or report inability to discover it; no fabricated global route inventory. |
| Siblings disagree on writes and have unrelated NoteToSelf changes | Preserve alternatives and unrelated work in the candidate; identify any real integration refusal still preventing this. |
| A third write choice arrives between review and application | Refuse applying a choice to changed evidence and show the newly relevant alternative. |
| Stop future uploads while provider retains all old bytes | No further uploads from the actor honoring the stop; no erasure claim, key change or device-trust change is inferred. |

Use deterministic local fixtures for the model and local mocks or MinIO for runtime probes.
Check both delivery orders where outcomes could depend on arrival order, and redelivery separately from a fresh deliberate choice.
The runtime probe is itself the control for choosing by latest signature: today's selector does exactly that, and the probe records it.
The stop/replay model needs a control that intentionally reads only the first successful location, demonstrating that the assertions catch it.
Do not add a general fault-injection framework or operational retry machinery for this experiment.

Run the existing model suite unchanged when adding the stop/replay model, and report its results separately from new candidate assertions.
Run the new probe with the existing probe command and record its observations as observed behavior, not as pass/fail of the design.
Use the established `.venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/models/model_*.py -q` command when that environment is available.
For eventual runtime changes, run affected micro tests and the repository suite with `uv run pytest`, recording limitations and blocked checks.
A model result does not establish that current storage, trust verification or transport APIs implement its assumptions.

## Design checks and limits

Migration asks whether reads can overlap X and Y while future writes move, and how a reader discovers Y.
Primary/backup asks whether more than one channel can remain useful without a single winner.
Use those two examples to avoid hard-coding disputed locations as an exceptional error state.
Do not implement data copying, mirroring, automated failover/failback, provider cleanup, polling configuration or management UX for those workflows now.

More sources cost I/O and retained evidence.
The bounded experiment uses two; the handoff must identify how existing trust and resource boundaries apply rather than silently accepting unlimited provider work from route announcements.
This is not a request for a new quota system.
Likewise, channel retirement does not replace signer revocation, encryption/key policy, or ordinary hostile-input validation.

The #224 ownership and trust principles remain: participant ownership, equal siblings, NoteToSelf coordination, and no assumed peer-verifiable selection DAG.
The single-route cardinality is explicitly reopened by this discussion.
Do not equate multiple locations with per-device ownership, and do not treat the old experiments as requiring public resolution disclosure under the new read behavior.
Any eventual issue update must explain exactly which part of #224 changes.

## Scope and handoff

The path map and the read-side probe are delivered; the next deliverable is the decision on the sibling wedge, followed by a recommended minimal contract.
Full provider migration, backup replication, app provisioning UX, the Files capstone and remote erasure remain outside scope.
Practical read retirement and stopping writes are now part of the branch's behavioral scope; automatic provider-object cleanup remains outside it.

Keep substantial discussion and experiment results in [notes.md](notes.md), and revise this plan briefly as steps finish or change.
[follow-up.md](follow-up.md) tracks local proposals for eventual issue updates; no issue messages are authorized by this edit.
A human handles the PR and branch-folder cleanup.
The completed Hub spec corrections remain recorded in [doc-fixes.md](doc-fixes.md).
