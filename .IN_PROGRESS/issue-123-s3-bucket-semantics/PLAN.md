# Branch Plan: S3 Member Transport Bucket Semantics

**Branch:** `issue-123-s3-bucket-semantics`
**Base:** `main`
**Primary issue:** #123 "Decide S3 member transport bucket semantics"
**Related context:** #102 (member transport configuration), #111 (app-bootstrap sightings)
**Kind:** Policy decision plus narrow code and test update.

## Problem Statement

`MemberTransportAnnouncement` has a signed `bucket` field.
For S3, the Hub ignores it: `_download_peer_file` overrides with `ss-{ss_session.berth_id.hex()[:16]}`,
where `ss_session` is the **caller's** session, not the peer's.
For Dropbox, `transport.bucket` is used as the folder prefix and is authoritative.

This asymmetry is a latent bug and a source of semantic confusion:

- In the single-device test that covers this path, alice downloads from herself,
  so her berth_id and the peer's berth_id coincide.
  The test passes but does not exercise a real multi-device scenario.
- In a real two-device scenario, the Hub would look in the caller's S3 bucket
  instead of the peer's — silently wrong.
- The test is named `test_peer_download_uses_announced_endpoint_and_session_berth_bucket`
  and asserts `data == b"berth"` (the fallback), even when a valid announcement exists.
  This is the wrong behavior documented and enshrined as a test.

The comment at `backend.py:1459` reads:
> "Legacy peer transport metadata may still point at the Core berth bucket,
>  so peer reads for ordinary app sessions derive the bucket from the current session berth."

This was written to address a pre-#111 concern: Vault impersonated `SmallSeaCollectiveCore`,
so old announcements could carry the Core berth bucket.
Issue #111 removed that impersonation.
The concern is no longer live.

## Decision

Make `bucket` authoritative for S3, matching Dropbox behavior.
The Hub always routes to `transport.bucket` from the effective peer transport selection,
regardless of protocol.
The S3 bucket override and its stale comment are removed.

**Why this is correct:**

- `transport.bucket` already carries the right value for S3.
  Manager's `_bucket_name_for_protocol` correctly derives `ss-{berth_id.hex()[:16]}`
  from the **peer's** berth ID at announcement time.
- The Dropbox path has always been authoritative; S3 should match.
- The signed announcement is the right authority for peer routing.
  Overriding it with caller-session state violates the transport announcement contract.
- The pre-#111 concern is gone.

**What stays unchanged:**

- `bucket` stays in the `MemberTransportAnnouncement` dataclass and schema.
- `bucket` stays in the signed canonical bytes (no signature format change).
- The derivation formula `ss-{berth_id.hex()[:16]}` stays in Manager;
  it just becomes the authoritative value written into announcements
  rather than a Hub-side override.
- Legacy fallback path (`_legacy_transport_for_member`) is unchanged;
  it already carries the peer's bucket from `team_device`.

## Scope

**In scope:**

- Remove the S3 bucket override in `backend.py:_download_peer_file`
- Remove the stale comment at `backend.py:1459–1461`
- Unify the S3 and Dropbox paths to both use `transport.bucket`
- Update `test_peer_download_uses_announced_endpoint_and_session_berth_bucket`:
  rename it and flip the assertion to confirm the announced bucket wins
- Add a companion micro test verifying that when the announcement is invalid,
  the legacy fallback bucket is used instead
- Update `packages/small-sea-hub/spec.md` if it describes S3 peer routing
- Note the change in architecture.md if it touches transport routing semantics

**Out of scope:**

- Changing `MemberTransportAnnouncement` schema or signature format
- Changing Manager's `_bucket_name_for_protocol` derivation
- Any migration shim for old announcements (pre-alpha, none needed)
- Bucket lifecycle (creation, public-access policy) — unchanged
- Dropbox path — already correct, no change needed

## Key Files

- `packages/small-sea-hub/small_sea_hub/backend.py` — `_download_peer_file` (~line 1454)
- `packages/small-sea-hub/tests/test_peer_transport.py` — test to rename and update
- `packages/small-sea-hub/spec.md` — check for S3 routing description
- `architecture.md` — check for transport routing prose

## Validation

The goal is to convince a skeptic that:

1. **The goals of the branch are accomplished:**
   - The renamed test asserts `data == b"announced"` — the Hub now routes to the
     announced bucket, not the caller's berth bucket.
   - A second micro test verifies that an invalid/unsigned announcement falls back
     to the legacy bucket (not the announced one).
   - A third micro test (existing or new) verifies that when no announcement exists
     at all, the legacy fallback is used.
   - All three cases are distinguishable in the test: different bucket names,
     different file contents.

2. **General repo integrity is maintained:**
   - The Dropbox path is unchanged — a reader can diff the two branches of the
     `if protocol == "s3" / elif protocol == "dropbox"` block and see them
     converge to the same `transport.bucket` usage.
   - `_bucket_name_for_protocol` in Manager is untouched — the announced value
     it writes is now trusted end-to-end.
   - Signature verification is unchanged.
   - All existing passing micro tests continue to pass.
   - Full test suite: `uv run pytest packages/small-sea-hub/tests` and
     `uv run pytest packages/small-sea-manager/tests packages/shared-file-vault/tests`.

## Non-Negotiable Invariants

1. An invalid or unsigned announcement must never route to the announced bucket.
   Only the legacy fallback is used when verification fails.
2. The Hub must not read the peer's berth_id directly from any DB;
   it gets all peer routing information from the transport selection result.
3. No new internet calls, no new dependencies.
4. Tests use MinIO (local); no real S3.
5. Use "micro tests" terminology throughout.
