# Branch Plan: Berth Cloud Location Provisioning Semantics

**Branch:** `issue-134-berth-cloud-location-semantics`
**Base:** `main`
**Primary issue:** #134 "Define Manager-owned berth cloud location provisioning semantics"
**Blocked issues:** #123, #114, #102, #10, #9, #16
**Kind:** Design pass — written design, schema sketches, spec updates,
and a narrow code change to make the Hub fail cleanly on missing cloud location.
Not a full implementation of all provisioning flows.
**Reference docs:** `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md`,
`architecture.md`

## Why This Branch Exists

Issue #123 exposed that fixing S3 bucket semantics requires settling a larger question first.
The codebase currently conflates several distinct concepts:

- **Cloud account**: a cloud provider endpoint plus credentials (S3 key pair, OAuth tokens).
  Currently participant-scoped. `cloud_storage` + `cloud_storage_credential` in NoteToSelf.
- **Storage location**: where a specific berth's data actually lives on a provider.
  Currently synthesized at runtime by the Hub from `berth_id` as `ss-{berth_id[:16]}`.
  Never explicitly provisioned or stored.
- **Peer storage announcement**: how teammates know where to read a member's berth data.
  Currently `member_transport_announcement` (member-scoped, carries `bucket` field).
  Bucket is authoritative for Dropbox but ignored for S3 (Hub overrides with own derivation).
- **Legacy transport metadata**: `team_device(protocol, url, bucket)` — admission-time fallback.
  Marked temporary; still in use.

The core problem: the Hub invents storage names from `berth_id` rather than resolving
a location that Manager explicitly provisioned.
This makes narrower changes (like #123) unsafe — fixing one override risks cementing another.

## Current State Summary

**Hub `_get_cloud_link`**: reads `cloud_storage LIMIT 1` — takes any first account,
no berth filtering.

**Hub `_make_s3_adapter`**: derives bucket as `ss-{session.berth_id[:16]}`.
The berth_id appears only in the session; the derived name is never stored anywhere.

**Hub `_make_dropbox_adapter`**: derives folder prefix as `ss-{member_id[:16]}`.
Also synthesized, never stored.

**Hub `_download_peer_file` (S3)**: ignores `transport.bucket` from announcement;
overrides with `ss-{caller_session.berth_id[:16]}`.
Bug in multi-device scenario: uses caller's berth, not peer's.

**Hub `_download_peer_file` (Dropbox)**: uses `transport.bucket` correctly.

**`member_transport_announcement.bucket`**: signed but semantically ambiguous for S3.

## Design Working Proposal

This section presents the model to validate and refine during the branch.
If the design changes during work, update here before touching code.

### Three-layer model

**Layer 1 — Cloud account** (participant-scoped, unchanged shape)

`cloud_storage` + `cloud_storage_credential` in NoteToSelf.
Represents "I have an account at provider P with these credentials."
One participant may have multiple accounts.
No berth specificity here.
The Manager UI manages these (existing `add_cloud_storage` / `list_cloud_storage`).

**Layer 2 — Berth cloud location** (berth-scoped, new)

A provisioned allocation that links a specific berth to a specific cloud account and
names a provider-facing location within that account.
Stored in NoteToSelf shared DB.

Proposed schema:

```sql
CREATE TABLE berth_cloud_location (
    id               BLOB PRIMARY KEY,  -- allocation UUID (uuid7)
    berth_id         BLOB NOT NULL,
    cloud_storage_id BLOB NOT NULL,
    location         TEXT NOT NULL,     -- opaque provider-facing name:
                                        -- S3 bucket, Dropbox folder prefix, GDrive folder ID
    created_at       TEXT NOT NULL,
    FOREIGN KEY (cloud_storage_id) REFERENCES cloud_storage(id)
);
```

`location` is chosen by the Manager at provisioning time — a UUID, not derived from `berth_id`.
This is the key change: storage names become Manager-allocated, not Hub-synthesized.

For S3: `location` is the bucket name (e.g. a random UUID, or a human-readable slug
the Manager generates and records).
For Dropbox: `location` is the folder prefix.
For GDrive: `location` absorbs the current `path_metadata` field (removes a special case).

One berth may have at most one active location per provider in v1.
The `id` field allows future multiple-location rows without schema changes.

**Layer 3 — Berth storage announcement** (berth-scoped, synced, new or evolved)

How teammates learn where to read a member's berth data.
This replaces `member_transport_announcement` for storage-routing purposes,
or the `bucket` field becomes explicitly berth-scoped and authoritative.

Two options to evaluate:

**Option A — Evolve `member_transport_announcement`:**
Rename to `berth_transport_announcement` and key it by `berth_id` instead of `member_id`.
The `bucket`/`location` field becomes authoritative for all protocols.
`member_id` is retained for trust verification (the signer must be that member's device key).

**Option B — Split the concepts:**
Keep `member_transport_announcement` for transport-layer metadata (how to reach a peer's Hub).
Add a separate `berth_storage_announcement` for storage location (where to read data).

The distinction matters if transport endpoint (Hub address) and storage location
can diverge — e.g., a member changes cloud provider but keeps the same Hub.
Both announcements would be signed by a device key of the announcing member.

The branch should settle this question and record the reasoning.

### Hub resolution (after this branch)

When a file operation arrives for a session:
1. Hub looks up `berth_cloud_location` for `session.berth_id`.
2. If no row exists: return a clean, explicit error (see below). No fallback synthesis.
3. If found: resolve credentials from `cloud_storage` + `cloud_storage_credential`,
   use the stored `location` to address the provider.
4. Hub never calls `f"ss-{berth_id[:16]}"` as a default bucket name.

For peer reads:
1. Hub resolves the peer's berth storage announcement for the target `berth_id`.
2. Uses the announced `location` — authoritative for all protocols.
3. No session-berth override.

### Error shape for missing cloud location

When a session is valid but no cloud location is provisioned for the berth,
the Hub should return a structured, distinguishable error — not a generic 500 or 404.

Proposed: HTTP 422 with `detail: "cloud_location_missing"` (or similar machine-readable key),
mirroring the pattern used for `participant_berth_missing` / `team_berth_missing`
in session bootstrap.

### Legacy cleanup

`team_device(protocol, url, bucket)` legacy fallback:
Once berth storage announcements cover the routing use case, this fallback can be removed.
That removal is a follow-up, not in scope here — but the design should identify
what specifically triggers the removal.

`cloud_storage.path_metadata`:
Absorbed into `berth_cloud_location.location` for GDrive.
The existing field stays until GDrive provisioning is updated in a follow-up.

## Scope

**In scope (design pass):**

- Written answers to all questions posed in issue #134
- Schema definition for `berth_cloud_location` in NoteToSelf shared DB
- Decision on Option A vs Option B for peer storage announcement
- Updated `spec.md` for Hub and Manager reflecting the new three-layer model
- Hub change: replace bucket synthesis with `berth_cloud_location` lookup;
  return a clean error when no location is provisioned
- A Manager provisioning function `add_berth_cloud_location(root, participant_hex, berth_id, cloud_storage_id, location)` — enough to make the Hub resolution testable
- Micro tests for:
  - Hub fails cleanly with `cloud_location_missing` when no berth cloud location row exists
  - Hub routes to the stored location when a row exists
  - Peer read uses the announced location, not a synthesized name

**Out of scope:**

- Manager web UI for berth cloud location provisioning (#10)
- Multi-location failover or backup
- New cloud providers
- Migrating existing data (pre-alpha, no shims)
- Removing `team_device` legacy fallback (follow-up)
- Full berth storage announcement implementation if Option B is chosen
  (design + schema + one happy-path test is enough for this branch)

## Key Files

- `packages/small-sea-hub/small_sea_hub/backend.py` — `_get_cloud_link`, `_make_s3_adapter`,
  `_make_dropbox_adapter`, `_download_peer_file`
- `packages/small-sea-hub/spec.md` — Cloud Storage section (~line 157)
- `packages/small-sea-manager/small_sea_manager/provisioning.py` — `add_cloud_storage`,
  `_bucket_name_for_protocol`, `announce_member_transport`
- `packages/small-sea-manager/spec.md` — NoteToSelf schema section (~line 979)
- `packages/wrasse-trust/wrasse_trust/transport.py` — `MemberTransportAnnouncement`
- `packages/small-sea-hub/tests/test_peer_transport.py` — existing S3 peer tests
- `packages/small-sea-manager/tests/` — provisioning micro tests

## Validation

The design is complete when a skeptical reviewer can trace this story end-to-end
in code and micro tests — no prose required:

1. Manager provisions a cloud account (`add_cloud_storage`).
2. Manager provisions a cloud location for a berth (`add_berth_cloud_location`),
   recording a Manager-generated location name (not `ss-{berth_id[:16]}`).
3. An app obtains a Hub session for that berth.
4. The app stores a file through the Hub; the Hub routes to the provisioned location.
5. A peer retrieves the berth storage announcement; the announced location matches step 2.
6. The Hub reads the peer's file using that announced location.
7. A micro test shows that if step 2 is skipped, the Hub returns `cloud_location_missing`,
   not a generic error and not a silently wrong synthesized bucket.

Additional integrity checks:
- `grep -r "ss-{.*berth_id" packages/` returns no results in Hub core code
  (only in tests that explicitly exercise the removal).
- All existing passing micro tests continue to pass.
- Full suite: `uv run pytest packages/small-sea-hub/tests packages/small-sea-manager/tests packages/shared-file-vault/tests`.

## Non-Negotiable Invariants

1. The Hub must not synthesize a storage name from `berth_id` at runtime after this branch.
2. A valid session with no provisioned cloud location must produce a clean, intentional error.
3. The Manager is the sole authority for allocating provider-facing location names.
4. Peer routing uses the announced location; no session-berth override.
5. Existing signature verification for transport announcements is unchanged or strengthened.
6. Tests use MinIO and local MinIO only; no real cloud calls.
7. Use "micro tests" terminology throughout.
