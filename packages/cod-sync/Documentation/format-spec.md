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

The fresh initial bundle contains the repository state and Git object reachability required by the retention policy.
Compaction does not synthesize a replacement root commit, rebase history, or discard the commit DAG.
Stable commit identities and parent relationships remain available even when older bulk blobs have been dehydrated beyond the live-data window.
That window is not a strong erasure boundary.
It describes what the shared Cod Sync substrate keeps readily rehydratable; any teammate who has already fetched an older snapshot may retain an independent copy.

Core has an additional retention invariant: every current Core database snapshot produced by a clone includes the complete Constitution evidence that clone has adopted into its live database.
That evidence is application data in the database, not something reconstructed from Git authorship or pruned historical checkouts.
Adopted Constitution objects and their declared causal closures are non-prunable in later states produced by that clone.
This does not guarantee that the clone or any physical copy survives.
It is also an invariant Cod Sync asks a well-behaved clone to maintain, not one a peer can verify: an object never adopted and an object adopted and dropped are indistinguishable from outside.
Separable PII payloads and fetched-but-unadopted parked input do not inherit that retention merely by arriving.

The checkpoint rule that would permit safe window advancement past a long-unseen teammate is not yet specified.
A signed staleness observation can warn that advancement is approaching and preserve what an observer knew, but it is not itself finality or pruning authority.

Any user with write access to the cloud storage can trigger compaction. There is no admin/permission distinction at this layer.

## 8. Forward Restoration and Replay

Cod Sync never repairs a shared history with `git reset`, a backward ref move, a rebase, or a replacement root.
Restoration is a forward operation.

Given a selected old commit `B` and the current head `H`, a repair appends a new descendant `R` of `H` whose tree contains the chosen old state.
The commits between `B` and `H` remain ancestors of `R`.
That preserved interval is the inventory of changes overwritten by the restoration.

The repair workflow may then append new commits that replay desirable intervening work.
When an application supports replay, replay commits should retain machine-readable provenance naming the source commits, paths, records, or application operations they reproduce and the certainty that provenance provides.
A repair manifest should distinguish:

- changes intentionally omitted;
- changes replayed without conflict;
- mixed or causally ambiguous changes requiring human review;
- application or external side effects that Git cannot reverse.

The exact manifest format and app-facing replay interface remain open.
Cod Sync supplies commit reachability, stable identities, and the ability to publish a forward repair series.
It does not prove who authored a Git change, whether a replay is honest, or whether a content-level operation remains valid.
Applications own those semantics and may offer user-directed restoration, author-asserted replay, or application-defined cryptographically attributable replay.

A repair series may be prepared locally and published in one Cod Sync update so peers do not treat the temporary old-content tree as the completed repair.
Every commit in the published series remains ordinary inspectable Git history.

Core follows the same forward-only Git rule with an extra constraint.
A Core restoration must not publish a database that omits Constitution objects that clone adopted after `B`.
Core repair appends repudiation, reconciliation, or ratification evidence and rebuilds a named local projection while preserving the complete evidence DAG.

## 9. Encryption Envelope

> **Status: Placeholder** — encryption is designed for but not yet implemented.

Design decisions so far:
- Link blobs and git bundles are encrypted as **separate files**, allowing chain traversal (decrypting the small link blob) without downloading the full bundle.
- Both files use the same encryption key for a given chain.
- Cipher selection and key exchange protocol are TBD.
- During the invitation/clone flow, the new teammate receives key material as part of the invitation process (separate protocol, not yet specified).
