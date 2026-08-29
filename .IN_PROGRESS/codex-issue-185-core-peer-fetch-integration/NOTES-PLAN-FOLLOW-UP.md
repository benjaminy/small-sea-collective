# Notes

## Goal and status

Branch: `codex/issue-185-core-peer-fetch-integration`.
Implements GitHub issue #185, extracted from #3 and the `sync-orchestration` planning branch.
Written against `main` at `3c98abc` on 2026-08-29.
The plan below is implemented; all five steps have landed.
The branch name predates the narrowed fetch-and-park scope.

Add a production `TeamManager` operation that fetches one teammate's Core chain through the encrypted Hub session and preserves the fetched head under durable device-local refs without moving local `main`.
Expose a restart-stable read model over those teammate refs and the immutable self-publication refs already created by Cod Sync.
Fetching remains explicit and unconditional.
This issue does not integrate a parked source, interpret Hub signals as proof of currency, or decide which fetched Core data may acquire local effect.

## Narrowed boundary

Issue #185 establishes transport and durable observation only.
A structurally valid Cod Sync chain and a parked Git ref prove what bytes this device fetched from the selected store.
They do not prove publication authorship, extension admissibility, or permission to integrate the fetched state.

Git refs are the durable truth.
Do not add a Core sync sidecar database, a signal watermark, a latest-fetched SHA column, or persisted status labels.
Derive fetched, superseded, and contained-in-local-main state from live refs and Git ancestry after constructing a fresh `TeamManager`.

Use one logical source per teammate and a separate `self_publication` logical source.
Within one logical source, a live head that is a strict ancestor of another live head is superseded.
Containment by a different teammate's head must not hide a source.
Ordinary divergence retains both maximal heads rather than selecting a canonical one or returning a permanent fetch failure.

## Landed prerequisites and current seams

- #183 delivers the invitee's first Core storage announcement, so both invitation directions can resolve peer storage after their documented admission step.
- #184 makes `TeamManager.push_team` commit and publish completed Manager-owned Core state.
- #187 replaced synthetic Git remotes with direct bundle plumbing.
  The peer transport is `cod_sync.store.PeerSmallSeaStore`, and `CodSync.fetch(pin_to_ref=...)` imports validated bundle history while moving only the requested forward-only ref.
- #191 preserves a competing own-publication head under immutable `refs/cod-sync/parked/{link_uid}`.
  Issue #185 exposes those refs in the same read-only parked-source vocabulary but does not integrate them.
- #188 remains open for async-first I/O design.
  This issue adds no web or CLI orchestration and defines no broader concurrent-operation policy.
- #190 owns Cod Sync link-authorship verification.
  Until it lands, a parked teammate ref is an inert transport observation and must not be described as authenticated or admissible for integration.
- `CodSync.fetch` validates link and bundle structure, imports objects without a work tree, and uses `Repo.advance_ref` for forward-only pin movement.
- A pin divergence raises `PinIntegrationRequiredError` only after the fetched objects have been imported.
  Manager can therefore preserve the observed head under an immutable link-scoped ref and report an ordinary divergent observation.
- Encrypted Core peer reads already go through Hub endpoints and advance the device-local sender-key receiver state.
  This issue follows the Manager's current synchronous operation boundary rather than adding multi-process coordination ahead of #188.
- The Hub already reports an unknown peer route as `peer_storage_unknown`, but `PeerSmallSeaStore` currently collapses that response into a generic provider failure.
  A missing peer sender key currently raises a bare `ValueError`, becomes an untyped Hub 500 response, and is also collapsed into a provider failure.
  This issue includes the narrow Hub and store error-contract changes needed to preserve both retryable prerequisites end to end.

## Parked source contract

### Teammate sources

Use `refs/small-sea/core-peer/{teammate_id_hex}/latest` as the forward-only convenience ref for one teammate.
The Core chain belongs to the teammate across storage-route replacements, so route-announcement identity does not belong in the ref name.

Call `CodSync.fetch(pin_to_ref=latest_ref)`.
When the ref is created, advanced, unchanged, or already ahead of the observation, return success and report both the observed head and the ref's current head.
When the fetched head diverges from `latest_ref`, create or verify `refs/small-sea/core-peer/{teammate_id_hex}/observations/{link_uid}` at the observed SHA and return success with both maximal heads.
The convenience ref remains unchanged.
A repeated fetch of the same divergent link must rediscover or verify the same immutable observation rather than wedge the teammate's fetch path.

The teammate id selects the source.
Do not accept an arbitrary store, repository ref, provider locator, or caller-supplied link id.
The operation resolves the teammate through the existing Core Hub session and constructs `PeerSmallSeaStore` for that teammate.

### Self-publication sources

Discover existing `refs/cod-sync/parked/{link_uid}` refs created by Cod Sync's publication-settlement path.
Classify them as `self_publication` without claiming a sibling device identity.
Do not copy, rename, advance, delete, or integrate them in this issue.

### Read model

Expose this state as `TeamManager.list_core_source_heads(team_name) -> list[CoreSourceHead]`.
Define `CoreSourceHead` as a frozen record in `small_sea_manager.manager` with these fields:

- `source_kind: Literal["teammate", "self_publication"]`;
- `teammate_id: str | None`;
- `ref_name: str`;
- `head_sha: str`;
- `is_maximal: bool`;
- `superseded_by_refs: tuple[str, ...]`; and
- `contained_in_main: bool`.

`teammate_id` is the lowercase hex teammate id for a `teammate` source and `None` for `self_publication`.
Return records in `ref_name` order and order each `superseded_by_refs` tuple the same way.
Within one logical source, a ref supersedes another only when it names a strict descendant SHA; two refs at the same SHA remain maximal rather than superseding each other.
`is_maximal` is true exactly when `superseded_by_refs` is empty.
These are derived observations, not persisted workflow states.
Do not expose a conflict label because this issue performs no merge and current splice behavior defines no semantic-conflict result.
Do not expose `up_to_date` because a parked ref says nothing about whether the remote store has changed since the last explicit fetch.

## Fetch operation

Add `TeamManager.fetch_teammate_core(team_name, teammate_id)`.
It resolves exactly one teammate from one encrypted Core session and calls `CodSync.fetch` through `PeerSmallSeaStore`.
It never checks out or merges the fetched head and never moves local `main`.

Define and return a frozen `TeammateCoreFetchResult` record in `small_sea_manager.manager` with `disposition`, `observed_head_sha`, `current_head_sha`, `latest_ref_name`, and `observation_ref_name` fields.
`disposition` is one of `created`, `advanced`, `unchanged`, `stale`, or `divergent`.
`current_head_sha` names the SHA left at `latest_ref_name`.
`observation_ref_name` is present only for `divergent`, where it durably names `observed_head_sha`; every other disposition leaves it `None`.

A missing teammate, missing publication, unavailable sender key, unknown peer storage route, Hub failure, invalid Cod Sync chain, or failure to preserve a divergent observation surfaces as a typed Manager fetch error.
Define the Manager vocabulary in `small_sea_manager.manager` under a common `CoreFetchError(Exception)` base, not under `ValueError` or in provisioning:

- `TeammateNotFoundError`;
- `CorePublicationMissingError`;
- `PeerSenderKeyUnavailableError`;
- `PeerStorageUnknownError`;
- `CoreFetchRemoteError`;
- `InvalidCoreChainError`; and
- `CoreRefPersistenceError`.

For a well-formed teammate id, distinguish an unknown teammate with one narrow Manager-owned existence lookup against the local Core `teammate` table before constructing the peer store.
Use a dedicated existence helper that selects no route data; do not implement this check through `list_teammates`, because that listing also derives Core routes.
An absent row maps to `TeammateNotFoundError`.
Once the row exists, the Hub's result is authoritative for the fetch, and `peer_storage_unknown` maps to `PeerStorageUnknownError` without Manager trying to explain or second-guess the missing route.
Failed transport may leave structurally validated Git objects without refs, as Cod Sync already permits, but it must not move or invent a durable source ref unless the corresponding fetch result is known.
Unavailable sender-key delivery and an unknown peer route remain distinguishable retryable prerequisites rather than provider failures.
The Hub's route selection for the actual Core session remains authoritative.
Manager must not duplicate route selection with a local preflight through `list_teammates`, `_core_routes_by_teammate`, storage announcements, or any other route view.
Give the Hub's dedicated missing-key exception a stable HTTP 409 response with `{"error": "peer_sender_key_unavailable", "detail": ...}` and preserve the existing HTTP 409 `peer_storage_unknown` response.
Teach `PeerSmallSeaStore` to classify the exact status-and-code pairs as `cod_sync.store.PeerSenderKeyUnavailableError` and `cod_sync.store.PeerStorageUnknownError`, both direct `StoreError` subclasses, before delegating every other response to the shared Hub-store classifier.
Keep that classification peer-specific: an unexpected non-CAS 409 received by `SmallSeaStore` remains a provider failure.
Manager maps those store errors into its fetch-error vocabulary without interpreting other Hub, provider, or cryptographic failures as retryable prerequisites.
Map `RefAdvanceContendedError` and hard `RepoError` failures from forward-only or immutable source-ref updates to `CoreRefPersistenceError`; map Cod Sync's structural chain errors separately to `InvalidCoreChainError`.

The operation does not read, compare, or persist `signal_count`.
It remains callable when notification state is absent, stale, equal, or unknown.
Signal observations are advisory UX input and are planned separately below.

## Scope discipline

Do not add integration methods, merge-driver readiness probes, merge-state inspection, `Repo.abort_merge`, clean-work-tree policy, candidate validation, conflict persistence, or recovery ladders.
Fetching has no work-tree dependency, so foreign tracked, staged, or untracked files are irrelevant to this operation.

Do not add link-signature verification locally as a one-caller exception to #190.
Do not add a Hub signal-contract change, update `ssc-files`, or create a Core watermark store.
The narrow peer-fetch prerequisite error responses above are in scope; broader Hub error normalization is not.
Do not add compatibility layers for `PeerSmallSeaRemote` or synthetic-remote APIs.
Do not expose the private Git command runner through Manager.
Do not add web or CLI entry points before #188 supplies the async operation shape.
Do not add an eighth typed fetch distinction or a new Hub response for malformed teammate-id hex in this issue; input-validation normalization remains deferred.

# Plan

1. Lock the narrowed local seams and specify the new behavior with focused micro tests.
   Use `LocalFolderStore` and focused fake-Hub responses where provider transport is not the behavior under test.
   Characterize the existing forward-only pin creation, advancement, staleness, and divergence-after-import behavior.
   Specify immutable link-scoped observation creation, prefix ref discovery, ancestry classification, and restart discovery of Cod Sync self-publication refs before implementing them.
   -> verify: the fast test layer shows that fetch imports without moving `main`, divergence leaves the convenience ref unchanged, and existing self-publication refs survive independently of Manager process memory.

2. Add the narrow generic Git-ref support needed by the Manager read model.
   Add prefix ref listing and immutable create-or-verify behavior to `cod_sync.repo.Repo` only where existing methods cannot express them cleanly.
   Reuse `advance_ref` for the teammate convenience ref.
   Implement immutable creation with Git compare-and-swap: create against the null object id, reread after a lost race, accept the same SHA, and reject every different existing SHA regardless of ancestry.
   Replace `CodSync._park`'s existing read-then-`advance_ref` sequence with the same primitive, preserving its `ChainError` contract when one link uid names a different SHA; this is the one existing caller brought over because it already promises the identical immutable-ref semantics.
   -> verify: ref listing returns exact ref/SHA pairs under one prefix; an immutable ref can be created or verified at the same SHA but cannot be retargeted or silently lost to a concurrent creator; Cod Sync settlement has the same race-safe behavior; and no operation needs a work tree.

3. Add the Manager parked-source read model.
   Discover teammate latest and observation refs plus Cod Sync self-publication refs.
   Return the named `CoreSourceHead` records through `list_core_source_heads`, classifying strict same-source supersession and containment in local `main` from refs and ancestry.
   -> verify: two teammates and two teams do not collide; same-source strict ancestry marks only strict ancestors nonmaximal; equal-SHA refs remain maximal; cross-teammate containment does not hide a source; divergent maximal heads remain visible; results have deterministic ref order; self-publication sources are not attributed to an invented sibling device; and every result survives a fresh `TeamManager` without a sidecar database.

4. Implement `fetch_teammate_core` through the encrypted Core Hub session and `PeerSmallSeaStore`.
   Resolve only the named, well-formed teammate with the dedicated local existence query, fetch into its forward-only convenience ref, and convert pin divergence into a successful immutable observation ref.
   Return the named `TeammateCoreFetchResult` for every successful disposition.
   Map an absent local teammate row to `TeammateNotFoundError`, but keep route selection in the Hub rather than preflighting Manager's local Core route view.
   Give the Hub's missing peer sender-key case a dedicated exception and stable HTTP 409 `peer_sender_key_unavailable` response, preserve the HTTP 409 `peer_storage_unknown` response, and classify both only for `PeerSmallSeaStore` before mapping them into Manager fetch errors.
   Cover the Hub response, store classification, and Manager mapping as separate micro-test boundaries.
   Keep the existing `SmallSeaStore` non-CAS-409 provider-failure micro test unchanged, and add the peer-store cases rather than weakening or replacing that invariant.
   Map both bounded ref contention and hard Git ref-write failures to `CoreRefPersistenceError`.
   Preserve the seven typed Manager distinctions for a missing teammate, missing publication, unavailable sender key, unknown route, Hub/provider failure, structural Cod Sync failure, and local ref failure.
   -> verify: a well-formed absent teammate id fails before peer transport without consulting route data; only the named known teammate is contacted; all provider reads use Hub endpoints; route and sender-key prerequisites survive both Hub and store boundaries without becoming provider failures; `SmallSeaStore` still treats unexpected non-CAS 409 responses as provider failures; local `main` never moves; every success returns the specified result shape; created and advanced refs survive restart; unchanged and stale observations are harmless; same-generation divergence retains both heads and can be fetched again; both ref-contention and hard ref-write failures have the local-ref error type; and failed fetch never fabricates a durable source.

5. Run layered local-only validation and inspect the resulting surface.
   Run focused Cod Sync, Hub/store-boundary, and Manager micro tests plus the affected package suites.
   Run one successful encrypted Core peer-read integration probe in each invitation direction with valid routing and completed sender-key delivery.
   Use only local Hub fixtures and MinIO; the two probes may share one fully provisioned invitation setup when neither fetch assertion relies on the other fetch having run.
   Treat these as integration probes rather than micro tests because the real peer path supports S3 and Dropbox, not `LocalFolderStore`.
   Review the diff for work-tree writes, duplicated Git truth, canonical-head assumptions, direct provider access, signal/watermark coupling, integration or admissibility claims, stale synthetic-remote names, and unrelated refactoring.

## Convincing a skeptic

- Restart assertions construct a fresh `TeamManager` and rediscover refs from disk, so persistence claims cannot pass on in-memory state.
- Fetch tests instrument the Hub boundary and teammate id, so one-peer and gateway claims are mechanically observable.
- Divergence tests fetch again after preserving both maximal heads, proving divergence is an observation state rather than a permanent error.
- Ancestry tests distinguish same-source supersession from cross-teammate containment without choosing a canonical divergent head.
- Self-publication tests begin with the immutable ref produced by Cod Sync rather than reconstructing publication state in Manager memory.
- Direction-specific encryption probes prevent a routing or sender-key fixture failure from being mistaken for encryption coverage.
- Tests use local stores or local services only.

# What landed

Step 1 is folded into the other steps: each behavior was specified by its own micro tests as it was built.

**`cod_sync.repo.Repo`** gains `list_refs(prefix)` and `create_ref_immutable(ref_name, sha)`.
The immutable creation is a compare-and-swap against a nonexistent ref; losing that race to a writer that stored the same SHA is reported as `verified` rather than a failure, and any other existing SHA raises the new `RefImmutableConflictError` regardless of ancestry.
`CodSync._park` now goes through that primitive and keeps its `ChainError` contract.
Its conflict branch had no test before; it does now.

**Peer-read prerequisites** stay distinguishable end to end.
`small_sea_hub.crypto` raises a dedicated `SenderKeyUnavailableExn` where it raised a bare `ValueError`, `/peer_cloud_file` answers it with HTTP 409 `peer_sender_key_unavailable` beside the existing `peer_storage_unknown`, and `PeerSmallSeaStore` classifies exactly those two code-and-status pairs as `PeerSenderKeyUnavailableError` and `PeerStorageUnknownError` before delegating everything else to the shared Hub classifier.
`SmallSeaStore` still treats an unexpected non-CAS 409 as a provider failure, and that micro test is untouched.

**`TeamManager`** gains `fetch_teammate_core` and `list_core_source_heads`, the two frozen records, and the seven-error `CoreFetchError` vocabulary.
Teammate existence is checked through the new `provisioning.teammate_exists`, which reads no route data.

One deviation from the plan: Cod Sync raises `LinkFormatError` and `UnsupportedLinkVersionError` outside `CodSyncError`, so a malformed head link would have escaped the Manager's vocabulary untyped.
They are named explicitly in the same handler as `ChainError` and map to `InvalidCoreChainError`.
Widening Cod Sync's own error hierarchy instead would have been a larger change to a contract this issue does not otherwise touch.

## Validation

The Cod Sync suite passes 196 tests, and the Manager suite passes 290 tests.
The Hub suite passes 125 tests; `test_notification_roundtrip` errors during fixture setup because the local Docker daemon is not running.

The encrypted probes are staged as two invitations in opposite directions across two teams, not as two reads inside one team.
Reading the *invitee's* chain as the *inviter* needs the invitee's sender key, and that key only arrives through the runtime redistribution loop, which needs the invitee's membership certificate to travel back over the Core chain — the integration #185 does not perform.
The invitee always holds the inviter's sender key from the invitation token, so inviter-reads-invitee is currently unreachable without the integration work.
This is a real ordering constraint on the follow-up issues below, not a fixture inconvenience.

The probes need no Hub watcher pass because both readers are invitees and receive the inviter's sender key in the invitation token.

# Follow-up

No new issue is filed from this planning branch yet.
Use the following issue shapes when this fetch primitive or adjacent work is resumed.

**Define admissible Core integration and convergence semantics.**
Create a focused design issue for a written decision and characterization micro tests, not production integration code.
Decide whether whole `core.db` snapshots are integrable at all; classify signed history, imported shared data, and local mutable projections; define the convergence target, structural and extension validation, content-identity collision behavior, and any real semantic-refusal outcome.
Relate the answer explicitly to #173, #174, and #165 rather than treating the current generic SQLite ours-wins behavior as the intended architecture.
Include isolated candidate evaluation and the final checked-out-`main` adoption boundary in the design, because a disposable worktree simplifies failed candidate cleanup but does not by itself update the live Core checkout safely.

**Make Hub peer signal observations route-generation and exact-berth aware.**
Create a focused bug/task for the existing max-over-berths watermark failure.
Have the Hub report the selected storage-announcement generation plus the exact session-berth count or explicit absence, and update every current consumer in one reviewable unit.
Prove that route replacement, exact-berth regression, and absence cannot be mistaken for equality.
Describe the value as the Hub's transport observation, not an integration-policy decision.

**Add explicit Core integration from parked sources.**
Note the ordering constraint found while building the encrypted probes: until this lands, an inviter cannot read an invitee's Core chain at all, because the invitee's sender key never reaches the inviter.
File this implementation task only after the Core integration decision above has an executable contract.
Make it depend on #190 for publication authorship.
Consume both teammate and self-publication sources from #185, evaluate merge and admissibility in an isolated candidate checkout, and define the narrow final adoption transition separately from candidate cleanup.
Add merge-driver readiness and typed semantic or runtime failure only if the selected integration design actually requires them.

- Keep link-authorship verification in #190, including first-contact trust grounding and the policy for a link signed by a device removed after publication.
- Keep notification-driven UX in #35.
- Keep preview and automatic integration policy in #36.
- Keep proposal-only discovery in #162.
- Keep NoteToSelf own-chain integration in #48.
- Keep the cross-member delivery capstone in #150.
- Recheck the public method and cancellation shape against #188 before exposing fetch through async web handlers.
- Update #35's older comment when that issue resumes: #185 now provides fetched parked-source state only, while honest signal hints and Core integration are separate follow-ups.
- Do not populate `DESIGN-RECORD.md` until late in the branch, when implementation evidence identifies decisions worth preserving.
- File input-validation normalization for peer-facing Manager entry points before any caller is wired up.
  `fetch_teammate_core` passes its teammate id to `bytes.fromhex` through `provisioning.teammate_exists`, so a malformed id raises a bare `ValueError` outside the `CoreFetchError` vocabulary.
  #185 deferred this deliberately because it had no external caller; a web or CLI entry point cannot.
