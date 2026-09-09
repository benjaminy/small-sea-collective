# Implementation handoff: sibling berth disagreement and human resolution

Branch: `issue-238-shared-berth-changes`, plan [step 4](plan.md#4-prepare-the-complete-human-resolution-path--revised-after-review).
Status: implemented for the demonstrated schedules, with review fixes applied; the [remaining verification checklist](#remaining-verification-after-the-latest-commit-review) is still open.
Three points where the runtime contradicted this document are recorded in [notes.md](notes.md#three-things-the-runtime-said-that-the-handoff-did-not);
the most significant is that piece 4's admissible-announcement check had to come off the investigation path.
The handoff adopts removing allocation uniqueness and deleting losing live candidates on resolution.
The user's response and the reasons for these choices are recorded in [notes.md](notes.md#step-4-review-follow-through-2026-09-08).

The behavioral contract is [contract-multiple-locations.md](contract-multiple-locations.md).
This document turns it into named runtime changes, and says which are forced by the contract and which are the cheapest way to satisfy it among alternatives.

## What the runtime does today

Established by Moves 8 and 9 and the 2026-09-08 probes; every claim below names the code.

A berth has at most one allocation: `idx_berth_cloud_allocation_berth` in `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql:51`.
Two siblings that each rotate the same berth produce two allocation rows in one merged state, so `apply_delta` raises `sqlite3.IntegrityError` and `_apply_source_rows` (`packages/small-sea-manager/small_sea_manager/note_to_self_sync.py:525`) refuses the whole source as `constraint_refused` with SQLite's message as its detail.

Three consequences follow, and each is a requirement this handoff has to meet.

The refusal names a commit, not a disagreement.
`note_to_self_conflict_status` (`manager.py:426`) reports a ref name and a SHA;
`core_storage_allocation` (`manager.py:1118`) reports this device's own row and `route: ready`.
The competing allocation is real but lives inside a parked commit's `core.db` blob.

The refusal blocks the whole channel, not the disputed berth.
Adoption is per source and all-or-nothing, so an unrelated `cloud_storage` row from the sibling is refused with the allocation, and this device's own unrelated rows cannot be published either, because `push_note_to_self` (`manager.py:332`) is refused by Cod Sync's divergence check first.

The refusal does not pause the operations the disagreement is actually about.
`push_team` keeps publishing to this device's own location, because `_require_own_storage_announcement` (`packages/small-sea-hub/small_sea_hub/backend.py:1710`) falls back to the current device's own signed announcement, and `_resolve_berth_cloud_or_raise` (`backend.py:1035`) still resolves exactly one row.

## The shape of the change

Five pieces, in the order the contract asks for them.

### 1. Detection

The observation that opens a source-use question is domain state, not an error string:
**the participant's NoteToSelf holds more than one `berth_cloud_allocation` row for one berth.**
Resolution leaves one live allocation, so an earlier local choice never exempts a new set of live candidates from detection.

That observation is available at four points, and detection runs at all four because each is a place new evidence lands:

- the adoption path shared by `note_to_self_sync.integrate` (`note_to_self_sync.py:613`) and refresh, before each source's row transaction commits;
- `Manager.refresh_note_to_self` (`manager.py:374`), which adopts through the same path;
- `resolve_berth_cloud_allocation_intent` (`provisioning.py:6229`), which is how this device's own deliberate rotation can land beside a sibling's;
- the status operation in piece 4, which refreshes the projection under a writer reservation before reporting it.

In `_apply_source_rows`, project the resulting allocations and write any pause and retained candidates using the same attached NoteToSelf connection and transaction as adoption.
Do the same inside the local allocation-change transaction.
Capture relevant pre-change rows before applying a deletion or replacement when a pause is already held, and retain both sides when the resulting state opens a pause.
Commit the shared rows and device-local pause together; no post-commit callback supplies the enforcement guarantee.
A crash before commit exposes neither change, and a crash after commit leaves the pause durable even if recording the Git merge has not finished.

Detection is a Manager concern.
`splice_merge` stays generic and learns nothing about berths; the per-berth projection belongs in a new Manager module, `berth_source_decision.py`, next to `note_to_self_sync.py` rather than inside it.

What detection cannot say: which sibling device introduced which row.
`berth_cloud_allocation` has no author column, and Cod Sync proves a history's structure, not its author (`note_to_self_sync.py` module docstring, #190).
The report can name retained heads containing a row and its stored `created_at`; it must not name a device or claim an earliest origin it has not established.
Locally observed rows not yet recorded in Git are labeled as local snapshots without an invented head SHA.

### 2. Pause state

A held pause is durable device-local state.
It cannot be derived from live rows and refs alone, and the reason is a contract requirement: new evidence that removes the apparent disagreement — a sibling that adopted this device's choice, or deleted its own row — must not release the pause.
A predicate over current state releases exactly then.

Add to `device_local_schema.sql`, bumping `LOCAL_SCHEMA_VERSION`:

```sql
CREATE TABLE IF NOT EXISTS berth_source_pause (
    berth_id BLOB PRIMARY KEY,
    evidence_digest BLOB NOT NULL,
    evidence_json TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS berth_write_choice (
    berth_id BLOB PRIMARY KEY,
    allocation_id BLOB NOT NULL,
    evidence_digest BLOB NOT NULL,
    evidence_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
```

Both records are device-local.
The pause belongs to this device, and `berth_write_choice` records the human decision and reviewed evidence on this device.
It is not a routing override: the single surviving shared allocation determines the destination after resolution.

`evidence_json` retains a versioned report snapshot, including complete candidate allocation rows, their account route fields, provenance, source-associated observed heads and investigation outcomes.
It contains no credentials.
Preserve distinct route snapshots even when they reuse an allocation id; identify each reported candidate by a key derived from its complete canonical route snapshot.
When refreshing a held pause, retain earlier candidates and observations and mark which candidates still exist in live state.
Deletion from shared state must not delete the local explanation or the locator needed for deliberate inspection.

`evidence_digest` hashes a canonical, versioned representation of the berth id, complete candidate route snapshots, their live or withdrawn status, associated retained head SHAs and relevant investigation outcomes.
Route content includes allocation and account ids, protocol, URL, location, `client_id` and `path_metadata`; allocation ids alone do not bind mutable locators or account routes.
Exclude report-generation timestamps and credential values so rereading an unchanged report does not invalidate it.
Resolution reconstructs the current report from retained evidence, live rows and relevant refs rather than trusting the stored digest.

Restart is covered by both tables being ordinary rows and the publication heads being retained Git refs.
Replay is filtered by ancestry in `outstanding_sources`; unchanged evidence recomputes the same digest.
Keep the resolved snapshot in `berth_write_choice` when clearing a pause, so resolution does not erase its explanation.
Historical candidates alone do not reopen a resolved pause.

### 3. Enforcement

`_resolve_berth_cloud_or_raise` (`backend.py:1035`) is the one place every own-berth provider operation resolves its location: uploads, downloads, materialization, runtime artifacts and signals all reach it.
It becomes the enforcement point:

1. a `berth_source_pause` row for this berth → new `CloudBerthSourcePausedExn`, reason `berth_source_paused`, mapped in `ROUTE_REASON_BY_CLOUD_REASON` (`manager.py:48`);
2. no allocation row → `CloudLocationMissingExn`, unchanged;
3. one allocation row → use it, unchanged;
4. more than one → raise `CloudBerthSourceAmbiguousExn`, even if `berth_write_choice` names a surviving row.

Read the pause and allocation rows in one SQLite read transaction so they describe the same committed state.
Case 4 refuses ambiguous state even if it arrived outside the Manager paths that atomically record the pause.
It never treats an old choice as permission to use a newly disputed allocation.
The guarantee applies to operations starting against the new committed state; it does not cancel provider requests already in flight.

The Hub reads device-local NoteToSelf here already, for credentials, so this adds no new coupling; it decides nothing, and every write to both tables stays with the Manager.

What is deliberately not paused, and why:

- `_download_peer_file` (`backend.py:1523`) reads a *teammate's* placement for the same berth id.
  That is their decision, not the one in dispute, and for `s3` it never touches the local allocation.
- `push_note_to_self` and NoteToSelf integration.
  They are the channel the resolution itself travels over; pausing them would make the pause unresolvable.
- Investigation, and the operations in piece 5 that apply the choice.

### 4. Presentation and investigation

One Manager operation, `berth_source_status(team_name)`, refreshing device-local evidence without provider I/O and reporting for the Core berth:

- whether a pause is held, when it was detected, and the digest the human would be deciding over;
- each live or retained candidate: candidate key, allocation id, account, protocol, url, location, `created_at`, provenance and whether it has been withdrawn from live state;
- what is blocked — the Hub reasons above for this berth, plus the channel-wide NoteToSelf block when a source is still refused;
- what could not be established: which device authored a row, and any candidate whose location could not be reached.

`core_storage_allocation` keeps its current shape and gains the pause state, so no existing caller has to learn the new report to notice it is paused.

Investigation reuses what already exists rather than adding a fetch path.
`fetch_teammate_core` (`manager.py:1268`) already imports a chain, refuses to advance a pin on divergence, and preserves the observed head under an immutable observation ref (`_record_core_divergence`, `manager.py:1344`);
`list_core_source_heads` (`manager.py:1380`) reports them.
Investigating a *candidate location of this participant's own berth* needs the same three steps against a named retained candidate.
Add one Manager-only Hub entry point taking a candidate key from the current pause report or retained decision report.
The Hub looks up the saved route snapshot in device-local NoteToSelf, checks its participant and berth scope, and uses current local credentials for the named account.
This path explicitly bypasses the ordinary pause and live-allocation cardinality checks, including when the candidate no longer has a live allocation row.
It preserves session authorization, admissible announcement and publication verification, credential checks and any applicable explicit inspection authorization.
A retained route snapshot is evidence, not authority to bypass those checks; missing credentials or admissible announcements are reported as unavailable evidence.

Bind the entire chain walk to that candidate snapshot, including its account, endpoint and location; do not reselect a live allocation or announcement for each object request.
The implemented path permits credential refresh but refuses changed account route fields before provider I/O, including inspection of a withdrawn candidate.
Associate preserved heads and failures with that candidate key, including when two locations return the same head.
The entry point reads, never materializes, never writes back a locator and never accepts an arbitrary caller-supplied route.
It fetches and verifies outside the database writer reservation; publishing the resulting evidence follows the serialization rule below.

### 5. Choice, staleness and resumption

`resolve_berth_source(team_name, candidate_key, evidence_digest)`, one Manager operation:

1. begin an attached NoteToSelf `BEGIN IMMEDIATE` transaction before reading live candidates, retained evidence or relevant refs;
2. reconstruct the report and digest under that writer reservation; refuse with `evidence_changed` if it differs from the caller's, preserving the pause and exposing the updated report;
3. require the candidate key to identify exactly one reviewed snapshot for this berth;
4. if the snapshot has been withdrawn, restore its original allocation row only as this explicit human management decision, and only if its account still exists with matching route fields and its allocation id is not live with different content;
5. delete every other live candidate row for the berth, save the reviewed snapshot and chosen allocation in `berth_write_choice`, and delete `berth_source_pause` in that same transaction.

If restoring a withdrawn candidate is not possible, report the missing account or conflicting live content and keep the pause; do not recreate accounts, credentials or provider objects.
Selection from retained evidence does not claim that the location is reachable or its data complete.
Restoring a row does not integrate any fetched Core history.

Every Manager path publishing relevant observation refs or changing local investigation outcomes must use the same NoteToSelf writer reservation for that short publication step.
Resolution reads the relevant refs while holding it, so a new head is either included in the comparison or published after the choice commits.
Import and verification may run outside the reservation; imported objects alone are not a completed observation.
If a process stops after publishing a ref but before updating the report, reconstruction reads that retained ref on the next status or resolution call.
The review fix fetches without a pin, then publishes an immutable observation ref and advances the convenience ref under the NoteToSelf reservation.
Both status and resolution read those refs under the same reservation.
If resolution finished before the fetch completes, the later ref remains inspectable without rewriting the earlier decision's evidence or reopening its pause.
Keep this serialization at the Manager boundary rather than teaching generic Cod Sync or `splice_merge` about allocation policy.
Ordinary SQLite writers, including Hub locator writeback and account edits, are excluded by `BEGIN IMMEDIATE` until the comparison and mutation complete.

Deleting the losing rows is what makes the choice effective for siblings.
It is a participant-level management decision written to the participant's own shared state, and it reaches other devices by ordinary NoteToSelf adoption, not by claiming their agreement.
A sibling whose own signed announcement does not match the surviving allocation gets `route: pending` from `derive_team_join_state` (`provisioning.py:3656`), and an argument-free `reconcile_team_route` signs for the row it now holds once any held pause is explicitly resolved.
If that sibling already holds a pause, adoption keeps it held and preserves the withdrawn alternatives for inspection and an explicit local decision.
No new adoption-time security review is introduced for a sibling that was not already paused.

With the pause row gone the Hub's case 1 no longer fires.
Normal I/O resumes at the chosen location only when the existing credential, announcement, materialization and publication checks permit it; route reconciliation may still be required.
Unrelated work from both siblings survives because it was adopted at integration time and never refused.

The Move 9 gap closes in both directions.
Choosing the sibling's location is `resolve_berth_source` plus `reconcile_team_route`, with no raw row delete.
Choosing this device's own location is the same two calls with a different candidate key, and needs no location parameter on `reconcile_team_route` and no third rotation.

## Remaining verification after the latest-commit review

These are implementation and verification tasks, not a request to reopen the accepted human-pause design.
Do not mark this handoff fully validated until each item has a runtime result or an explicit, human-approved deferral.
The existing nine connected probes are the starting fixtures; the focused inspection micro tests are in `packages/small-sea-manager/tests/test_berth_source_inspection.py`.
Those micro tests use real SQLite, Git and Cod Sync with filesystem transport, substituted session/team lookup, and an adapter stub for the account-route checks.
Their injected interruption unwinds the SQLite transaction; it is not an operating-system process-kill or power-loss test.

1. **Adoption interrupted around pause projection — open.**
   Extend the competing-allocation setup in `test_note_to_self_integration.py` or the two-installation probe.
   Start with a prior explicit choice, then adopt a genuinely new competing allocation.
   Interrupt after the row delta but before the post-mutation projection, and separately after the SQLite commit but before Git merge recording.
   Inspect through a fresh attached connection and a fresh Hub operation.
   Before commit, neither the new row nor its pause may be visible; after commit, the new candidate and held pause must both survive and the old choice must not permit ordinary I/O.
   Retry adoption and assert that unrelated work and both alternatives survive without duplication or automatic resumption.

2. **Allocation/locator writers racing resolution — open.**
   Use two connections with deterministic barriers around resolution's `BEGIN IMMEDIATE` and digest comparison.
   Exercise a new allocation, a Hub `_writeback_locator` update, and an account-route edit.
   When the other writer commits first, resolution with the old digest must refuse and report changed evidence.
   When resolution acquires its reservation first, the writer must wait; its subsequent change must not be swallowed by resolution's deletion.
   A subsequently adopted competing allocation must produce a fresh pause; a locator update against a deleted allocation must report that it lost the race.
   Record the live rows, retained alternatives, digest, pause and actual Hub destination in each ordering.
   The inspection publication versus resolution micro test does not stand in for these writers.

3. **Publication interrupted after resolution — open.**
   Choose each existing candidate in separate two-installation runs, reconcile, and reach a publishable Core head (perform ordinary integration if the chosen source diverges).
   Inject failure before publication takes effect, and separately after the remote head write succeeds but before the caller records success.
   Retry without asking for a new location.
   Assert that the selected allocation ID, route and intended content survive, publication remains forward-only, and retry neither creates another choice nor writes to the discarded destination.
   A typed unresolved publication outcome is acceptable only if reported honestly and exercised through the documented settlement path.

4. **Restoration of older local state — open; document the limit.**
   Save snapshots before detection and after a pause, then test restoring only the device-local database and restoring the whole installation separately.
   State exactly which shared rows, choice/pause records and Git refs survive each restoration.
   If competing live rows remain, a fresh Hub call must still reject ambiguity and Manager status must project the pause again.
   If restoration loses the only evidence that this device held a pause or made a decision, demonstrate and report that loss; do not claim that restart durability covers restoring an earlier snapshot.
   No runtime read-stop workflow was added by this branch, so do not report its replay model as runtime restoration coverage.
   Do not add a recovery subsystem as part of this check.

Run the relevant micro tests with temporary-repository Git signing disabled if local signing setup interferes:

```sh
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest packages/small-sea-manager/tests/test_berth_source_inspection.py packages/small-sea-manager/tests/test_note_to_self_integration.py -q
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest .IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_source_resolution.py -q
```

The connected probes require local Hub/MinIO servers and are not collected by an ordinary repository-suite invocation.
For every added schedule, record the exact invocation, assertions, result and any remaining limitation in `notes.md`, then briefly update `plan.md` and the issue-follow-up proposals.

## Required representation change

Everything above assumes a berth may hold more than one allocation row while a question is open.
That means dropping `idx_berth_cloud_allocation_berth` and bumping `SHARED_SCHEMA_VERSION` (`packages/small-sea-note-to-self/small_sea_note_to_self/db.py`).
The invariant does not disappear; it moves from a schema constraint to a stated rule:
*one live allocation per berth after resolution, and more than one live candidate means a held pause.*
Retained historical alternatives are not live allocations and do not themselves authorize normal I/O.

`initialize_shared_db` raises `NotImplementedError` for any non-zero prior version, so this replaces existing research playgrounds rather than migrating them.
That is the branch's stated posture on research artifacts, and the version marker stays in place.

Four functions currently read or write on the assumption of one row and must be scoped to the chosen allocation:

- `get_berth_cloud_allocation_for_berth` (`provisioning.py:6362`) — return the sole live row; raise rather than pick whenever several exist.
- `resolve_berth_cloud_allocation_intent` (`provisioning.py:6229`) — refuse rotation while paused or ambiguous; only explicit resolution may delete competing candidates.
  For ordinary rotation, select and replace the sole allocation under `BEGIN IMMEDIATE`, deleting by allocation id and projecting pause state before commit.
- `add_berth_cloud_allocation_by_berth_id` (`provisioning.py:6184`) — keep its explicit "already exists" refusal; it is a first-allocation helper, not a rotation.
- `_auto_allocate_berth_cloud_if_available` (`provisioning.py:6398`) — same, and it must not allocate into a berth that already has candidates.

`_writeback_locator` (`backend.py:1157`) is already keyed by allocation id and remains an execution-result write.
Its mutable location is included in the evidence comparison; it cannot land between resolution's comparison and commit.
`packages/small-sea-manager/spec.md:1475` and `packages/small-sea-hub/spec.md` (berth allocation resolution, around line 240) state the one-row rule and are updated after validation, not before.

## Out of scope

No fan-out reads, no failover, no replication, no migration between locations, no read stops or write stops in the runtime, no succession or ancestry records, no isolation machinery to keep unrelated NoteToSelf work moving during a refusal, and no attempt to discover a location nobody has announced.
The channel-wide block that survives a refused source stays; it is reported, not narrowed.
Straightforward migration to a new cloud provider remains an eventual goal.
Deleting losing allocation rows does not delete provider data, credentials, accounts or retained historical evidence.
Additional security checks comparing old and incoming placement state, with possible human intervention during adoption, are future work and are not designed here.
