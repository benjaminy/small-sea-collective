> Migrated to GitHub issue #9.

---
id: 0024
title: Clean up cloud storage adapter abstraction in Hub backend
type: task
priority: medium
---

## Problem

The Hub backend has two tiers of cloud storage access that are not unified:

**Own-bucket access** is cleanly abstracted. `_make_storage_adapter(ss_session)`
returns a `SmallSeaS3Adapter` or `SmallSeaDropboxAdapter` that implements a
common interface (`upload`, `download`, `ensure_bucket_public`, etc.). Adding a
new protocol means implementing the adapter and plugging it into
`_make_storage_adapter`.

**Peer-bucket access** is not abstracted at all. `_download_peer_file` does its
own protocol dispatch with inline S3-specific boto3 code:

```python
row = conn.execute("SELECT protocol, url, bucket FROM peer ...").fetchone()
protocol, url, bucket = row
if protocol == "s3":
    import boto3
    ...  # inline S3 download logic
# No other protocols handled
```

`proxy_cloud_file` (used by `/cloud_proxy` during invitation acceptance) has
the same problem — its own separate S3 blob.

The result: every place that reads from *any* cloud bucket (own or peer) has
diverged. Adding Dropbox peer access required patching `_download_peer_file`
directly with Dropbox-specific logic rather than just providing a Dropbox
adapter.

## What to build

Extend the storage adapter interface to cover anonymous/peer reads, so
`_download_peer_file` and `proxy_cloud_file` can be rewritten as:

```python
adapter = _make_peer_adapter(protocol, url, bucket, credentials=None)
ok, data, etag = adapter.download(path)
```

`_make_peer_adapter` constructs the right adapter from the peer's protocol/url/bucket
and optionally the session's own credentials (needed for Dropbox, which has no
anonymous read path).

The existing `_make_storage_adapter` and the new `_make_peer_adapter` can share
the same adapter classes — the difference is only in how credentials and bucket
are sourced, not in the download logic itself.

## Scope

- Refactor `_download_peer_file` and `proxy_cloud_file` to use adapter objects
- Move S3 and Dropbox download logic out of inline code and into their adapters
- The adapter interface should be sufficient to implement `_download_peer_file`
  for any protocol the Hub already supports

## Out of scope

- Adding new storage protocols
- Changing the HTTP API surface
- Changing how `_make_storage_adapter` works for own-bucket writes

## References

- `packages/small-sea-hub/small_sea_hub/backend.py` — `_download_peer_file`,
  `proxy_cloud_file`, `_make_storage_adapter`, `SmallSeaS3Adapter`,
  `SmallSeaDropboxAdapter`
- Issue 0010 — Hub permissions (separate concern but same file)
