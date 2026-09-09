# Working contract: evidence gathering during a human pause

Branch: `issue-238-shared-berth-changes`.
Status: accepted behavioral direction, 2026-09-08; representation and runtime implementation remain open.
The user accepted narrowing multiple-location support to evidence gathering while ordinary operations wait for a human decision.
[plan.md](plan.md) defines the sequence, validation and scope.

## Intent

When a device detects an unresolved question about which sources to use for a berth, pause its normal operations for that berth until a human decides.
The Hub and Manager may continue fetching, verifying, comparing and preserving relevant evidence from admissible sources.
Investigation and the actions needed to resolve the pause remain available.
Evidence gathering does not require choosing a winning location, proving sibling agreement or reconstructing private selection history.

This narrows the earlier candidate, which explored continued normal reading across multiple locations during a placement disagreement.
Automatic multi-location operation is deferred.
The ordinary route-trust, publication-verification and application-integration boundaries still apply.

## Concepts kept separate

These are behavioral concepts, not proposed tables or mandatory independent records.

| Concept | Meaning |
| --- | --- |
| Location claim | A device-authenticated statement of complete route content in the relevant participant/berth context, with its provenance retained. |
| Evidence sources | Known locations relevant to the unresolved question and admissible under existing trust and local management policy. |
| Read observation | What an attempt at one source established: retrieved and verified content, missing dependencies, failure or unresolved outcome. |
| Write choice | This device's current Manager-controlled destination for publication, which may differ from a disconnected sibling's choice. |
| Read stop | A local decision to stop checking a named location, with an explicit possibility of missing later work there. |
| Write stop | A decision to stop future uploads to a named destination on the device applying it. |

A location claim is not a write command or an assertion that this is the only current location.
A reader's evidence sources are not a complete inventory of the participant's cloud state.
Read preference, if useful later, is scheduling policy and cannot establish retirement or redirect writes.
A write stop does not cancel a request already in flight or control an offline sibling; report those limits if they apply.
How management intent is shared with siblings is part of the write-coordination investigation, not implicit in a local stop.

## Detecting, holding and resolving the pause

Uncertainty means a detected unresolved question about source use, not the absence of proof that every possible location has been discovered.
Several sources deliberately accepted by a human need not themselves constitute uncertainty.
A failed request alone establishes an unavailable source, not that another source is authorized or should replace it.
The runtime handoff must name the observations that trigger the pause instead of claiming to detect unknown locations.

The pause covers normal reads, integration and publication for the affected berth on the detecting device.
It does not claim to stop an offline sibling or establish a team-wide decision.
If the storage representation blocks other work too, make that broader pause visible and preserve the blocked work.
Building isolation machinery solely to keep unrelated work moving is not required by this branch.

Further evidence may explain or narrow the problem, but does not release the pause automatically, even when it removes the apparent disagreement.
Resumption requires an explicit human choice over the relevant reviewed evidence, and any remaining independent block must still be honored.
That choice may authorize one source or several without establishing a universally winning location.
The branch's first complete resolution path must support choosing either sibling's existing location; normal multi-source operation remains deferred.

## Reading and preserving work

The Hub performs all provider I/O, including investigation requested by the Manager.
Evidence gathering is permitted, not an obligation to poll or retry continually while paused.
For the two-location validation, demonstrate that both alternatives can be inspected even if the first answers successfully or fails.
Report unavailable or incomplete evidence without requiring every source to answer before a human can decide.
Existing authorization, device trust and explicit read stops continue to apply; relevance alone does not override them.

Keep each chain traversal and its observed head associated with its source.
Use existing validation before treating content as a publication in this berth.
Do not join a head from X to arbitrary objects at Y or treat two mutable provider heads as one CAS domain.
A future use of interchangeable copies would need validation of the exact referenced content; this branch does not require it.

After verification, identical publications may represent the same work irrespective of where they were retrieved.
Different histories remain preserved outside live integration while the pause is held.
Fetching evidence does not integrate it, redirect writes or cancel an explicit stop.
Finding two locations does not itself constitute a data fork; finding a data fork does not make either route unauthentic.
A route's location age, response speed or apparent freshness is not an integration rule.

The experiment must establish what the current verifier actually binds and rejects.
Preserve contradictory claims under reused identities; do not let deduplication overwrite the evidence.
A failed or rejected source does not make other sources trustworthy or prevent preserving independently valid evidence from them.
No total ordering of location announcements is required merely to read both.

## Choosing and repairing writes

Retain participant ownership and equal trusted siblings.
The first complete path resumes publication to one explicitly chosen existing location; there is no automatic replication or failover.
A sibling that has not received a new write choice may continue using its old choice.
Evidence gathering can discover that work; it does not establish sibling agreement.

Changing the write destination is a Manager decision.
An old announcement arriving, a read succeeding elsewhere, a signature being repaired or a failed publication being retried does not make that decision.
Repair signs the claim it has adopted with unchanged route content rather than deriving new content from mutable account rows.
Provider setup and completion remain separately observable from signing, publication and adoption.

When a person resolves a known write disagreement, identify the alternatives and the relevant evidence reviewed.
Refuse applying the choice if newly relevant evidence changes that disagreement before application.
Retain unrelated sibling work and the alternatives needed to explain the result.
The runtime walkthrough must show that either sibling's existing location can be selected without a raw database edit or an unnecessary third allocation.

The existing unique-allocation refusal is not sufficient implementation of this contract.
Its alternatives must become inspectable, the pause must be enforced and reported, and the choice must actually take effect.
Whether to retain that constraint or change the storage representation is an implementation question judged against those requirements.

## Retirement is a decision about channel use

Use the terms stop reading and stop writing whenever that is the actual effect.
Neither action revokes a signing device, erases a provider's retained bytes, nor establishes that another device has adopted the same decision.
A valid publication discovered later at a stopped location is evaluated under ordinary trust and integration rules.
It is not rejected merely because its transport was retired.

An explicit local read stop remains effective while its evidence is retained.
Redelivering the same old claim does not silently reactivate that location while the stop evidence is retained.
Keep its locator available for deliberate inspection authorized by the human; a one-time inspection does not itself resume routine reads or writes there.
The eventual representation must say how it recognizes the stopped location, including a fresh attestation of the same route, an intentional return to that location and account/endpoint distinctions.
Do not use an announcement's new signing identity as sufficient evidence that the user intended to undo a stop.
The existing stop model uses explicit X/Y identities and does not establish runtime enforcement.

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

Evidence gathering can inspect known admissible sources; it does not supply a complete location inventory.
If T knows only X and Y is announced only at Y, this contract provides no way for T to discover Y.
Report that limitation; it does not require pausing every reader merely because undiscovered locations could exist.

A candidate transition can publish a signed route announcement for Y through X while X remains usable, or deliver it through another already available channel.
The receiver must apply the ordinary trust rules; a provider-controlled redirect alone cannot create route authority.
A forwarding claim supplies discovery information without proving that X is silent or requiring immediate retirement.
Failure to update X can require another communication path or human assistance.
These are possible delivery paths, not a migration protocol required by this branch.

Several locations can remain useful without being in dispute, but normal multi-location reading, migration and primary/backup workflows are deferred.
Copying old data, synchronizing replicas, electing a primary and switching writers after failure require additional behavior and are outside this branch.
A backup that is stale is still a source of verified historical work, not evidence that its head should replace newer held state.

## Claims and acceptance criteria

A report must identify the unresolved question, the affected operations and any broader storage-imposed pause.
It may say which locations were inspected, which failed or were skipped by choice, what valid work was found, what remains unintegrated and what source and write choices the human can make.
While paused, a retained write destination is not permission for normal publication to continue.
It may not say all work was found, siblings agreed, a location can never publish again, or a provider erased data without evidence that actually establishes that claim.
Lack of route ancestry limits succession claims rather than automatically forbidding reads.

The branch succeeds with one complete path: detect the disagreement, visibly pause, inspect and preserve both sides, let the human choose either existing location, and resume without losing unrelated work.
Restart, replay and newly relevant evidence must not silently release the pause or reverse an explicit choice.
This avoids requiring automatic progress through disagreement, public route-resolution ancestry or proof of global retirement.
It still requires concrete detection, evidence preservation, pause enforcement and effective resolution; the existing models and probes do not establish that implementation.
