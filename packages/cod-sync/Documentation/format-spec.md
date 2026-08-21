# Cod Sync Format Specification

## 1. Overview

Cod Sync is a chain-of-deltas protocol for sharing git repositories via dumb cloud storage (S3, Dropbox, etc.).
It works by uploading git bundles (snapshots or incremental deltas) to cloud storage and maintaining a linked chain of metadata files ("links") that describe the bundle sequence.

The protocol requires no server-side logic beyond basic file storage.
All intelligence lives in the client.
Concurrency between writers is handled via compare-and-swap (CAS) on a single mutable head file.

The current Google Drive adapter is experimental and non-conforming for writable Cod Sync.
It remains available for functional testing, but Drive names are not unique, so its lookup-then-create path cannot make the first publication atomic.
Do not use it for real Cod Sync data until creation is keyed by a provider-enforced unique ID.

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

## 3. Link Schema

A link is a YAML mapping with exactly six top-level keys:

```yaml
version: 2.0.0
link_id: 0123456789abcdef
head: <main commit object id>
bundle_id: fedcba9876543210
previous:
  link_id: 1111111111111111
  head: <previous main commit object id>
extensions: {}
```

### `version`
The semver format version of the writer that created this link. See Section 4.

### `link_id`
This link's own random identifier, matching the `L-{uid}.yaml` it is archived under.
Every real link, including the first in a chain, has a random identifier.
There is no sentinel identifier for the initial link.

### `head`
The commit object id this link publishes. It always means `refs/heads/main`;
version 2 transports one branch and has no branch list.

### `bundle_id`
The `B-{uid}.bundle` carrying this head's objects.

### `previous`
`null` for the first link in a chain, whose bundle is a full snapshot with no Git prerequisite.
Otherwise exactly `{link_id, head}`, naming the link this one extends and the head that link published.

An incremental bundle must build on `previous.head`: that commit must be among the bundle's actual Git prerequisites.
A merge legitimately produces additional prerequisites, which are acceptable only when `previous.head` already contains them —
so anyone who can satisfy the declared prerequisite can satisfy the whole bundle.
A prerequisite outside that history is a hidden dependency and the chain is rejected.

An initial bundle must have no prerequisites at all.

### `extensions`
The one open part of the format. Unknown keys inside `extensions` are accepted, preserved
through a decode/encode round trip, and covered by canonical signing.
Unknown *top-level* keys are rejected.

The reserved key `signatures` maps a teammate id to `{device_public_key, signature}`.
The canonical signed bytes are the whole mapping with only `extensions.signatures` removed.

## 4. Versioning Rules

Each link carries its own format version. This is a semver string (e.g. `"2.0.0"`).

Rules:
- **Per-link versioning**: each link records the format version of the writer that created it. A chain may contain links written by different versions.
- **Monotonically non-decreasing**: traversing the chain forward, version numbers must never decrease. A writer must not produce a link with a lower version than the chain head.
- **MAJOR bump = breaking change**: a reader that encounters an unsupported major refuses the chain and reports that an upgrade is needed. This is distinct from a corrupt-link error: the bytes may be perfectly well-formed for a newer Cod Sync.
- **MINOR/PATCH bump = additive**: new keys inside `extensions`. A change affecting traversal, validation, or adoption semantics requires a new major.

Version 2 is the only supported major. There is no version-1 compatibility reader:
nothing is deployed, so a migration shim would add machinery without preserving real data.

## 5. CAS Semantics

`latest-link.yaml` is the only mutable file and the concurrency control point.

### Write Protocol (Publish)

1. Create the git bundle `B-{uid}.bundle` with create-only semantics. A uid collision fails rather than replacing bytes.
2. Create the archived link `L-{uid}.yaml`, also create-only.
3. Conditionally write `latest-link.yaml`, the serialization point:
   - First publication: create-only, so one of two racing first publishers wins and the other receives a conflict.
   - Later publication: compare-and-swap against the etag read when the head was inspected.

The head write goes last because it is the only moment a publication becomes visible.
A publication that loses the race leaves only unreferenced write-once objects.

### Forward-Only Publication

Before extending an existing chain, the stored head must be an ancestor of local `refs/heads/main`.
A stored head that is not present locally is fetched through the ordinary chain walk first, because the comparison needs the commit.
That import moves no application ref, no `main`, and no work tree.
Otherwise publication stops before uploading anything and reports that integration is required.
A full snapshot is valid only for an empty store, or for compaction that preserves forward Git ancestry.

Publishing when the stored head already equals local `main`, or already descends from it, is a successful no-op:
nothing is uploaded, the head and its etag are untouched, and no notification is sent.

### Conflict Handling

One publication gets a fixed envelope: at most one head write, and at most two validated observation passes.
An observation pass may fetch and import chain objects, because comparing the stored head to the attempted one needs that commit locally.
Within that envelope Cod Sync neither merges nor retries, and it stops at the application boundary.

The first pass runs before any upload and can end the invocation on its own.
A failed head write buys the second pass, which settles two separate questions:
what the stored history now holds, and whether this invocation's write can still take effect.
The caller does not reread on Cod Sync's behalf; it reads the terminal result.

A publication has five terminal meanings.

| Result | Meaning |
| --- | --- |
| `published` | This invocation's head write was acknowledged. |
| `already_present` | The validated stored head equals or descends from the attempted head, and no head write is open. |
| `retryable` | This invocation's head write was never issued or is closed, and nothing observed needs application integration first. |
| `integration_required` | The validated stored head and the attempted head diverge, and no head write is open. |
| `outcome_unresolved` | This invocation's head write is still open, so it may yet move the stored head whatever the pass observed. |

A head write whose response is lost is `outcome_unresolved` rather than a conflict,
because a blind retry could overwrite a competing publication that actually won.

Whenever a pass observes a divergent stored head, that head is preserved at an immutable ref
under `refs/cod-sync/parked/{observed_link_uid}` before Cod Sync stops.
The publishing process exits with only a result to show for the conflict,
so a later, separate integration operation finds the competing state as a ref rather than refetching and re-deciding it.
Choosing the integration is the application's, not Cod Sync's: the repository may have no work tree,
and discarding the stored head would discard another device's or teammate's commits.

### ETag Semantics

- The Hub returns an etag on every download and upload of `latest-link.yaml`.
- The etag is both the condition on a head write and the evidence that settles a lost one:
   an observation pass whose etag still equals the one the write was conditioned on leaves that write open,
   and a differing etag proves the condition is spent and the write closed.
   A non-empty chain whose head arrives without a comparable etag therefore supports neither a conditional write nor settlement,
   and publication refuses it before uploading anything.
- For `LocalFolderStore` (testing), the etag is the MD5 hex digest of the file content, and its compare-and-replace happens in one locked critical section.
- For conforming cloud backends (S3 and Dropbox), the etag comes from the storage provider's native conditional-write support.
- Google Drive can conditionally update a known file ID, but its current name-based first creation is not atomic and does not satisfy this contract.

## 6. Publish and Fetch Flows

### Publish

1. Read `latest-link.yaml`. Exact absence means an empty store; every other failure leaves the store's state unknown.
2. Empty store: create a full snapshot of `main` and a link with `previous: null`, then write all three objects.
3. Non-empty store: decode the head strictly, confirm its archived copy byte-matches it, and download and inspect its bundle.
   The bundle's advertised head and prerequisites must agree with the link before the chain is extended.
4. If the stored head equals or descends from local `main`, stop and report `already_present`.
5. If it diverges, park it and report `integration_required`; no object is uploaded.
6. Otherwise apply the forward-only rule, create `^<stored-head> refs/heads/main`, inspect the created bundle against the new link, and write.
7. If the head write fails or its response is lost, observe once more and report `retryable`, `integration_required`, or `outcome_unresolved` from what that pass proves about the stored head and about the write's condition.

### Fetch

1. Read `latest-link.yaml`; an empty store means there is no published head to fetch.
2. Download and inspect the latest bundle, always — local possession of the declared commit is not proof that this store published it.
3. If the declared head is present, its prerequisites are present, `bundle verify` succeeds, and the declared ancestry holds,
   no predecessor traversal or import is needed.
4. Otherwise walk newest to oldest until `previous.head` is present locally or a valid `previous: null` link is reached,
   rejecting cycles, missing predecessors, identity mismatches, inconsistent predecessor heads, and version regression.
   Each bundle is downloaded once, into one operation-scoped temporary directory outside every work tree.
5. Import oldest to newest, then confirm each declared head exists and descends from its declared prerequisite.

Fetch creates no remote, remote-tracking ref, `FETCH_HEAD`, or temporary tag.
The only durable ref it can move is a pin the caller asked for, and only forward:
an absent pin is created, an ancestor pin advances, an equal pin is unchanged,
and a pin that already descends from the observed head is retained as stale.
A diverged pin moves nothing and reports that integration is required.

### Cold start

There is no clone operation. A caller inits a repository, fetches, and checks out `FetchResult.observed_head`.

## 7. Chain Compaction

Over time, chains grow long and accumulate orphaned bundles (from failed CAS attempts). Chain compaction addresses both:

1. Walk the current chain to identify all referenced bundle UIDs.
2. Create a fresh full-snapshot bundle from the current state, under a link with `previous: null`.
3. Upload the new snapshot and a new `latest-link.yaml` pointing to it.
4. Unreferenced `L-{uid}.yaml` and `B-{uid}.bundle` files can be garbage collected.

Compaction also serves as the version migration path: compact into the new format, producing a single-link chain in the latest version.

Compaction must preserve forward Git ancestry. A full snapshot that did not contain the previous head would be a chain replacement, not a compaction, and forward-only publication exists to prevent exactly that.

The fresh initial bundle contains the repository state and Git object reachability required by the retention policy.
Compaction does not synthesize a replacement root commit, rebase history, or discard the commit DAG.
Stable commit identities and parent relationships remain available even when older bulk blobs have been dehydrated beyond the live-data window.
That window is not a strong erasure boundary.
It describes what the shared Cod Sync substrate keeps readily rehydratable; any teammate who has already fetched an older snapshot may retain an independent copy.

Core and application repositories may choose different live-data windows.
Constitution event retention is a storage-policy decision above Cod Sync and the event-envelope protocol.
Cod Sync provides reachability and transport; it does not interpret an event as adopted, accepted, final, or safe to prune.

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

Core follows the same forward-only Git rule.
Its storage implementation and event extensions define any additional retention or projection-repair constraints.

## 9. Encryption Envelope

> **Status: Placeholder** — encryption is designed for but not yet implemented.

Design decisions so far:
- Link blobs and git bundles are encrypted as **separate files**, allowing chain traversal (decrypting the small link blob) without downloading the full bundle.
- Both files use the same encryption key for a given chain.
- Cipher selection and key exchange protocol are TBD.
- During the invitation/clone flow, the new teammate receives key material as part of the invitation process (separate protocol, not yet specified).
