# Branch Plan: Berth Cloud Location Provisioning Semantics

**Branch:** `issue-134-berth-cloud-location-semantics`
**Base:** `main`
**Primary issue:** #134 "Define Manager-owned berth cloud location provisioning semantics"
**Related and blocked issues:** #123, #114, #102, #10, #9, #16
**Kind:** Design branch with spec updates, schema sketches, and only small proof code if it clarifies the design.
Do not write broad production code until the design decisions below are settled.
**Reference docs:** `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md`, `architecture.md`

## Why This Branch Exists

Issue #123 exposed that S3 bucket semantics are not an isolated question.
The current implementation partially collapses several distinct ideas:

- a participant's cloud service accounts
- device-local credentials for those accounts
- the storage location this participant chose for one berth
- how teammates learn that member's storage location for the same berth
- Hub session authorization for a berth
- legacy `team_device(protocol, url, bucket)` admission-time routing metadata

The result is that the Hub synthesizes provider-facing names from `berth_id`
and peer routing has different behavior for S3 and Dropbox.
That makes narrow fixes unsafe because each local correction risks preserving a larger accidental model.

## Design Principles

1. **Session authorization and storage provisioning are separate.**
   A valid Hub session means an app may act in a berth.
   It does not mean cloud storage exists for that berth.
   A later file operation may fail cleanly because no location has been provisioned.

2. **Different teammates make separate storage choices for the same berth.**
   Alice and Bob may store their clones of `{Team}/SharedFileVault` in different providers, accounts, buckets, or folders.
   Peer storage routing must therefore be keyed by both `member_id` and `berth_id`.

3. **Manager decides; Hub performs internet I/O.**
   The Manager owns the UI, policy, and durable provisioning choices.
   The Hub owns cloud I/O and materializes those choices against providers.
   The Manager may still link users to provider account-management or OAuth pages,
   but Small Sea cloud operations go through the Hub.
   When provider reality differs from Manager's requested state,
   the Hub reports structured reconciliation results back to Manager for persistence and UX.

4. **Team-scoped state belongs in the team Core DB when possible.**
   Do not dump team routing semantics into NoteToSelf just because it is convenient.
   NoteToSelf remains the home for participant-scoped state such as cloud account locators and local allocation records that reference those accounts.

5. **Provider-facing location names are explicit allocation state, not identity formulas.**
   A bucket or folder prefix may be a Manager-generated per-berth, per-cloud UUID or a later human-friendly generated name.
   Some providers may instead finalize the location during Hub materialization and report the provider-issued locator back to Manager.
   It is not just `ss-{berth_id[:16]}`.

6. **Each device may lack credentials.**
   Shared account locator state can sync across sibling devices.
   Device-local credentials may not.
   The Hub must distinguish "no berth location exists" from "this device has no usable credentials for the selected account."

7. **Allocation should be lazy, not cross-product eager.**
   A participant may belong to many teams and use many apps.
   Most teams will not use most apps.
   Creating every possible team/app/provider location up front would waste cloud objects and UI attention.
   Team creation and app activation may leave a berth without cloud storage until the human or app workflow asks Manager to provision it.

## Current State Summary

**Hub `_get_cloud_link`** reads `cloud_storage LIMIT 1`.
It chooses the first account and does not filter by berth.

**Hub `_make_s3_adapter`** derives `ss-{session.berth_id[:16]}` as the bucket name.
The derived name is never stored as Manager-owned state.

**Hub `_make_dropbox_adapter`** derives a member-like folder prefix.
That is also synthesized rather than provisioned.

**Hub `_download_peer_file` for S3** ignores `transport.bucket` from the effective peer transport selection.
It overrides with `ss-{caller_session.berth_id[:16]}`.

**Hub `_download_peer_file` for Dropbox** uses `transport.bucket`.

**`member_transport_announcement`** is member-scoped and has a signed `bucket` field.
That was a reasonable B7 step, but it is not precise enough for berth storage routing.

## Proposed Vocabulary

### Cloud Account Locator

Participant-scoped shared metadata saying "this participant has an account or endpoint at provider P."
This is the existing shared `cloud_storage` row in NoteToSelf.
It contains provider locator fields such as `protocol`, `url`, and OAuth client metadata.
It does not contain device-local secrets.

### Device Cloud Credential

Device-local auth material for a cloud account locator.
This is the existing `local.cloud_storage_credential` row in NoteToSelf local storage.
Sibling devices may know the account exists but still lack credentials for it.

### Local Berth Cloud Allocation

The Manager's local decision that this participant stores one berth at one provider-facing location.
This record ties a `berth_id` to a local `cloud_storage_id` and a provider-facing `location`.

This record is for the local participant's own writes.
It may live in NoteToSelf because it references NoteToSelf cloud account rows.
For non-NoteToSelf team berths, the `berth_id` is still the team DB berth ID resolved by the Hub session.
NoteToSelf cannot enforce a foreign key to that team DB row, so the design must treat `berth_id` as an opaque stable ID.

Schema sketch:

```sql
CREATE TABLE berth_cloud_allocation (
    id               BLOB PRIMARY KEY,
    berth_id         BLOB NOT NULL,
    cloud_storage_id BLOB NOT NULL,
    location         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (cloud_storage_id) REFERENCES cloud_storage(id)
);
```

V1 should allow at most one allocation per berth.
The exact enforcement can be a unique index if SQLite support is acceptable,
or a Manager invariant checked in provisioning code.
Do not add provider-migration history columns until provider migration exists.
The `id` column remains useful as an operation/audit handle and as a future announcement back-reference even while `berth_id` is unique in v1.

The Manager-facing helper should prefer `(team_name, app_name, cloud_storage_id)` over a raw `berth_id`.
Raw `berth_id` helpers are acceptable for micro tests, but normal provisioning should resolve the same berth a Hub session would resolve.

For S3, the Manager should generate a bucket-safe value such as `ss-{uuid7_hex}`.
The exact format must obey S3 bucket naming rules: lowercase, 3-63 characters, and no underscores.
For providers that do not allow caller-chosen stable names, `location` may start as a requested locator and be finalized during Hub materialization.

### Member Berth Storage Announcement

The team-visible signed statement that tells peers where one member stores readable data for one berth.
This belongs in the relevant team Core DB for team berths.
For NoteToSelf berths, the same concept may live in `NoteToSelf/Sync/core.db`.

This replaces `member_transport_announcement` for berth storage routing.
The key semantic correction is that the announcement is scoped to `(member_id, berth_id)`,
not just `member_id` and not just `berth_id`.

Schema sketch:

```sql
CREATE TABLE member_berth_storage_announcement (
    announcement_id BLOB PRIMARY KEY,
    member_id       BLOB NOT NULL,
    berth_id        BLOB NOT NULL,
    protocol        TEXT NOT NULL,
    url             TEXT NOT NULL,
    location        TEXT NOT NULL,
    announced_at    TEXT NOT NULL,
    signer_key_id   BLOB NOT NULL,
    signature       BLOB NOT NULL,
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE,
    FOREIGN KEY (berth_id) REFERENCES team_app_berth(id) ON DELETE CASCADE
);
```

Canonical signed fields should include every routing field:

- `announcement_id`
- `member_id`
- `berth_id`
- `protocol`
- `url`
- `location`
- `announced_at`
- `signer_key_id`

Canonical bytes should follow the existing transport-announcement convention:
JSON object encoding with `sort_keys=True` and `separators=(",", ":")`.

Selection should mirror the current transport-announcement rule:
sort by `announcement_id` descending and choose the first valid row for `(member_id, berth_id)`.
Because `announcement_id` is UUIDv7, this is equivalent to newest valid announcement.
`announced_at` is display/audit data and is not the ordering authority.
This dependence on UUIDv7 ordering is intentional.
If announcement IDs ever stop being time-ordered, the selection rule must change in the same branch.
The signer must resolve to a currently trusted device key for `member_id`.
The trust-chain rule is unchanged from `select_effective_member_transport`.
Invalid or no-longer-trusted rows remain inert data.

## Storage Flow

### Own Writes

When an app calls a cloud-file endpoint with a valid session:

1. The Hub resolves the session to `participant_id`, `team_id`, `app_id`, and `berth_id`.
2. The Hub looks up the `berth_cloud_allocation` for `session.berth_id`.
3. If no allocation exists, the Hub returns a structured missing-location error.
4. If an allocation exists, the Hub joins to `cloud_storage` and `local.cloud_storage_credential`.
5. If the device has no usable credential for the selected account, the Hub returns a structured missing-credentials error.
6. If the provider-facing location is not materialized yet, the Hub materializes it before the operation.
   Materialization is idempotent.
7. The Hub builds the storage adapter using the finalized `location`.
8. The Hub never synthesizes a provider-facing name from `berth_id`.

The Hub does not need to re-read the team Core DB on every file operation.
The session was opened only after resolving the berth from the appropriate DB,
and the session row carries the resolved `berth_id`.
The file path uses that session `berth_id` to look up the local allocation in NoteToSelf shared storage,
then joins to NoteToSelf local credentials.

If a team Core berth is later removed while an old session still exists,
that is a general stale-session problem rather than a cloud-location join problem.
Future session revocation or revalidation can address it.
For this design, orphaned NoteToSelf allocation rows are inert unless a valid session resolves to the same `berth_id`.

### Provider Materialization

The Manager records desired or finalized provider-facing locations.
The Hub materializes them.

For example, for S3 the Manager may allocate a bucket name and record it in `berth_cloud_allocation`.
Then a Manager-authorized Hub operation creates the bucket and applies the public-read policy needed by the current peer-read model.
The Manager should not perform S3 or Dropbox writes directly.

Tentative policy: materialization is lazy but explicit.
The Manager writes the allocation when the human or workflow decides the berth should use cloud storage.
The Hub materializes that recorded allocation on `/cloud/setup` or on the first storage operation that needs it.
The Manager UI may call `/cloud/setup` immediately after allocation as a validation step.
Team creation, app activation, and session open do not pre-materialize storage.

Materialization failures are not `cloud_location_missing`.
If an allocation row exists but provider setup fails, the Hub should return a separate provider/materialization failure.
This keeps "Manager has not provisioned this berth" distinct from "the remote provider is unavailable or rejected setup."

The current `/cloud/setup` endpoint should be re-specified or replaced so it materializes the provisioned location.
It should not imply that the Hub can invent a default bucket.

### Provider Reconciliation Feedback

The common pattern is:

1. Manager records desired service state.
2. Hub reconciles that state with the provider.
3. Hub reports the provider result back to Manager.
4. Manager persists any durable outcome and decides the user-facing repair path.

Some providers accept a Manager-chosen locator.
For S3, the requested bucket name can be the final location if bucket creation succeeds.
For Dropbox, the requested folder prefix may simply become usable on first write.
Other providers may return a provider-issued locator during materialization.
For example, a future GDrive allocation may need Hub to create a folder and return the provider's folder ID.

The Hub should therefore return structured materialization outcomes such as:

- `materialized`: requested location is ready
- `materialized_with_locator`: provider returned a final locator that Manager must persist
- `needs_user_action`: provider requires OAuth, quota repair, account settings, or another human step
- `failed`: provider rejected setup or was unavailable

If materialization returns a final locator different from the requested one,
Manager must persist that final locator before publishing any peer-visible announcement.
An announcement should be published only after the Hub has successfully materialized the corresponding location.
A peer-visible announcement pointing at an unmaterialized location is a defect.

### Team Creation And Bootstrap

Team creation should not pre-allocate cloud storage for every berth.
Creating a team creates the Core berth and whatever app berths the user explicitly activates.
Those berths may be valid but storage-missing until Manager provisions allocations for them.

This means a newly created team can be locally valid but not yet syncable.
That is acceptable and should be surfaced as repairable provisioning state.
The first implementation slice should decide which Manager workflows immediately allocate storage for the Core berth,
but the architecture should not require eager allocation for every possible app berth.

### Peer Reads

When a Hub reads from a peer for the current session berth:

1. The Hub uses `session.berth_id` as the target berth.
2. The Hub selects the peer's newest valid `member_berth_storage_announcement` for `(peer_member_id, session.berth_id)`.
3. The Hub builds a peer-read adapter from the announcement's `protocol`, `url`, and `location`.
4. The Hub never overrides the peer location with caller-session state.

Provider-specific auth remains adapter-specific.
S3 currently models peer reads as anonymous reads from public buckets.
Dropbox currently uses this device's own Dropbox credentials for a shared account.
The design should state the provider rule instead of hiding it in `_download_peer_file`.

Open question: should S3 peer reads continue to require public-readable buckets in the medium term?
The current model uses public-read buckets because confidentiality is expected to come from encryption rather than bucket ACLs.
This branch should document the current rule but does not need to solve private-bucket delegation.

### Same-Member Sibling Reads

Remote reads from another device of the same member should use the announcement path,
not this device's local allocation.
Device A's local allocation describes where device A writes.
Device B may have written the same berth to a different account or location.

Therefore any read of remote/synced berth data should select `member_berth_storage_announcement`
for `(target_member_id, session.berth_id)`, even when `target_member_id` is the local member.
Local own reads from this device's selected storage location are still own-storage reads.
Sync/pull reads from a sibling device are announcement reads.

## Error Shape

Use one machine-readable response family for "valid session, but Manager provisioning is incomplete."
This is close to app-bootstrap behavior, which currently uses HTTP 409 and a JSON body with `error` and `reason`.

Proposed for cloud-file endpoints:

```json
{
  "error": "cloud_storage_required",
  "reason": "cloud_location_missing"
}
```

and:

```json
{
  "error": "cloud_storage_required",
  "reason": "cloud_credentials_missing"
}
```

HTTP 409 is the initial recommendation because it already represents a repairable Small Sea state conflict in session bootstrap.
The final design may choose another status code, but it must be consistent across upload, download, setup, runtime artifact, signal, and peer paths.
Do not return a generic 500 or an ambiguous 404 for missing provisioning.

Provider setup failure should use the same response family with a distinct reason, for example:

```json
{
  "error": "cloud_storage_required",
  "reason": "cloud_materialization_failed"
}
```

The exact `detail` fields may carry provider diagnostics,
but callers must be able to branch on the stable `reason`.

## GDrive Note

Do not treat current `cloud_storage.path_metadata` as the berth location.
In the current GDrive adapter, `path_metadata` is a mutable path-to-file-id cache.
It is adapter state, not the root provider-facing location.

A future GDrive berth location might be a folder ID, `appDataFolder`, or some other provider-specific root descriptor.
The path cache should remain separate unless a later GDrive design deliberately replaces it.

## Legacy Cleanup

`team_device(protocol, url, bucket)` remains legacy admission-time routing metadata.
It should not gain new semantics.

The removal trigger is:

1. own writes resolve through `berth_cloud_allocation`,
2. peer reads resolve through `member_berth_storage_announcement`,
3. admission and bootstrap flows publish or prompt for the new storage announcement,
4. micro tests cover no-announcement and invalid-announcement behavior without relying on `team_device` fallback.

Until those are true, legacy fallback may remain as a compatibility bridge.
Any retained fallback must be named and tested as legacy behavior.

## Scope

### In Scope For This Branch

- Settle the terminology and authority boundaries in this plan.
- Update Hub and Manager specs to describe the vocabulary above.
- Sketch exact schema for `berth_cloud_allocation` and `member_berth_storage_announcement`.
- Decide the error response shape for missing location and missing credentials.
- Identify which existing code paths synthesize provider-facing names.
- Write small proof code only if it clarifies a disputed design point.

### Not In Scope For This Branch

- Broadly rewriting Hub storage adapter construction.
- Fully replacing `member_transport_announcement`.
- Removing `team_device` fallback.
- Building Manager web UI for berth cloud allocation.
- Implementing multi-location failover or backup.
- Adding new cloud providers.
- Migrating existing data.
- Making real internet calls in tests.

## Follow-Up Implementation Slices

### Slice A: Own-Storage Allocation

- Add `berth_cloud_allocation` schema and migrations.
- Add Manager provisioning helpers.
- Change Hub own cloud-file paths to resolve allocation plus credentials.
- Return structured errors for missing allocation and missing credentials.
- Update `/cloud/setup` and first-use storage paths so they lazily materialize the recorded location.
- Return structured provider/materialization errors distinct from missing allocation.
- Return provider-issued final locators to Manager when a protocol cannot use the requested locator directly.

### Slice B: Member-Berth Storage Announcements

- Add signed `member_berth_storage_announcement` types and canonical bytes.
- Publish an announcement from the local allocation only after the Hub has successfully materialized that allocation.
- Select newest valid announcement for `(member_id, berth_id)`.
- Change peer reads to use the selected announcement for all protocols.
- Use valid announcements before legacy fallback; legacy fallback is allowed only when no valid announcement exists.

### Slice C: Legacy Removal

- Stop writing transport fields onto `team_device`.
- Remove `team_device` transport fallback.
- Revisit or close #123 and #102 under the new model.

### Slice D: Manager UX

- Let humans choose which cloud account backs a berth.
- Generate provider-facing locations.
- Surface missing-location and missing-credential repair actions.

## Validation For The Design

A skeptical reviewer should be able to trace these stories without guessing:

1. A valid session can exist before cloud storage is provisioned.
2. A missing own-location is reported as intentional repairable state.
3. A missing device credential is distinct from a missing location.
4. A provider/materialization failure is distinct from both missing states.
5. Alice and Bob can store the same berth in different clouds.
6. A peer read is scoped to `(peer_member_id, session.berth_id)`.
7. A same-member sibling read uses the announcement path.
8. Team-visible storage routing for team berths lives in `{Team}/SmallSeaCollectiveCore`.
9. NoteToSelf is used only for participant-scoped account and local allocation state.
10. The Hub performs provider I/O but does not invent provider-facing storage names.
11. GDrive path metadata is not mistaken for a berth storage location.
12. Peer-visible announcements are published only after materialization succeeds.

## Later Micro Tests

These are not all required in this design branch,
but they should drive the first implementation slices:

- A valid session with no allocation returns `cloud_location_missing`.
- An allocation whose cloud account lacks local credentials returns `cloud_credentials_missing`.
- An allocation whose provider setup fails returns `cloud_materialization_failed`.
- A provider-issued final locator is persisted before any announcement is published.
- S3 own writes use the stored allocation location, not `ss-{berth_id[:16]}`.
- Dropbox own writes use the stored allocation location, not a member-derived prefix.
- Two members announce different locations for the same berth and peer reads select by `(member_id, berth_id)`.
- A same-member sibling read selects by `(local_member_id, berth_id)` instead of using this device's local allocation.
- An invalid storage announcement never routes to its announced location.
- A no-announcement peer read either fails cleanly or uses an explicitly named legacy fallback until that fallback is removed.

## Non-Negotiable Invariants

1. The Hub must not synthesize provider-facing storage names from `berth_id`.
2. Manager-owned provisioning choices must be explicit durable state.
3. Team berth peer-routing state belongs in the team Core DB.
4. Peer storage announcements are member plus berth scoped.
5. A valid session and a provisioned cloud location remain separate conditions.
6. Missing location and missing credentials are distinct failures.
7. Materialization failure is distinct from missing location.
8. Team creation and app activation do not require cross-product storage preallocation.
9. Peer-visible announcements must point only at successfully materialized locations.
10. Valid announcements take precedence over legacy fallback.
11. The Hub performs cloud I/O.
12. Tests use local services such as MinIO and do not call real cloud providers.
13. Use "micro tests" terminology throughout.
