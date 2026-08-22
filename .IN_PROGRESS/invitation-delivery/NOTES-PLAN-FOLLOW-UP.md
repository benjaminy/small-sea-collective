# Notes

Branch for issue #183: deliver the invitee's first berth storage announcement to the inviter.

## The gap, restated from the code

After #137 and #138, a signed `teammate_berth_storage_announcement` is the only source of peer storage routing.
`Hub._download_peer_file` resolves through `select_effective_teammate_berth_storage` and raises `SmallSeaNotFoundExn` when no valid announcement exists (`packages/small-sea-hub/small_sea_hub/backend.py:1596`).

`accept_invitation` currently publishes the acceptor's own announcement into the acceptor's clone and commits it (`packages/small-sea-manager/small_sea_manager/provisioning.py:5137`).
The `admission_acceptance` blob carried back to the inviter does not include it: the signed field set is `subject_record_id`, `nonce`, `invitee_device_public_key`, `invitee_bootstrap_key` (`provisioning.py:959`).
So after `complete_invitation_acceptance` the inviter holds the acceptor's teammate row, `team_device` row, and membership cert, but no route to the acceptor's storage.

The ordering is circular: fetching the acceptor's Core chain to obtain the announcement requires already knowing the acceptor's storage.
First contact has no bootstrap.

## Working criteria for this branch

Small Sea's local-first setting changes the tradeoffs.
There is no central service that can make an admission and its storage route globally atomic, and the same signed row may arrive through the acceptance courier and later through a peer's Git history.
At the project's human scale, a surfaced failure is preferable to automatic conflict machinery that silently chooses a route.

This is also a research branch.
Implement enough to answer the successful first-contact delivery question and preserve the intended trust boundaries.
Admission without a route remains valid.
Post-admission route repair, rare collision policy, and cross-berth route delivery are follow-up work.

## Route meaning and publication discipline

A teammate berth storage announcement is a signed route selection.
It says that the teammate selected one locator for one berth.
It does not promise that the provider object remains reachable or peer-readable.
Every reader must handle every provider error whenever it attempts I/O.

The durable rule on the publisher's side is locator finality: sign only a locator the provider will not rewrite.
Hub may replace a requested locator during materialization (`packages/small-sea-hub/small_sea_hub/backend.py:1202-1226`).
A signature over a provisional locator hands peers a stale route and stops matching the owner's own allocation, which then fails the next `_require_own_storage_announcement` check (`backend.py:1785-1820`).

Materialization is how a publisher learns the final locator today, not an independent requirement.
Stating the rule this way keeps the publish-after-materialization ordering established by #134 and #137 intact (`Archive/design-record-issue-134-berth-cloud-location-semantics.md:25`, `Archive/design-record-issue-137-member-berth-storage-announcements.md:53-62`) while leaving room for a provider whose locator is final from the moment it is chosen.
Materialization is a readiness check, not a validity condition or availability guarantee, and it makes no claim about what happens after the check.

The current code publishes during `provisioning.accept_invitation` before any Hub materialization, so the code is what this branch must bring back into line with the contract.

`EffectiveTransportSelection.status == "announced"` remains correctly named.
It reports that the reader holds a selected valid announcement, not that the route is `available` or `ready` now.

## Why the acceptance courier remains the chosen carrier

`select_effective_teammate_berth_storage` accepts an announcement only when its `signer_key_id` maps to a device public key that `trusted_device_keys_for_teammate` derives from the team's cert history (`packages/wrasse-trust/wrasse_trust/transport.py:190`).
`complete_invitation_acceptance` issues exactly that membership cert for `invitee_device_public_key`, and the acceptance's self-certifying verify already proves the acceptor holds that key (`provisioning.py:5232`).
The announcement can therefore be verified independently against the same device key without trusting the courier or creating a new routing authority.

Keeping the announcement beside, rather than inside, the signed `admission_acceptance` remains the right split.
Admission is an immutable membership fact while storage announcements are replaceable routing claims.
The separation keeps the canonical bytes and `record_id` derivation settled by #164 unchanged and lets #165 re-canonicalize announcements without rewriting admission records.

Once inserted, the announcement is an ordinary row in the shared team `core.db`.
The inviter's next push republishes it, so the rest of the team can receive the route without facing the same first-contact dependency.

## Settled decisions

### 1. Route preparation moves to Manager, after Hub materialization

`provisioning.accept_invitation` must not acquire invitee-storage materialization responsibilities and must stop publishing the pre-materialization allocation.
It still performs the clone, local key generation, optional Core allocation, and construction of the self-certifying base acceptance.

`TeamManager` owns route preparation as a separate operation over that local result.
It opens an encrypted team Hub session, ensures a Core allocation exists, calls the existing `/cloud/setup` path, rereads the Core allocation after any provider locator writeback, publishes a signed announcement over the final locator, commits that row, and reads the full stored row back.
Encrypted is the mode the interfaces can actually produce: the web UI mints team sessions only in encrypted mode (`web.py:364-390`), and `push_team` already uses one for this same berth and this same `ensure_cloud_ready` call (`manager.py:722-723`).
A passthrough team session would miss the `(team, mode)` session cache every time and fall through to `client.open_session`, which raises unless the Hub runs with `SMALL_SEA_AUTO_APPROVE_SESSIONS=1` (`manager.py:116-127`, `client.py:145-161`).
The mode is inert for this operation: it is stored on the session row (`backend.py:795`) and read only at upload and download (`backend.py:1325`, `1387`, `1405`), never by `materialize_for_session`.
Encrypted also reuses a team session the invitee may already hold, and avoids an `[unsafe]` PIN prompt for an operation that moves no data.
The `/cloud/setup` carve-out is intentionally ungated, so no announcement is required for this first materialization (`materialize_for_session`, `backend.py:1228`, which does not call `_require_own_storage_announcement`).

The Hub performs provider I/O and any narrow locator writeback; Manager remains the only component that reads or writes the team Core DB directly.

Route preparation is retryable rather than one-shot; decision 2 defines the state that makes it so.
Each expected Hub outcome maps to its own stable reason: `CloudUserActionRequiredExn`, `CloudMaterializationFailedExn`, and `CloudAllocationConflictExn` are distinct, as is a team Hub session that cannot be opened.
Unexpected publication, serialization, or local I/O failures are surfaced distinctly and logged rather than misreported as provider unavailability.
Storage failure does not undo the local team join preparation or invalidate the base acceptance.

### 2. Joining is a two-phase local ceremony with a pending route state

Attaching the sidecar at acceptance time and signing only a final locator together force provider I/O into the acceptance ceremony.
Leaving that ceremony one-shot would create a new terminal failure mode rather than a research simplification:

- `TeamManager._get_or_open_session` (`manager.py:116`) falls back to `client.open_session`, which requires `SMALL_SEA_AUTO_APPROVE_SESSIONS=1` and otherwise raises (`packages/small-sea-client/small_sea_client/client.py:142-161`).
  The invitee holds no team session at acceptance time, so on a normally configured Hub the first route-preparation attempt fails.
  Under decision 1's encrypted mode the invitee can establish one through the existing team-session PIN flow (`web.py:364-390`) and retry, so this is a pending state rather than a dead end.
- The acceptance token is never persisted (`provisioning.py:5204`), and `accept_invitation` cannot re-run: `os.makedirs(team_sync_dir, exist_ok=False)` plus the NoteToSelf team-row check.
- A failed preparation would also block the invitee's own pushes, because `upload_to_cloud` gates on `_require_own_storage_announcement` (`backend.py:1384`).
  Publication is currently unconditional during acceptance, so that cannot happen today.

The resolution is to model the pending state rather than skip the network step or treat it as fatal.
All the local work — clone, NoteToSelf row, team device key, allocation choice, signed acceptance — completes offline.
The announcement is the invitee's own statement, not something fetched from the network; the only inherently non-local fact is whether the chosen locator is final.
The signature waits for that fact in an explicit pending state.

Derive the state; persist only the artifact.
`joined_locally` is already derived rather than stored (`has_local_team_clone`, `manager.py:294`, `provisioning.py:3562`), and this branch follows that idiom:

- clone, NoteToSelf `team` row, and team device key exist: local join complete
- no membership cert for this participant's `self_in_team` and current team device key: admission pending
- a Core allocation with no `teammate_berth_storage_announcement` signed by this device for the Core berth: route pending, which is the query `_require_own_storage_announcement` already runs (`backend.py:1785-1820`)

The acceptance artifact is the one thing not stably derivable.
`created_at` is `_now_iso()` and is covered by the signature, so re-signing would mint a different `record_id` on every export and could put two valid acceptances for one proposal into circulation.
Persist the signed base acceptance once in the device-local DB (`device_local_db_path`, `packages/small-sea-note-to-self/small_sea_note_to_self/db.py:38`, alongside sender-key state: local-only, non-syncing, the right scope for an installation-bound ceremony artifact).
The device-local schema gains one dedicated `admission_acceptance_artifact` table keyed by `(team_id, proposal_id)`.
The row stores `nonce`, `author_teammate_id`, `author_device_key_id`, `acceptance_record_id`, the exact base acceptance token, and nullable `first_exported_at`.
It is a new local table, not a shared team fact or another stored join-status machine.

The row is not authoritative evidence that the join still exists.
Before route preparation or export, Manager derives the current join and requires the stored artifact's team, author teammate, and author device key to match the NoteToSelf `team` row's `id` and `self_in_team` and the current team device key.
`proposal_id` and `nonce` are checked against the artifact's own stored copies, which is what binds the exported bytes to the record they were signed over.
Any mismatch makes the artifact stale and ineligible; lookup never falls back to another row by team name or insertion order.

Eligibility deliberately does not require an `admission_proposal` row in the invitee's clone.
`create_invitation` commits the proposal but never pushes (`provisioning.py:5024-5026`), and neither `TeamManager.create_invitation` (`manager.py:405`) nor the web route (`web.py:469`) pushes either.
Whether the invitee's clone contains their own proposal therefore depends on the inviter having pushed between creating the invitation and handing over the token; the two-MinIO test only has it because it re-pushes explicitly (`test_invitation.py:212`).
Nothing on the invitee side reads that row today, since `proposal_id` and `nonce` arrive in the invitation token, and making export depend on it would strand an invitee whose inviter forgot to push.

Persisting the same artifact again is a no-op.
After deliberate local cleanup makes a join retryable, a different artifact for the same `(team_id, proposal_id)` may replace the old one only when `first_exported_at IS NULL` and it matches the new current join.
Once the base acceptance has been exported, it may be re-exported indefinitely, but it may never be replaced by a differently signed acceptance for that proposal.
The export operation sets `first_exported_at` before returning the courier token; the marker prevents replacement, not recovery of the same bytes after an output or process failure.

Only the signed base acceptance is immutable.
The exported courier token may attach the currently selected signed route row beside those stored bytes, so a later route change may change the sidecar without changing the acceptance's canonical bytes or `record_id`.
Repeated exports with no intervening route change remain byte-identical.

This record does not make the clone, Git, NoteToSelf, key-file, and device-local writes one transaction.
An artifact persistence failure is a surfaced local acceptance-preparation failure and must never be reported as route-pending or exportable.
Cross-store rollback machinery is outside this research slice.

Physical deletion is not required for correctness.
Once the current local state no longer describes that pending join, the row is inert under the eligibility checks above.
Retirement after locally observing finalization, expiry, or explicit abandonment remains cleanup work.

Export is gated on route readiness, with no exception.
Every pending reason — no Hub session, no cloud storage configured, provider down, user action required — withholds the token and is retried.

There is no structural pending state.
An invitee with no cloud storage configured has not finished setup; they are not someone who can never hold a route.
`_auto_allocate_berth_cloud_if_available` is idempotent: it returns an existing allocation, otherwise allocates from the first `cloud_storage` row, otherwise `None` (`provisioning.py:5820-5830`).
Route preparation calls it rather than only reading what acceptance left behind, so adding cloud storage through the ordinary UI action (`web.py:556`) and retrying produces an allocation and releases the token.
Nothing is lost by waiting, because the one-shot work is the local join, which already completed.

The gate is what makes deferring repair defensible.
The invitee never spends the proposal on a route-less token.
Repair is then genuinely needed only for a sidecar stripped or corrupted in transit, and stays follow-up work.

Membership with no storage of one's own is a separate design question, not a degraded case of this one.
Such a member could not push anything at all, because `upload_to_cloud` gates on `_require_own_storage_announcement` (`backend.py:1384`).
Whether Small Sea wants read-only membership is follow-up work, and the export gate defers to it rather than inventing a warning-gated half-answer here.

### 3. The sidecar is acceptance-scoped, not a generic importer

Acceptance-time verification has a special trust context.
The prospective device key is self-certified by the acceptance and bound to the inviter's proposal, but it is not yet a currently trusted teammate key while quorum is pending.

Implement a private acceptance-scoped helper that parses, verifies, binds, and conditionally inserts this one sidecar under the already verified admission transcript.
Do not present it as a channel-neutral Manager importer.

A future general route importer must derive acceptable signer keys from the current local trust view rather than accept a caller-supplied public key.
Keeping those operations separate avoids creating an accidental routing trust path.

### 4. No route-sidecar failure aborts admission

The admission record is independently self-certifying (`provisioning.py:5232`).
A malformed or conflicting route sidecar does not taint it.

After the outer token and base acceptance have been parsed and verified, every sidecar-specific failure is isolated:

- absent sidecar: `missing`
- malformed fields, bad signature, wrong signer, wrong teammate, or wrong berth: `invalid`
- a different stored row under the same `announcement_id`: `conflict`

Each outcome inserts no sidecar row, completes the otherwise valid admission, and returns a reason suitable for the UI and CLI.
The helper must catch route-specific parsing failures as well as cryptographic failures.
That includes Core-berth resolution: the uniqueness guard added in plan step 1 raises, and an unguarded raise inside the sidecar path would abort the admission this decision exists to protect.
An ambiguous or absent Core berth on the inviter's side is therefore `invalid`, not an exception.
A malformed outer token or invalid base acceptance remains fatal because there is no independently usable admission record in that case.

### 5. Courier payload integrity

The sidecar remains outside the signed fields of `admission_acceptance`.

An optional signed manifest has no anti-stripping value because a courier can strip the manifest and sidecar together.
Only a mandatory association proves whether the invitee sent a route, and that would turn route presence into part of the acceptance protocol.

The courier cannot forge an alternate route under the invitee's key.
Because the sidecar is not bound to the acceptance, a courier that obtains multiple valid announcements from that key could replay an older invitee-authored route.
For this research slice, stripping and stale valid replay are surfaced denial-of-service risks.
The stronger association and repair mechanism stay follow-up work.

### 6. Only Core counts as first-contact evidence

Invitation acceptance allocates only `SmallSeaCollectiveCore`, and Core routing is what unblocks later sync of announcements for other berths.

`_resolve_berth` reads non-NoteToSelf berth IDs from the team DB (`backend.py:518`), so the reader's session `berth_id` is the same row the invitee announced against.
Acceptance-scoped verification must require `announcement.berth_id == _core_berth_id(conn)`.
Merely requiring the ID to exist in `team_app_berth` could accept a correctly signed non-Core announcement while leaving the first-contact cycle unresolved.

### 7. Insert while quorum is pending

Insert the verified row in the same transaction that records the acceptance, even when quorum has not finalized admission.

The table deliberately carries no foreign keys because invitation and linked-device bootstrap flows can temporarily know the signed statement before every clone has adopted the corresponding teammate, berth, or trust rows (`packages/small-sea-manager/spec.md:882`).
Selection treats a row whose signer is not yet trusted as inert, so a pending row cannot route anything.

Activation requires a membership cert for the exact `(teammate_id, device key)` pair, which finalization of this proposal issues (`provisioning.py:5371`).
An abandoned proposal's inviter-preallocated `teammate_id` is never reused.

The tradeoff is explicit: the pending invitee's locator becomes visible to the existing team and an abandoned proposal leaves one inert row that may sync forever.
The invitee sent the route while attempting to join that team, and the research slice accepts that disclosure and storage cost rather than adding private staging lifecycle machinery.

### 8. Idempotency across local insert and Git merge

Direct insertion treats an existing byte-identical row under the same `announcement_id` as a no-op.
A different row under the same ID returns `route_delivery = "conflict"` and does not abort admission.

Later peer integration starts from a common ancestor in which the acceptor and inviter branches inserted the same primary key.
The merge driver should recognize byte-identical inserts as redundant without weakening its warning for genuinely divergent rows.
A general divergent-ID policy remains deferred to #165.

### 9. Result contracts

`provisioning.accept_invitation` continues to return the self-certifying base acceptance token as a string, and that return is the one path around the export gate.
A direct caller receives an exportable route-less acceptance without setting `first_exported_at`, which is what the gate and the marker exist to prevent: the stored artifact stays replaceable by a differently signed acceptance for a proposal whose bytes may already be circulating.
Treat the low-level return as a test-only escape hatch, say so in the spec, and keep `TeamManager` the only production producer of a courier token.
It performs no invitee-storage materialization and no pre-materialization route publication.

`TeamManager.accept_invitation` returns a join-state report rather than a one-shot outcome:

- `join`: `complete` once the local work has landed
- `route`: `ready` or `pending`
- `route_reason` when `route` is not `ready`: `hub_session_unavailable`, `storage_not_configured`, `user_action_required`, `materialization_failed`, `allocation_conflict`, or `route_preparation_error`; every one of them is retryable
- `acceptance`: `exportable` or `withheld`
- `acceptance_reason` when `acceptance` is `withheld`: `route_pending` when an eligible artifact is waiting for a route, and `artifact_missing` or `artifact_stale` when the current derived join has no eligible immutable acceptance

Route and acceptance reasons are separate because a route may be ready while its local acceptance artifact is absent or stale, and an artifact may be valid while provider setup is pending.
An artifact persistence failure or forbidden post-export replacement is a local workflow error, not `route_preparation_error`.
Route preparation performs no provider I/O unless the exact current join has an eligible artifact to export after success.

`TeamManager` also exposes route-preparation retry and acceptance export over the derived state, so a pending join can be advanced whenever the Hub and the provider are available.

The invitee web UI shows the state and offers retry, and shows export once the route is ready.
`storage_not_configured` names the user action rather than a provider fault: add cloud storage, then retry.
The CLI keeps the acceptance token alone on stdout when it is exportable, prints state and warnings to stderr, and exports a later-prepared token through a separate command.

`complete_invitation_acceptance` returns:

- `route_delivery`: `imported`, `missing`, `invalid`, or `conflict`
- `admission`: `finalized` or `pending`
- a reason when the route was not imported

`TeamManager.complete_invitation_acceptance` must stop discarding the provisioning return value.
The inviter web UI shows the route-delivery status in its completion notice.
The CLI preserves its ordinary completion line on stdout and sends route warnings to stderr.

These values describe local processing, not external reachability.
UI copy may say "Core route claim received from Bob" and "the route passed setup before Bob sent it."
It must not say "Bob's storage is available" or otherwise convert a readiness check into a current-world guarantee.

### 10. The integration witness isolates routing

Use passthrough sessions or a raw runtime artifact.

`accept_invitation` saves only the inviter's sender key and initializes the acceptor's own state locally (`provisioning.py:5152-5161`).
The inviter holds no receiver record for the invitee, so an encrypted invitee-to-inviter read would fail for a sender-key bootstrap reason unrelated to #183.
Encrypted delivery stays a separate test with explicit sender-key setup, as #185 already anticipates.

## Problems found in the surrounding code

### A. The merge driver warns on every invitation

`reconcile_deltas` classifies same-key inserts as an insert/insert conflict and prints a warning without comparing values (`packages/splice-merge/splice_merge/core.py:141-146`).
After this branch, every routed invitation creates that ordinary case because the invitee commits the announcement and the inviter inserts the couriered copy.

Rows are plain dicts at reconciliation time.
Suppress the warning when the two inserted rows compare equal and keep the warning for divergent rows.
Confirm that BLOB columns arrive as `bytes` on both sides rather than `memoryview`, or byte-identical rows will still fail to compare equal.

### B. Several specs disagree with current code

`packages/small-sea-manager/spec.md:541` says the invitee does not write any rows to the shared team DB during the provisioning acceptance step.
Moving route publication out of `provisioning.accept_invitation` makes that true again.
Manager's post-materialization publication and sidecar attachment must be documented as the next orchestration step.

`packages/small-sea-manager/spec.md:537` says cloud endpoints are not included in the acceptance blob.
A routing sidecar beside the signed admission record makes that statement incomplete, although the endpoint remains outside the signed admission fields.

The Hub and Manager publish-after-materialization statements remain correct and need only the route-meaning clarification from the publication-discipline section.

### C. Route-less admission still has no repairer

If admission completes with no sidecar, the inviter cannot learn a route through sync because that is the first-contact cycle this branch exists to break.
No current Manager operation accepts a later peer-authored route artifact, and completion is one-shot.

Decision 2's export gate removes every cause the invitee controls.
What remains unrepairable is a sidecar stripped or corrupted in transit.
Both halves of real repair — later delivery and a usable receiving path — stay follow-up.

### D. `_core_berth_id` has no uniqueness guard

`_core_berth_id` (`provisioning.py:920`) and the Core lookup in `accept_invitation` (`provisioning.py:5119`) both `fetchone()` over an unguarded join.
If multiple Core join rows exist, the two sides can choose independently and fail the sidecar binding confusingly.

The repository already has the pattern to copy: `_single_berth_id_for_app` raises `app_friendly_name_ambiguous` when the app-name join returns more than one berth (`backend.py:535-557`).
Make `_core_berth_id` do the same and use it on both sides rather than retaining a second unguarded lookup in `accept_invitation`.

### E. Vestigial first-contact route columns

`invitation.acceptor_protocol`, `acceptor_url`, and `acceptor_device_key_id` exist in the schema (`core_other_team.sql:39-41`) and in the ORM model (`provisioning.py:2059-2061`) and are never written by any code path.
They are the fossil of the pre-#137 first-contact route channel that the sidecar now replaces.

Do not delete them in this branch.
State in the spec that the sidecar supersedes them, so a reviewer does not read their emptiness as a gap this branch missed.
Removing them belongs in a separate cleanup.

### F. Announcement identity and ordering remain follow-up concerns

`teammate_berth_storage_announcement` makes `announcement_id` a `PRIMARY KEY`, so two differently signed rows cannot coexist under one ID.
A peer can deliberately craft a colliding ID; honest UUIDv7 collisions are not the interesting case.

The current selector also sorts raw `announcement_id` bytes as an author-clock last-writer-wins mechanism.
It is not a coordination-free monotonic order, and malformed or far-future IDs require the eligibility work already planned in #165 (`Archive/branch-plan-issue-165-committee-history.md:191-202`).

This branch neither fixes nor relies on later correction through that ordering.
It surfaces a direct-insert collision as `conflict`, preserves the merge warning for divergent rows, and hands the identity and eligibility design to #165.

## Remaining uncertainties

None blocking.
The branch proves the successful first-contact path, withholds an unroutable acceptance instead of spending the proposal on it, and reports rather than repairs the delivery failures that remain.
The device-local artifact is eligible only under the current derived join, and its export marker prevents a wiped-and-retried join from replacing an acceptance that may already be in circulation.

# Plan

1. Make `_core_berth_id` raise when the Core join returns multiple rows, following `_single_berth_id_for_app` (`backend.py:535-557`), and use it in `accept_invitation` as well as completion.
   `_core_berth_id` takes a SQLAlchemy connection and uses `text()`, while `accept_invitation` opens a raw `sqlite3` connection for the same lookup (`provisioning.py:5115`), so sharing it means converting one side.
   → verify: a micro test with two Core `team_app_berth` rows raises instead of silently picking.

2. Stop publishing a storage announcement inside `provisioning.accept_invitation`.
   Keep the optional Core allocation, local key generation, clone preparation, and signed base acceptance unchanged.
   → verify: the low-level operation performs no invitee-storage materialization, writes no invitee announcement to the shared team DB, and still returns a valid route-less acceptance when called directly.

3. Add the device-local `admission_acceptance_artifact` table and persist the signed base acceptance before `accept_invitation` reports local preparation complete.
   Key the row by `(team_id, proposal_id)` and store the nonce, author teammate, author device key, acceptance `record_id`, exact base token, and nullable `first_exported_at`.
   Treat an identical write as a no-op; permit a different artifact to replace a never-exported row only after deliberate local cleanup has produced a matching current join; reject replacement after first export.
   Increment `LOCAL_SCHEMA_VERSION` and keep the table out of shared NoteToSelf refresh and sync.
   `_migrate_device_local_db` raises `NotImplementedError` (`db.py:104-113`), so the bump forces every existing developer workspace to delete and recreate `device_local.db`, losing sender-key state and with it existing team memberships.
   That is the established pre-alpha idiom rather than a new cost, but it should be stated rather than discovered.
   Do not claim cross-store atomicity: a persistence failure is a local acceptance-preparation error and exposes no token.
   → verify: repeated persistence preserves the same bytes and `record_id`; an unexported stale artifact is replaceable only by the exact current join; an exported artifact can be re-exported but not replaced; and the table remains device-local across NoteToSelf refresh.

4. Add Manager-side route preparation as a retryable operation over the local join.
   Resolve the exact current join and eligible acceptance artifact before performing provider I/O; an absent or stale artifact is an acceptance-state failure, not a route failure.
   Call `_auto_allocate_berth_cloud_if_available` first, so an invitee who configures cloud storage after accepting can retry into a route; `None` is `storage_not_configured` and stays retryable.
   Then open an encrypted team Hub session, call `ensure_cloud_ready`, reread the allocation after possible locator writeback, and only then call `publish_teammate_berth_storage_announcement`.
   Map each expected Hub and allocation outcome to its own reason, and surface unexpected post-acceptance errors distinctly without misclassifying them as provider unavailability.
   → verify: a missing or stale artifact causes no Hub or provider call; an unopenable Hub session, each expected Hub setup failure, and an injected publication failure each leave the join pending and retryable; an invitee with no cloud storage reports `storage_not_configured`, and adding storage then retrying reaches `ready` without a second local join; success publishes only after materialization.

5. Derive the join state and gate export on it.
   Report local join completion, admission pending, and route readiness from the clone, the NoteToSelf `team` row, the membership-cert view, and the announcement query the Hub already uses; add no status column and do not read `admission_proposal`.
   Require the artifact's team, author teammate, and author device key to match that derived state exactly, and check `proposal_id` and `nonce` against the artifact's own stored copies; a stale row is never an export candidate.
   Withhold the acceptance token whenever the route is not ready, with no exception.
   Mark `first_exported_at` before returning the first courier token while allowing later exports of the same stored base acceptance.
   → verify: a pending route withholds the token, the same call after a successful retry releases it, every eligibility-field mismatch withholds the stale artifact, repeated exports preserve the base acceptance bytes, and an invitee whose clone contains no `admission_proposal` row still exports normally.

6. Read the published row back by the publish result's `announcement_id` and attach it beside `admission_acceptance`.
   Never reconstruct or re-sign the row.
   The publish result is not a full row: its no-op branch omits `announced_at` and neither branch returns `signature`.
   That branch also returns the selected row's `signer_key_id`, which need not be this device's key (`provisioning.py:3441-3459`), so assert on read-back that it equals `key_id_from_public` of the current team device key.
   Otherwise a legitimate route reaches the inviter and fails step 8's signer check as `invalid`.
   → verify: the sidecar is field-for-field identical to the invitee's stored row, and a read-back whose signer is not the current device is refused before export rather than attached.

7. Carry `announcement_id`, `teammate_id`, `berth_id`, `protocol`, `url`, `location`, `announced_at`, `signer_key_id`, and `signature` beside `admission_acceptance`, never inside its signed fields.
   → verify: golden acceptance canonical bytes and `record_id` remain unchanged; a route-less acceptance still verifies and completes.

8. Add the private acceptance-scoped sidecar helper inside `complete_invitation_acceptance`.
   Verify the signature against `invitee_device_public_key`, require `signer_key_id == key_id_from_public(invitee_device_public_key)`, bind `teammate_id` to `author_teammate_id`, and bind `berth_id` to the unique Core berth.
   Isolate every sidecar-specific parse, verification, resolution, and collision failure from the admission transaction's outcome, including an ambiguous or absent inviter-side Core berth.
   → verify: one micro test per failure, including a validly signed non-Core announcement and an ambiguous inviter-side Core join; every case completes admission and inserts no couriered route row.

9. Insert the verified row verbatim inside the acceptance transaction, including when quorum is not yet met.
   Place it in the `else` branch that records the acceptance, not merely inside `with engine.begin()`: the `block_reason` path leaves that block having written nothing and raises afterward (`provisioning.py:5303-5308`, `5397`), so a sidecar inserted there would persist against a refused admission.
   Existing identical bytes under `announcement_id` are a no-op; different bytes return `conflict` without raising.
   → verify: inviter and invitee rows match field-for-field, a blocked admission inserts no sidecar, quorum-pending selection remains `missing`, finalization makes the same row selectable without rewriting it, and a collision does not cost the invitee admission.

10. Implement decision 9's result contracts through Manager, web, CLI, and affected tests.
    Keep the acceptance token alone on CLI stdout.
    Keep `route_reason` and `acceptance_reason` independent so local artifact failures are never presented as provider failures.
    Move the three call sites that use `TeamManager.accept_invitation`'s return value as a token (`test_invitation.py:216`, `:505`, `:513`) onto the export operation.
    → verify: the invitee sees join state and whether a route was attached, a ready route with a missing or stale artifact remains non-exportable for the correct reason, retry and export are reachable from both interfaces, the inviter sees whether the route was imported, warnings use stderr, and no UI copy claims current reachability.

11. Add focused materialization-order tests.
    A fake provider returning `materialized_with_locator` must cause Manager to reread the allocation and sign and attach only the final locator.
    No in-tree adapter produces that outcome for s3 (`adapters/s3.py:40-45`) and `_make_storage_adapter_from_record` dispatches only on protocol (`backend.py:1192-1200`), so this test monkeypatches Hub internals; budget for that rather than assuming a fixture exists.
    A materialization failure must leave a pending, retryable join with no announcement row and no exported token.
    → verify: an own-storage operation after provider locator writeback passes `_require_own_storage_announcement` because the signed row matches the final allocation.

12. Suppress the spurious insert/insert warning in `reconcile_deltas` when the two rows compare equal, then exercise the duplicate through the real splice-sqlite merge path.
    Start from a common ancestor, insert through the acceptance path on the inviter branch, retain the original row on the invitee branch, and merge.
    → verify: the merge produces one byte-identical signed row with no warning; a divergent same-ID pair still warns and keeps the existing conservative policy.

13. Extend the two-MinIO Alice/Bob flow in `packages/small-sea-manager/tests/test_invitation.py`.
    Bob's route preparation must materialize his Core bucket through his Hub, apply the public-read policy, publish the route, and attach it before the acceptance becomes exportable.
    Do not call the test-only `_make_bucket_public` helper for Bob's bucket: `SmallSeaS3Adapter.materialize` creates the bucket and applies that policy itself (`packages/small-sea-hub/small_sea_hub/adapters/s3.py:20-45`).
    After completion and without sync delivery, Bob uploads a passthrough or runtime artifact and Alice reads it through her Hub's peer path; `upload_runtime_artifact` skips the own-announcement gate (`backend.py:1408`), so the witness exercises routing rather than that gate.
    → verify: the read succeeds through `select_effective_teammate_berth_storage` and fails if the inviter-side announcement insert is removed.

14. Update `packages/small-sea-manager/spec.md` and `packages/small-sea-hub/spec.md`.
    Document locator finality and the Manager-after-Hub publication discipline, the provider-issued final-locator reread, the derived pending-route state, the device-local artifact's eligibility and no-replacement-after-export rules, the unconditional export gate, the test-only status of `provisioning.accept_invitation`'s token return, the optional sidecar, acceptance-scoped verification, the four-valued route result, the inert quorum-pending row, the remaining no-repair boundary, and the superseded vestigial columns from problem E.
    Preserve the publish-after-materialization ordering at `manager/spec.md:871` and `hub/spec.md:259`, while clarifying that the readiness check creates no continuing reachability guarantee.
    → verify: admission optionality, route reporting, materialization timing, export gating, and first-contact delivery are internally consistent.

## Validation story

For a skeptic asking "is the goal accomplished":
the integration test in step 13 is the load-bearing delivery witness.
It uses two participants and separate MinIO locations, materializes and publishes Bob's route through the Hub-and-Manager boundary before couriering it, omits sync delivery and manual inviter-side routing fixtures, and fails if the courier insert is removed.

The provider-issued-locator test proves that the sidecar carries the final route rather than a provisional one.
The materialization-failure test proves that storage availability is not admission authority, and that a transient failure costs a retry rather than the proposal: the token is withheld, and a retry after the provider recovers releases the same acceptance bytes with the route attached.
The artifact tests prove that export always uses the acceptance for the exact current join and that an acceptance which may already be circulating is never replaced by a second signed record for the same proposal.
The merge witness proves that the same row can later arrive through Git without producing a false conflict warning.

Negative tests prove that no malformed, wrongly signed, wrong-teammate, wrong-berth, ambiguously resolved, or same-ID-divergent row reaches the inviter's DB.
The same tests prove that none of those route failures costs the invitee their otherwise valid admission.

For a skeptic asking "is repository integrity maintained":
no new general trust path is added.
Acceptance-scoped verification uses the self-certified key already bound by the admission transcript, and ordinary peer routing still activates the row only through membership-cert trust.
No new shared table and no change to `admission_acceptance` canonical bytes or `record_id` derivation are required.
One device-local table preserves the immutable acceptance bytes without turning the artifact into team state or a routing authority.
Delivery and team DB insertion remain Manager-side, consistent with Manager database exclusivity; materialization and provider locator writeback remain Hub-side.

## Permanent documentation boundary

This branch does not add a general "what may be signed" rule to the Constitution and does not need a new architecture-level local-state doctrine.
Those claims are broader than the first-contact routing question.

The permanent text belongs in the Manager and Hub storage sections and should make this narrower statement:

> A teammate berth storage announcement is a signed selection of a route for one teammate and berth.
> Manager signs only a final locator: one the provider will not rewrite.
> As an issuer-side publication discipline, that means publishing only after Hub has successfully materialized the selected location once and any provider-issued final locator has been durably recorded.
> That readiness check prevents publication of an unresolved route; it is not a certificate of current or future reachability.
> Readers must handle all provider failures when they attempt I/O.

# Follow-up

- #202 covers a later route-delivery artifact and a receiving path based on the current trusted-key view, for the one case the export gate does not cover: a sidecar stripped or corrupted in transit.
- #206 covers membership with no storage of one's own, which the export gate now defers rather than half-answers.
  Such a member cannot push at all (`backend.py:1384`), so read-only membership is a design question and not a degraded join.
- #205 asks whether invitation creation, which commits the proposal but never pushes (`provisioning.py:5024-5026`), must publish before token handoff.
  Whether an invitee's clone holds their own proposal is currently incidental.
  Nothing depends on it after this branch; decide separately whether it should.
- #203 covers physical cleanup for an inert device-local acceptance artifact after finalization, expiry, revocation, or explicit abandonment.
- #208 covers the missing web or CLI route-publication repair action for an existing member whose own Core announcement is absent.
- #204 covers acceptance-sidecar association against stripping and stale valid-sidecar replay.
- #207 covers removal of the vestigial `invitation.acceptor_*` columns from problem E.
- #165 now records problem F's same-ID conflict and UUIDv7 eligibility framing, and that the sidecar re-canonicalizes with the announcement rather than the admission record.
- #150 now distinguishes the successful first-contact witness here from its remaining sync-path delivery witness.
- #134 and #137 now record the clarified announcement meaning and preserved publish-after-materialization ordering.
- #138 now records that first-contact delivery introduces no fallback to legacy `team_device` routing.
