# Bootstrap NoteToSelf through Hub-owned transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Context

Identity bootstrap (#58, #63) works end-to-end with `LocalFolderRemote`: the
joining device decrypts the welcome bundle, clones NoteToSelf via CodSync, and
verifies the authorizing device's signature.

But `LocalFolderRemote` only works when both installations share a local
filesystem. In real use, NoteToSelf lives on cloud storage (S3, Dropbox, etc.)
and the Hub is the sole internet-facing component on each device.

The joining device has nothing: no identity, no Hub state, no cloud
credentials, no session. It only has the `remote_descriptor` from the welcome
bundle (`{storage_id_hex, protocol, url, client_id, path_metadata}`).

### How invitation acceptance works today (for reference)

When Bob accepts an invitation to Alice's team, Bob already has an identity
and a running Hub. His Manager opens a NoteToSelf session with his own local
Hub, then uses `ExplicitProxyRemote` to read Alice's team repo through
`/cloud_proxy`. Bob's Hub uses **Bob's** cloud credentials to proxy the read.

This pattern does not apply to identity bootstrap because the joining device
has no identity, no Hub, and no cloud credentials.

### How `proxy_cloud_file` works per protocol

- **S3**: anonymous read (public bucket, no credentials needed)
- **Dropbox**: uses the session owner's OAuth refresh token to get an access
  token, then reads from the shared Dropbox app folder
- **GDrive**: same pattern as Dropbox (OAuth refresh → access token)

### What the joining device needs

A CodSync remote that can read the NoteToSelf repo from cloud storage. The
repo contains `core.db` (user_device rows, team pointers, cloud_storage
config, etc.) tracked in git via CodSync links and bundles.

## Branch Goal

Make identity bootstrap work through real cloud storage so that the joining
device can clone NoteToSelf without sharing a local filesystem with the
authorizing device.

## The Hard Problem

The joining device needs cloud credentials to fetch NoteToSelf, but the cloud
credentials are *in* NoteToSelf (device-local DB). This is a chicken-and-egg
problem — but only for credential-bearing protocols (Dropbox, GDrive).

For S3 with public-read buckets, there is no chicken-and-egg: the Hub's
`proxy_cloud_file` uses anonymous reads for S3, and the joining device could
do the same without credentials.

## Approach Options (Sketches)

### Option A: Welcome bundle carries bootstrap cloud credentials

The authorizing device includes enough credential material in the welcome
bundle for the joining device to read from cloud storage directly.

- **S3**: only `{protocol, url, bucket}` needed (anonymous read). Already in
  the `remote_descriptor` minus the bucket name.
- **Dropbox/GDrive**: would need `{client_id, client_secret, refresh_token}`
  or a short-lived access token. The refresh token is long-lived and powerful
  — including it in the bundle shares the authorizing device's full Dropbox
  access. A short-lived access token is safer but may expire before the
  joining device uses it.

The joining device would use the Hub's adapter layer (S3Adapter,
DropboxAdapter, etc.) as a **library** — not through a running Hub server.
The adapter classes are already standalone: they take credentials and make
HTTP/S3 calls. They could be used directly from the Manager during bootstrap.

**Pros**: simple, no new infrastructure.
**Cons**: sharing refresh tokens is a real security concern for OAuth
providers. For S3 it's clean.

### Option B: Joining device bootstraps a minimal Hub

The joining device starts a Hub process pointed at a temporary/minimal root
directory, creates just enough state to get a NoteToSelf session, and uses
that Hub's `/cloud_proxy` to fetch NoteToSelf.

The sequence would be:
1. Welcome bundle includes cloud credentials (same as Option A)
2. Joining device creates minimal participant directory + NoteToSelf DBs
3. Inserts cloud storage config + credentials into the DBs
4. Starts Hub (or uses SmallSeaBackend as a library)
5. Creates a NoteToSelf session
6. Uses ExplicitProxyRemote through the Hub to fetch NoteToSelf
7. Replaces the minimal state with the real fetched state

**Pros**: reuses the full Hub infrastructure.
**Cons**: heavyweight for what's ultimately just "read some files from cloud
storage." The Hub's session flow requires OS notifications, PIN confirmation,
etc. — none of which make sense during bootstrap.

### Option C: Extract cloud adapter layer into a reusable module

Factor out the credential-resolution and adapter-construction logic from
`SmallSeaBackend` into a standalone module that can be used without a running
Hub or session.

Something like:
```python
# In a shared location (small-sea-hub or a new shared module)
def make_cloud_reader(protocol, url, bucket, credentials=None):
    """Return a CodSync-compatible read-only remote for cloud storage."""
    if protocol == "s3":
        # anonymous read — no credentials needed
        return S3ReadOnlyRemote(url, bucket)
    elif protocol == "dropbox":
        return DropboxReadOnlyRemote(credentials["access_token"], bucket)
    ...
```

The joining device calls this directly with credentials from the welcome
bundle, getting back a CodSync remote. No Hub session needed.

**Pros**: clean separation, testable, reusable.
**Cons**: duplicates some of the Hub adapter logic (or creates a new
dependency).

### Option D: S3-only for now, defer OAuth

Since S3 uses anonymous public-read and requires no credentials, implement
identity bootstrap through S3 first. Defer Dropbox/GDrive bootstrap to a
later branch when the credential-sharing story is worked out.

The joining device would use a `PublicS3Remote` (already exists in
`cod_sync/testing.py`) or an equivalent to do anonymous reads.

**Pros**: immediate progress on the common path, avoids the hard OAuth
credential-sharing problem entirely.
**Cons**: doesn't solve the full problem. But it proves the transport
architecture and defers only the credential question.

## What we know about bucket naming

The NoteToSelf CodSync bucket name is derived from the berth_id:
`ss-{berth_id.hex()[:16]}`. The `remote_descriptor` in the welcome bundle
currently does not include the bucket name. It needs to — or the joining
device needs another way to derive it.

The berth_id is in the NoteToSelf `team_app_berth` table, which the joining
device doesn't have until after the fetch. So the **authorizing device must
include the bucket name in the welcome bundle's `remote_descriptor`**.

## What's missing from `remote_descriptor` today

Current `remote_descriptor`:
```python
{"storage_id_hex": ..., "protocol": ..., "url": ..., "client_id": ..., "path_metadata": ...}
```

Needed for bootstrap (at minimum):
```python
{"protocol": ..., "url": ..., "bucket": ...,
 # For OAuth providers (future):
 # "bootstrap_access_token": ..., or "client_id": ..., "client_secret": ..., "refresh_token": ...
}
```

## Recommended path for this branch

**Option D (S3-only) + the shared infrastructure from Option C.**

1. Add `bucket` to the `remote_descriptor` in the welcome bundle
2. Make the joining device use S3 anonymous read for bootstrap (reuse/adapt
   `PublicS3Remote` or the `proxy_cloud_file` anonymous-S3 pattern)
3. Make the authorizing device push NoteToSelf through its Hub (not just
   `LocalFolderRemote`)
4. Factor out just enough adapter logic to be usable without a full Hub
   session
5. Prove it with a MinIO integration test
6. Leave OAuth-provider bootstrap as a documented follow-up

This gets the transport architecture right and proves it works, without
getting stuck on the OAuth credential-sharing problem.

## Open Questions for Discussion

1. **Is S3-only acceptable for this branch?** Or do we need at least one
   OAuth provider working?
2. **Should the joining device use the Hub adapter code as a library, or
   should we extract a thinner read-only layer?** The Hub adapters
   (S3Adapter, DropboxAdapter) are already fairly standalone — they just take
   credentials and make calls. But they're defined inside `small-sea-hub`.
3. **Credential sharing for OAuth**: when we do tackle it, is a short-lived
   access token in the bundle acceptable? Or does the joining device need its
   own refresh token (which means its own OAuth flow)?
4. **Does the authorizing-side push also need to go through Hub?** Currently
   `_push_note_to_self_to_local_remote` only supports `localfolder`. The
   authorizing device has a full Hub, so it could push through
   `SmallSeaRemote`. Is that in scope here?
