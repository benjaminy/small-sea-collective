# Cod Sync Format Specification

## 1. Overview

Cod Sync is a chain-of-deltas protocol for sharing git repositories via dumb cloud storage (S3, Google Drive, Dropbox, etc.). It works by uploading git bundles (snapshots or incremental deltas) to cloud storage and maintaining a linked chain of metadata files ("links") that describe the bundle sequence.

The protocol requires no server-side logic beyond basic file storage. All intelligence lives in the client. Concurrency between writers is handled via compare-and-swap (CAS) on a single mutable head file.

## 2. File Layout

A Cod Sync remote contains three kinds of files:

| File | Mutability | Purpose |
|------|-----------|---------|
| `latest-link.yaml` | Mutable (CAS-protected) | Points to the current head of the chain |
| `L-{uid}.yaml` | Immutable | Archived copy of a link blob |
| `B-{uid}.bundle` | Immutable | Git bundle (snapshot or incremental) |

- `{uid}` is a 16-character hex string (8 random bytes).
- `latest-link.yaml` always contains the same content as the most recent `L-{uid}.yaml`. It exists so readers can find the chain head without scanning.
- Bundles and archived links are write-once. Only `latest-link.yaml` is ever overwritten.

## 3. Link Blob Schema

A link blob is a YAML file containing a 4-element list:

```yaml
# [link_ids, branches, bundles, supp_data]

- [new_link_uid, prev_link_uid]       # link_ids
- [[branch_name, head_sha], ...]      # branches
- [[bundle_uid, {branch: prereq_sha, ...}], ...]  # bundles
- {cod_version: "1.0.0", ...}         # supp_data
```

### `link_ids` (index 0)
A 2-element list: `[this_link_uid, previous_link_uid]`. For the initial snapshot, both are `"initial-snapshot"`.

### `branches` (index 1)
A list of `[branch_name, commit_sha]` pairs representing the branch heads at the time of this push.

### `bundles` (index 2)
A list of `[bundle_uid, prerequisites]` pairs. `prerequisites` is a dict mapping branch names to the commit SHA the bundle was created against. For the initial snapshot, the prerequisite is `"initial-snapshot"`.

### `supp_data` (index 3)
A dict of supplementary data. Currently defined keys:

- `cod_version` (required): Semver string indicating the format version of this link. See Section 4.

## 4. Versioning Rules

Each link blob carries its own format version in `supp_data.cod_version`. This is a semver string (e.g., `"1.0.0"`).

Rules:
- **Per-link versioning**: each link records the format version of the writer that created it. A chain may contain links written by different versions.
- **Monotonically non-decreasing**: when traversing the chain forward (oldest to newest), version numbers must never decrease. A writer must not produce a link with a lower version than the chain head.
- **MAJOR bump = breaking change**: a reader that encounters a link with a higher major version than it supports must refuse to process the chain and prompt the user to upgrade.
- **MINOR/PATCH bump = additive**: new fields in `supp_data`, new optional elements. Old readers ignore fields they don't recognize.

When a reader traverses the chain backward and encounters a link whose major version exceeds its own, it raises an error rather than silently misinterpreting the data.

## 5. CAS Semantics

`latest-link.yaml` is the only mutable file and the concurrency control point.

### Write Protocol (Push)

1. Upload the git bundle `B-{uid}.bundle` (immutable, no conflict possible).
2. Upload the archived link `L-{uid}.yaml` (immutable, no conflict possible).
3. Conditionally write `latest-link.yaml` using compare-and-swap:
   - If the remote was empty (first push), use a "create-only" / `upload_fresh` semantic.
   - If updating an existing chain, provide the etag of the `latest-link.yaml` that was read during the fetch step. The write succeeds only if the file hasn't changed since that read.

### Conflict Handling

If the CAS write fails (409 Conflict), the pusher must:
1. Re-fetch `latest-link.yaml` to get the new chain head and its etag.
2. Merge the remote changes with local changes (standard git merge).
3. Create a new bundle and link blob against the updated chain head.
4. Retry the CAS write with the new etag.

This retry loop guarantees linearizability of the chain without requiring server-side locking.

### ETag Semantics

- The Hub returns an etag on every download and upload of `latest-link.yaml`.
- For `LocalFolderRemote` (testing), the etag is the MD5 hex digest of the file content.
- For cloud backends (S3, GDrive, Dropbox), the etag comes from the storage provider's native conditional-write support.

## 6. Push / Fetch / Clone Flows

### Push

1. `get_latest_link()` returns `(link, etag)` or `(None, None)`.
2. If the remote is empty, create a full snapshot bundle. Otherwise, create an incremental bundle from the prerequisite commit.
3. Call `upload_latest_link(link_uid, blob, bundle_uid, bundle_path, expected_etag=etag)`.
4. On 409, re-fetch and retry (see Section 5).

### Clone

1. `get_latest_link()` to find the chain head.
2. Walk backward through the chain (`get_link(prev_uid)`) to collect all links from initial to latest.
3. Apply bundles in forward order: clone from the initial snapshot, then fetch+merge each incremental bundle.

### Fetch

1. `get_latest_link()` to find the chain head.
2. Walk backward through the chain until a known commit is found locally.
3. Apply bundles in forward order from that point.

## 7. Chain Compaction

Over time, chains grow long and accumulate orphaned bundles (from failed CAS attempts). Chain compaction addresses both:

1. Walk the current chain to identify all referenced bundle UIDs.
2. Create a fresh initial-snapshot bundle from the current state.
3. Upload the new snapshot and a new `latest-link.yaml` pointing to it.
4. Unreferenced `L-{uid}.yaml` and `B-{uid}.bundle` files can be garbage collected.

Compaction also serves as the version migration path: compact into the new format, producing a single-link chain in the latest version.

Any user with write access to the cloud storage can trigger compaction. There is no admin/permission distinction at this layer.

## 8. Encryption Envelope

> **Status: Placeholder** — encryption is designed for but not yet implemented.

Design decisions so far:
- Link blobs and git bundles are encrypted as **separate files**, allowing chain traversal (decrypting the small link blob) without downloading the full bundle.
- Both files use the same encryption key for a given chain.
- Cipher selection and key exchange protocol are TBD.
- During the invitation/clone flow, the new member receives key material as part of the invitation process (separate protocol, not yet specified).
