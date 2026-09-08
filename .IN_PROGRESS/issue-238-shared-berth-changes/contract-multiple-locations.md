# Working contract: multiple readable locations

Branch: `issue-238-shared-berth-changes`.
Status: behavioral candidate for the next bounded experiment, 2026-09-08.
The user has asked to incorporate the multiple-location reframe and practical retirement into branch planning.
The specific read-stop, publication and discovery rules below are proposed experiment defaults, not an accepted wire format or implemented behavior.
[plan.md](plan.md) defines the sequence, validation and scope.

## Intent

Keep finding a participant's valid updates when its devices disagree about cloud placement, without requiring receiving teammates to resolve that disagreement first.
Several locations can be useful indefinitely.
A reader need not report a winner, prove sibling agreement or reconstruct private selection history to inspect them.

This changes the earlier candidate's read precondition.
It does not claim that a signature establishes reachability, that every known location is safe to use, or that divergent publications can always be integrated automatically.
The ordinary route-trust, publication-verification and application-integration boundaries still apply.

## Concepts kept separate

These are behavioral concepts, not proposed tables or mandatory independent records.

| Concept | Meaning |
| --- | --- |
| Location claim | A device-authenticated statement of complete route content in the relevant participant/berth context, with its provenance retained. |
| Read set | Locations this reader currently chooses to inspect under its trust and local management policy. |
| Read observation | What an attempt at one source established: retrieved and verified content, missing dependencies, failure or unresolved outcome. |
| Write choice | This device's current Manager-controlled destination for publication, which may differ from a disconnected sibling's choice. |
| Read stop | A local decision to stop checking a named location, with an explicit possibility of missing later work there. |
| Write stop | A decision to stop future uploads to a named destination on the device applying it. |

A location claim is not a write command or an assertion that this is the only current location.
A reader's read set is not a complete inventory of the participant's cloud state.
Read preference, if useful later, is scheduling policy and cannot establish retirement or redirect writes.
A write stop does not cancel a request already in flight or control an offline sibling; report those limits if they apply.
How management intent is shared with siblings is part of the write-coordination investigation, not implicit in a local stop.

## Reading and preserving work

For the two-location experiment, inspect both admissible, non-stopped locations even if the first answers successfully.
Bound individual attempts so a failed or stalled source does not prevent inspecting the other.
This is a schedule requirement for the experiment; it does not choose a production polling interval, concurrency scheduler or retry policy.

Keep each chain traversal and its observed head associated with its source.
Use existing validation before treating content as a publication in this berth.
Do not join a head from X to arbitrary objects at Y or treat two mutable provider heads as one CAS domain.
A future use of interchangeable copies would need validation of the exact referenced content; this candidate does not require it.

After verification, identical publications may represent the same work irrespective of where they were retrieved.
Different histories remain available for existing integration policy or human review.
Finding two locations does not itself constitute a data fork; finding a data fork does not make either route unauthentic.
A route's location age, response speed or apparent freshness is not an integration rule.

The experiment must establish what the current verifier actually binds and rejects.
Preserve contradictory claims under reused identities; do not let deduplication overwrite the evidence.
A failed or rejected source does not make other sources trustworthy and does not alone require suppressing independently valid content from them.
No total ordering of location announcements is required merely to read both.

## Choosing and repairing writes

Retain participant ownership and equal trusted siblings.
For the experiment, a device uses one local write destination per publication attempt; there is no automatic replication or failover.
A sibling that has not received a new write choice may continue using its old choice.
Multiple readable locations make that work potentially discoverable; they do not establish sibling agreement.

Changing the write destination is a Manager decision.
An old announcement arriving, a read succeeding elsewhere, a signature being repaired or a failed publication being retried does not make that decision.
Repair signs the claim it has adopted with unchanged route content rather than deriving new content from mutable account rows.
Provider setup and completion remain separately observable from signing, publication and adoption.

When a person resolves a known write disagreement, identify the alternatives and the relevant evidence reviewed.
Refuse applying the choice if newly relevant evidence changes that disagreement before application.
Retain unrelated sibling work and the alternatives needed to explain the result.
The first model can represent this operation abstractly; the path map must expose how the existing unique-allocation merge refusal obstructs it in runtime.

Pausing unresolved writes is available where the device cannot justify a choice.
Multiple read locations alone do not justify stopping all NoteToSelf or team publication.
Whether the actual storage representation still imposes a broader pause must be demonstrated, not concealed by the abstract model.

## Retirement is a decision about channel use

Use the terms stop reading and stop writing whenever that is the actual effect.
Neither action revokes a signing device, erases a provider's retained bytes, nor establishes that another device has adopted the same decision.
A valid publication discovered later at a stopped location is evaluated under ordinary trust and integration rules.
It is not rejected merely because its transport was retired.

The minimal experiment uses a retained local read stop.
Redelivering the same old claim does not silently reactivate that location while the stop evidence is retained.
Keep its locator available for deliberate inspection; inspecting old work does not itself resume writes there.
The eventual representation must say how it recognizes the stopped location, including a fresh attestation of the same route, an intentional return to that location and account/endpoint distinctions.
Do not use an announcement's new signing identity as sufficient evidence that the user intended to undo a stop.
The experiment can use explicit X/Y identities while marking this as a runtime binding obligation.

A reader may stop checking X even though it cannot prove that no more valid work will appear there.
The consequence is possible missed work, including work from an honest offline sibling.
The report should say what this device will stop doing and what it may miss, without implying a global loss of authority.
Manual recovery is conditional on someone learning of the work and the relevant bytes still being available.
No automatic eventual recovery is promised.

Stopping writes controls future uploads by the actor honoring the decision.
A provider can retain earlier uploads; the common assumption is that users cannot control that retention.
Deletion, proof of erasure, key rotation and device revocation are not retirement prerequisites or effects in this contract.
This distinction does not weaken ordinary verification or credential handling.

## Discovery and transition

Trying all locations means all admissible locations the reader knows and has not stopped checking.
If T knows only X and Y is announced only at Y, this contract provides no way for T to discover Y.
Show that failure explicitly.

A candidate transition can publish a signed route announcement for Y through X while X remains usable, or deliver it through another already available channel.
The receiver must apply the ordinary trust rules; a provider-controlled redirect alone cannot create route authority.
A forwarding claim supplies discovery information without proving that X is silent or requiring immediate retirement.
Failure to update X can require another communication path or human assistance.
Do not turn this experiment into a guaranteed migration protocol.

Overlapping reads during migration and keeping both primary and backup readable fit this vocabulary.
Copying old data, synchronizing replicas, electing a primary and switching writers after failure require additional behavior and are outside this candidate.
A backup that is stale is still a source of verified historical work, not evidence that its head should replace newer held state.

## Claims and acceptance criteria

A report may say which locations were inspected, which failed or were skipped by choice, what valid work was found, what remains unintegrated and where this device will next attempt a write.
It may not say all work was found, siblings agreed, a location can never publish again, or a provider erased data without evidence that actually establishes that claim.
Lack of route ancestry limits succession claims rather than automatically forbidding reads.

The candidate succeeds if the plan's schedules preserve legitimate alternatives and explicit choices while removing the need for receiving teammates to resolve route succession before reading.
Count the state required for that result and identify any costs shifted into integration or discovery.
A second list of endpoints with the same public resolution graph hidden underneath does not establish the proposed simplification.
Failures or new trust requirements should produce a narrower proposal or a concrete counterexample, not an expanding protocol by default.
