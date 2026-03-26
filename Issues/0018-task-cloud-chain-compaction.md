---
id: 0018
title: Cloud chain compaction (rebase to new initial snapshot)
type: task
priority: low
---

## Background

The cloud view of a niche is a single `latest-link.yaml` pointing to a chain
of delta bundles: one initial-snapshot bundle plus zero or more incremental
bundles, each describing commits since the previous tip. Any client that has
the previous tip can apply the latest incremental bundle without fetching the
full history.

Over time the chain grows unboundedly. Compaction means: create a new
initial-snapshot bundle from the current HEAD, upload it as the new chain
root, and delete the old bundles + link files that are no longer reachable
from the new root.

## Why bother

- Storage cost: old bundles accumulate in cloud storage indefinitely.
- Clone cost: a fresh clone (`cod clone`) must walk the entire chain back to
  the original initial-snapshot. A long chain means many round-trips and
  downloads even for content that's been superseded many times over.

## The happy path

1. Create a full bundle from HEAD (no prerequisites — this is the new
   initial-snapshot).
2. Upload the new bundle as `B-{new_uid}.bundle`.
3. Write a new `initial-snapshot` link pointing to the new bundle.
4. Atomically replace `latest-link.yaml` so it chains back to the new root
   rather than the old one. The `expected_etag` CAS mechanism can guard this.
5. Delete old bundles and link files.

Step 4 and 5 need care — see edge cases below.

## Edge cases

**In-flight clones.** A client that started a clone just before compaction may
be walking the old chain. If old link files (`L-*.yaml`) are deleted before
the client finishes, it gets a broken chain error. Options:
- Soft-delete: mark old links as deprecated but keep them for a grace period
  (e.g. 7 days) before hard-deleting.
- Accept that clones in progress during compaction may fail and need to retry.

**Concurrent pushes.** If another client pushes while compaction is in
progress, the new bundle will chain from the pre-compaction tip. The
compaction must either:
- Abort and retry (detect via CAS conflict on `latest-link.yaml`), or
- Re-run the chain walk after acquiring the lock.

The CAS on `latest-link.yaml` already handles this for the atomic swap — a
concurrent push will have changed the etag, causing the compaction write to
fail cleanly.

**Bundle prereq validity.** After compaction, the first incremental bundle
pushed on top of the new root will list the compacted HEAD as its prerequisite.
This is fine — the prereq is a commit SHA, not a link UID.

## Trigger policy (open question)

When should compaction fire? Options:
- Manual / operator-invoked only.
- After every N pushes (tracked in the link supplement).
- When the chain exceeds a storage or depth threshold.

Starting with manual/explicit is safest. Automated policy can be layered on.

## References

- `packages/cod-sync/cod_sync/protocol.py` — `push_to_remote`, `fetch_chain`,
  `build_link_blob`
- Issue 0019 — git history squashing (complementary: compaction covers the
  cloud chain; 0019 covers the local git history)
