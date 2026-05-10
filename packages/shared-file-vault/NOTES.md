# What to steal from other file-sync projects

Working notes on file-sync systems that resemble Vault, what is worth borrowing from each, and what to leave behind.
The goal is not to adopt any of these wholesale; Vault has its own structural commitments (Hub-as-gateway, Cod Sync over user-owned cloud, decentralized team membership via Cuttlefish/Wrasse Trust, niche-as-git-repo).
The goal is to harvest specific ideas that match those commitments and avoid known footguns.

This is a working reference, not a design doc.
Sections marked **first pass** are early sketches that need a second look before acting on them.

## At a glance

| Project | Closeness to Vault | Status | One-line take |
|---|---|---|---|
| Unison | medium | active, niche | Pairwise replica reconciler. Best ideas are about *how to talk to humans* about filesystem changes, not the algorithm. |
| Syncthing | medium | very active, millions of users | Multi-peer p2p. Wrong transport assumption (peers must overlap online), but mature ignore/conflict/versioning UX. |
| git-annex | high | active, real users since ~2012 | Closest structural match. Git-for-metadata, pluggable encrypted remotes, multi-peer redundancy tracking. Major design overlap. |
| Keybase / KBFS | low (now) | mostly frozen post-Zoom | Team-scoped, e2e-encrypted, but server-mediated. Mostly historical reference. |
| Resilio Sync | low | proprietary, real users | Multi-peer like Syncthing but closed-source and tracker-based. Skip. |
| Perkeep (Camlistore) | low | semi-active, niche | Content-addressed personal storage. Different shape. |
| Dropbox / iCloud / OneDrive | reference only | dominant SaaS | Useful for what users *expect*. Not structurally relevant. |

---

## Unison

### What it is, briefly

A pairwise replica reconciler with three pieces worth distinguishing:

- An *archive* per pair of roots that records the last-synchronized state of each path, keyed on `hash(root1, root2)` at archive format version 23.
- A *reconciler* (`recon.ml`) that classifies each path's state on both replicas against the archive and assigns a default action.
- A *transfer* layer (`transfer.ml`) with rsync-style rolling-checksum chunk reuse against the receiver's existing copy of the same file.

### Structural fit with Vault

Low to medium.
The "use Unison as a backend" idea is a long shot for a specific reason: Unison's archive is the *synchronizer's own opinionated memory of last-sync state* at the wrong granularity for a multi-peer model.
For an N-peer team, Unison's model would, in the worst case, want N(N-1)/2 archives.
Vault's per-niche `checkouts.db` plus parked peer refs is structurally simpler than that *and* strictly richer in expressive power: Vault can represent "Alice and Bob disagree with each other," which Unison's pairwise archive literally cannot.

The reconciler is the part Vault should learn from.
The archive is the part Vault should not adopt.
The transfer layer is a transit optimization (not a storage layer) and it is mostly orthogonal to what Vault is doing on top of git.

### Ideas to steal

**1. The decision lattice (highest leverage idea in this whole document).**

For each path, every Unison run produces exactly one of a small set of outcomes:

- **equal** — neither side changed since last sync; do nothing.
- **false conflict** — both sides "changed" but the resulting content is byte-identical; mark synced silently.
- **left-only update** — safe to propagate L→R.
- **right-only update** — safe to propagate R→L.
- **coordinated delete** — gone on both sides; mark synced.
- **conflict** — present to human; do nothing automatically.

That category structure, not any clever data layout, is the deepest idea worth importing.
Vault's parked-ref review should produce these same categories per path before merge.
Once that classification is the explicit data model of the review state, the rest of the UX (counts, filters, "what changes if I take everything safe?", "show me only conflicts") follows mechanically.

**2. False-conflict short-circuit.**

`markEqual`: when two replicas changed the same path to the same content, silently mark synced and never bother the user.
Vault inherits this at the *blob* level via git for free.
The interesting version is at the *workflow* level: "Alice's commit X touches paths I also touched, but the resulting tree is byte-identical to mine → auto-merge with no review noise."
This is the difference between a noisy parked-review surface and a quiet one.

**3. Catastrophic-delete guardrail (`confirmbigdel`).**

Unison refuses to propagate a change that empties an entire replica or top-level path without confirmation, and aborts in batch mode.
Cheap idea, big payoff.
Vault should refuse to publish a commit that deletes more than some threshold percent of tracked paths without explicit confirmation, and refuse to merge a peer commit that does the same, even if git would accept it cleanly.
Motivating scenario: a teammate `rm -rf`s their checkout, runs `publish`, and ten people lose their files at the next pull.

**4. Atomic-group predicate.**

Unison has an `atomic` preference: a pathspec for directories whose contents are treated as a unit during reconciliation.
Targets: `.app` bundles, `.docx` zip rewrites, SQLite + WAL pairs, package directories, photo bundles.
Without this concept, sync constantly catches half-written package directories mid-edit.

**5. Cross-platform path-hygiene as a single object.**

Unison's `case.mli` exposes one object with `compare`, `hash`, `normalizePattern`, `caseInsensitiveMatch`, `normalizeFilename`, `badEncoding`.
Decades of scar tissue boil down to: pick a normalization mode at sync-pair-creation time, store it in the archive, never let one side's view of a path silently shadow the other's.
Vault should have one of these, not scattered ad-hoc Unicode logic.

**6. Three-way merge with last-synced ancestor.**

Unison's `merge` preference invokes an external program (kdiff3, diff3, etc.) on `CURRENT1`, `CURRENT2`, and `CURRENTARCH` — the last common version, kept via the `backupcurrent` preference.
Vault gets the common ancestor for free from git.
Wiring up a per-extension external merge command (`*.txt`, `*.md`, `*.json`) for conflict resolution is straightforward and high-value.

### Things to avoid

- The pair-archive granularity. See "structural fit" above.
- Treating the synchronizer's last-sync memory as user-visible state.
- The "ignore = invisible to sync" model without the rename footgun closure (see below).

### Footguns Unison surfaces

**The rename-with-ignored-children footgun.**
From Unison's own caveats section:

> If a directory D contains an ignored child P on the local replica only, and D is renamed to D' on the remote replica and propagated, P is **deleted**.
> Unison sees rename as delete-plus-create; the create does not include the invisible children, since they are invisible to it.

Vault needs to either inherit this footgun knowingly or close it.
A likely closure: at the user-facing layer, `status` and `publish` warn about ignored children inside any renamed directory.
At the git layer, `.gitignore` produces the deletion behavior anyway, so Vault must not rely solely on git-level ignores for safety.

**Conflict preservation in Unison is *not* the Dropbox pattern.**
Worth being explicit: Unison does **not** write `Alice's conflicted copy.txt` files.
On a conflict it either propagates one direction (with optional `.bak` of the overwritten content) or it leaves the path alone and reports the conflict in the UI.
The side-by-side conflicted-copy filename pattern is Dropbox/iCloud/Syncthing, not Unison.
Vault should pick consciously between these, not import the choice as folklore.

**No baked-in default ignores.**
Unison ships zero default ignore patterns; the `.DS_Store`, `Thumbs.db` lists are folklore from sample profiles.
Borrow the *machinery* (Name/Path/Regex pathspec, `ignore`/`ignorenot`); Vault owns the default list.

---

## Syncthing

### What it is, briefly

A multi-peer, fully decentralized file-sync app.
Each peer maintains a complete replica of each shared folder.
Peers connect directly when both online, with optional public relay servers when direct connection fails, and a global discovery service for finding peers by device ID.
No central authority over data.
Mature: millions of installs, well past prototype, active development since ~2014.

### Structural fit with Vault

Medium.
The multi-peer + conflict-aware + decentralized-membership stance is the most Vault-shaped thing in the wild right now.
The transport assumption is the wrong fit: Syncthing assumes peers can establish concurrent network connections (directly or via relay), so two devices that are never online at the same time will never sync.
Vault's store-and-forward via user-owned cloud storage is a deliberate departure from this.

Use Syncthing as a reference for *human-facing* design (ignore syntax, conflict naming, versioning policies, folder-type semantics), not for transport.

### Ideas to steal — first pass

**1. Conflict file naming convention.**

Syncthing's pattern: `<filename>.sync-conflict-<YYYYMMDD>-<HHMMSS>-<DEVICEID>.<ext>`.
This is the Dropbox-style "keep both" pattern, but with much more useful metadata baked into the filename: sortable by time, attributable to a specific peer, and the original extension is preserved so file associations still work.
If Vault offers a "keep both as side-by-side files" conflict resolution action at all, this is the naming convention to use.

**2. Per-folder versioning policies.**

Syncthing offers several versioning modes per folder, configured separately:

- *Trash can* — overwritten/deleted files go to a hidden `.stversions/` directory.
- *Simple* — keep N versions per file.
- *Staggered* — keep dense versions recent, sparse versions older.
- *External* — run a user-supplied script.

Vault gets "all history" from git for free, but the *user surface* of "show me last week's version of this file" is a separate UX concern.
Worth thinking about whether Vault wants a built-in "view at commit X" workflow or a `.vault-versions/` materialized view.

**3. `.stignore` ignore file with `#include` and per-pattern flags.**

Syncthing's per-folder `.stignore` file supports:

- `#include <other-file>` for shared pattern sets — directly relevant to Vault's "team profile + local profile" layering question.
- `!pattern` for explicit overrides (same as Unison's `ignorenot`).
- `(?d)pattern` — marks files matching the pattern as *deletable when needed to satisfy a peer-originated directory delete*. Without `(?d)`, a directory containing ignored OS junk like `.DS_Store` will block the peer's delete from being applied locally; with `(?d)`, Syncthing may remove the ignored junk so the parent delete can succeed. This is essentially Syncthing's mitigation for Unison's rename-with-ignored-children footgun (rename surfaces as delete + create), framed at the file level instead of the rename level.
- `(?i)pattern` — case-insensitive marker.

The `(?d)` flag is the most interesting cross-reference here: it is a different shape of solution to the same family of "ignored files inside a directory whose existence on disk is being decided by sync" problem that Unison's caveats describe, and Vault needs a story for it one way or the other.

(For the *separate* feature of "don't propagate peer deletes at all on this folder," Syncthing has a folder-level `ignoreDelete` advanced setting, distinct from the ignore-pattern flags. Probably not what Vault wants — silently swallowing peer deletes is a recipe for divergence — but worth knowing it exists.)

**4. Receive-only and send-only folder types.**

Syncthing exposes "receive only" and "send only" as explicit folder types per device.
A receive-only folder will reject local edits and revert them to peer state.
Maps roughly to Vault's read-only/read-write berth permission, but at a per-niche-per-device granularity.
Vault already has this conceptually via Wrasse Trust roles; Syncthing's contribution is the *UX of marking the folder type explicitly in the device's local view* so the user is never surprised by reverts.

**5. Folder ID independent of folder name and path.**

Syncthing folders have a stable ID (a GUID-ish string).
Each device mounts the folder at a path of its choosing, with a label of its choosing.
Same idea as Vault's niche-with-name + per-device checkout-path.
Worth confirming Vault's identity model can survive a niche being renamed by one peer — Syncthing's split makes this trivial.

**6. Three-layer architecture: discovery, transport, content.**

Syncthing separates: global discovery server (find peers by device ID) → relay servers (NAT traversal fallback) → direct block-level transport (BEP).
Three independent concerns with independent failure modes.
Small Sea has an analogous split: Hub + cloud storage + future Hub-to-Hub direct paths.
The architectural pattern of "discovery is a separate concern from transport" is the same shape; Syncthing has a decade of experience operating it.

**7. "Out of sync" status with peer attribution.**

Syncthing reports per-folder per-peer status: "up to date", "syncing", "out of sync — N items differ", with drilldown.
Vault's analogue is per-niche per-peer state described in the per-peer-sync-memory section below.
Syncthing's lesson: surface *why* something is out of sync (specific peer, specific paths) — users will ask.

### Things to avoid

- The "always-online to sync" assumption. Syncthing wants peers concurrently reachable; if devices are offline at different times, sync stalls until they overlap. Vault's store-and-forward via cloud is a deliberate departure.
- Block-level p2p protocol BEP — it's an in-band block exchange, structurally wrong for Vault.
- The lack of a commit graph. Syncthing's "versioning" is per-file-with-policies; it doesn't have a commit graph. Vault's git layer is strictly more expressive.
- Watcher-based mtime rescan as the primary update mechanism. Vault is checkout-and-publish-driven, which is healthier for correctness and for large folders.

### Footguns Syncthing surfaces

- File-level versioning is *not* a substitute for atomic-group operations. Half-saved Office files create version-history noise; Syncthing users routinely complain about this.
- Same rename-with-ignored-children class of bug as Unison.
- "Out of sync" state without a clear *why* surface is frustrating; users spend time guessing which device or path is the disagreement source. Vault should bake the why in from the start.
- Default conflict-file behavior accumulates `.sync-conflict-...` files in the working tree, which then themselves sync to peers and accumulate further. There needs to be an explicit cleanup workflow.

---

## git-annex

### What it is, briefly

A git extension that tracks file *content* outside of git as content-addressed blobs, with the git history tracking *metadata* about which content exists and which "remotes" hold copies.
Pluggable special remotes (S3, WebDAV, Box, Dropbox, rsync, Glacier, Internet Archive, dozens more) provide arbitrary cloud-storage-as-backend.
Encrypted special remotes use per-file keys derived from a master key, with optional chunking.
Multi-peer, decentralized, no central server in the data path.
Real users since ~2012, scientific archives, photo/video libraries, personal archival use.
Maintained by Joey Hess.

### Structural fit with Vault

High.
This is the closest structural match to Vault by a wide margin.
Several Vault design choices could be re-derived from a careful study of git-annex:

- Git for metadata. Same.
- Cloud storage as transport via pluggable backends. Same shape as Cod Sync over user-owned cloud.
- Encrypted blobs at the storage layer. Same goal.
- Multi-peer with each location having its own clone. Same.
- Selective materialization (each location may not have all content). Same as Vault's residency modes (remote-only / cached / checked out).

The one big departure: git-annex stores all content *outside* git as keyed blobs.
Vault's current scope keeps content *inside* git as commits.
These are different trade-offs (git-annex wins for huge binaries; Vault wins for small/medium text-heavy folders), and the right answer depends on workload — see the large-files discussion below.

The other big departure: git-annex has no team / membership / governance model.
You wire up remotes ad hoc per-user.
This is exactly the gap Small Sea is filling.

### Ideas to steal — first pass

**1. `numcopies` and redundancy tracking.**

git-annex tracks how many copies of each file's content exist across known remotes.
You set policies — `numcopies = 3` means "at least 3 copies must exist before you may drop a local copy."
git-annex refuses to drop content that would violate the policy, and warns when the policy is unmet.

This is high-value for Vault.
A team of N peers caching the same niche should be able to assert "at least 3 copies of every file's content must exist somewhere we can see" and have Vault refuse the operation that would violate it.
Maps cleanly to Vault's residency modes: a `cached` niche can become `remote only` only if redundancy is preserved.

**2. Trust levels per remote.**

git-annex assigns each remote one of four trust levels:

- *trusted* — believed when it claims to have content.
- *semitrusted* (default) — claims of presence are believed; absence triggers re-verification.
- *untrusted* — claims must be verified before being acted on.
- *dead* — assume the remote is permanently lost; don't count it for redundancy.

Lets you mark "Alice's archive cloud" as trusted and "Bob's flaky home NAS" as semitrusted.
Vault's trust model is currently uniform across peers in a team; per-remote (or per-cloud-storage-account) trust is an interesting lever.

**3. Special remote interface.**

The special-remote protocol is small and well-shaped: `GET key`, `PUT key`, `CHECKPRESENT key`, `REMOVE key`.
Cod Sync's transport interface should align with something like this if it isn't already.
The lesson is to keep the cloud-storage-facing API at the level of "store and retrieve opaque keyed blobs" — never assume directory structure, never assume listing semantics, never assume atomic rename.

**4. Per-remote preferred-content expressions.**

Each remote can have a small DSL expression saying which files it should hold.
Examples: `include=photos/2025/* and largerthan=0`, `smallerthan=100MB`, `not metadata=archived=yes`.
The remote then materializes only matching files; others are remote-only there.

Highly relevant for Vault.
A phone might want `smallerthan=50MB and not include=raw/*`.
A laptop might want everything in `working/*` materialized but `archive/*` cached only.
A backup NAS wants everything.

This is a much richer story than "checked out vs. cached" applied uniformly to a whole niche.

**5. Required content / locked content.**

Files marked "required" never get dropped from a remote.
Critical for "this niche's `README.md` and top-level config files must always be locally available, even on a low-storage device."

**6. Metadata vs. content split exposed to the user.**

`git annex whereis FILE` shows which remotes hold each file's content.
`git annex info` shows aggregate state: total files, sizes per remote, missing content.
Vault should expose this clearly: "this file's content is on Alice's cloud and Bob's NAS, materialized locally on this device."
The honesty matters — distinguish "according to last-fetched metadata" from "verified just now."

**7. Content-addressed encrypted chunking.**

git-annex's encrypted special remotes encrypt each file's content with a per-file key derived from a shared master key, optionally chunking large files into fixed-size blocks for partial transfer and resumability.
This is the design pattern Vault might need *if* it hits the delta-hostile-binary case (Office docs, PSD, compressed media).
Worth studying as prior art before designing anything from scratch.

**8. `git annex sync --content` semantics.**

"Sync" means: pull peer branches, merge metadata, transfer content according to policy, in that order.
Distinguishing metadata sync from content sync is structural.
Vault has this implicit (registry chain vs. niche chain), but git-annex makes it a first-class operation per niche, which is worth replicating in the CLI/UX.

**9. `git annex assistant`.**

A daemon-with-watcher that converts git-annex from a CLI power-tool into a Dropbox-feel "drop files in a folder, they sync" experience.
Worth studying for the UX layer over the lower-level primitive — Vault will want a similar "watch the checkout, auto-publish on debounced quiet" mode eventually.

### Things to avoid

- The "all content lives outside git as keyed blobs" architecture, by default. Vault has explicitly chosen "small/medium files in git, content tracked as commits." git-annex's approach is right when you have huge binaries; it's overkill (and complicates merge semantics) when you don't. It becomes relevant *only* if Vault decides to take on the delta-hostile-binary case.
- The lack of a team / membership / governance model. git-annex remotes are wired up ad hoc. Don't import the gap.
- Direct client-to-cloud transfers without a Hub-style gateway. Vault's Hub abstraction is strictly more important for the security model than git-annex's direct-to-remote approach.

### Footguns git-annex surfaces

- Version migrations (V5 → V6 → V7 → V8) are painful when you have many remotes to update in lockstep. Vault's pre-alpha "no compat layer" stance is the right answer for now, but version markers in the niche schema are necessary to keep future migrations possible.
- Confusion between "metadata says content exists somewhere" and "content is currently fetchable." Network failures can leave the metadata-truth and content-truth divergent. Vault needs to be careful about the `whereis` UX honesty.
- Conflict resolution on the *metadata* git branch can cascade — if two peers add the same key with different contents, you get a metadata conflict that needs manual repair. Vault inherits the same risk for the niche-registry SQLite-in-git design.
- Special remote backends differ in atomicity, rename semantics, and listing performance. git-annex has accumulated a per-remote workarounds layer. Cod Sync should expect to do the same.

---

## Keybase Filesystem (KBFS) — research pile, not yet expanded

Mostly frozen since the Zoom acquisition, but a handful of design ideas are worth keeping in the pile even though the project itself is below Syncthing and git-annex in priority.

Things worth coming back to when there's reason:

- **Server-honesty via published Merkle tree.** Every state change is signed, chained, and published into a global Merkle tree the client can audit. Even though Keybase ran the servers, clients could detect a lying server. The pattern is broader than that one architecture: "every state is signed and chained; replay verifies" is exactly the property Cod Sync wants from cloud-storage providers it doesn't trust. Worth comparing to git's existing chain-of-commits property and asking whether Vault needs anything additional.
- **Conflict resolver as a distinct component.** KBFS had a rule-based automatic conflict resolver that handled common cases without prompting, with an explicit "go to a conflict branch" fallback when rules didn't apply. The architectural separation — resolver as a thing you can replace per workload — is interesting design language for Vault even if the specific rules aren't.
- **Per-folder key with rotation on membership change.** Each TLF (Top-Level Folder) had its own key; membership changes triggered key rotation. Directly relevant to Wrasse Trust's key rotation patterns; worth confirming Vault's per-niche key story aligns or learns from KBFS's specifics.
- **Conflict semantics on simultaneous writes.** KBFS handled the case where two devices write the same file while offline by creating a per-device conflict view at sync time. Different shape from Unison's per-path conflict marking and Syncthing's `.sync-conflict-` files; worth a closer read.

Not yet expanded into a full template section because the project is dormant and the design docs are scattered.
Promote when there is a specific Vault question (key rotation semantics, server-honesty audit story, conflict resolver architecture) that this pile would inform.

---

## Cross-cutting takeaways

The single most valuable cross-cutting ideas, with attribution:

1. **The decision lattice for parked-ref review** (Unison). The category structure — equal / false-conflict / one-side update / coordinated-delete / conflict — is the explicit data model the review state should be built on.

2. **Catastrophic-delete guardrail** (Unison `confirmbigdel`). Refuse to publish or merge a commit that deletes more than some threshold without explicit confirmation.

3. **Numcopies-style redundancy tracking** (git-annex). Per-niche policy that asserts "at least N copies of each file's content must exist across known peers/remotes." Vault refuses operations that would violate it.

4. **Per-peer / per-remote preferred-content expressions** (git-annex). A small DSL for "which files does this device want materialized." Generalizes residency modes from per-niche to per-path-within-niche.

5. **Atomic-group predicate** (Unison). Pathspec for directories whose contents are treated as a unit during reconciliation (`.app`, `.docx`, package directories).

6. **Layered ignore profiles with `#include` and per-pattern flags** (Syncthing). Team profile + local profile, with `!` overrides and a `(?d)`-style "this ignored file may be deleted to satisfy a peer-originated directory delete" marker (which is how Syncthing closes the rename-with-ignored-children footgun at the file level).

7. **Conflict-file naming with peer attribution** (Syncthing). If Vault ever offers "keep both" as a conflict-resolution action, the filename should include time and originating peer ID.

8. **Trust levels per remote / per peer** (git-annex). Lever for distinguishing reliable archives from flaky devices when computing redundancy.

9. **Per-extension external 3-way merge** (Unison `merge` preference). Wire up text-friendly external mergers; Vault gets the common ancestor from git for free.

10. **Folder-ID independent of folder name and local path** (Syncthing). Probably already true for niches; worth confirming.

## Per-peer sync memory: Vault is structurally richer than what's out there

Vault already has local peer sync state in `checkouts.db`.
That feels like the right analogue to Unison's archive memory or Syncthing's per-peer state, but at commit/ref granularity rather than per-path replica snapshots.

A point worth claiming, narrowed: Vault's per-peer-per-niche memory is *strictly richer* than Unison's pairwise archive at representing **divergent peer histories**.
Unison's archive collapses to one truth per pair of roots; Vault's parked refs let it say "I see Alice and Bob disagreeing with each other," and replay either side's history independently.

Compared to Syncthing, the comparison is by axis, not strict ordering:

- Vault is richer at **branchy, replayable peer history** — the commit graph, the ability to merge / fork / examine an old peer state.
- Syncthing is richer at **live per-file availability and block-level state** — per-path version vectors, block lists, a global model of which peer has which blocks of which file right now.

These carry different information for different purposes.
Vault's design choice favors the first axis because human-paced collaboration with merge moments cares more about replayable history than block-level availability.

The useful product question is:

> What does this device believe about Alice's registry head and Alice's head for this niche?

That should be a first-class status concept, driving clearer UI labels:

- no fetched data from Alice;
- fetched Alice at commit X;
- Alice commit X is ready to merge;
- Alice commit X is already merged;
- Alice has signaled newer data since the last successful fetch.

## Cross-platform path hygiene

Borrow Unison's `case.mli` shape: a single `ops` object with `compare`, `hash`, `normalizePattern`, `caseInsensitiveMatch`, `normalizeFilename`, `badEncoding`.
Pick a normalization mode at niche creation, store it durably, never let one side's view of a path silently shadow the other's.

Research and micro tests should still cover:

- Unicode normalization collisions;
- case-insensitive path collisions;
- Windows reserved names and characters;
- symlink behavior;
- executable bit behavior;
- package-directory behavior on macOS;
- AppleDouble files;
- extremely long paths;
- path separator normalization.

The strategic question is whether Vault is "shared files for humans" or "faithful filesystem replica."
The former can have a much smaller and safer policy surface.

## Large mutable files: transit vs. storage are separate problems

Three layers worth keeping distinct:

- **Transit-time deltas** — rsync's rolling-checksum approach reuses bytes the receiver already has of *the same file*. Unison does this. Git pack files do their own version of this between revisions.
- **On-disk content addressing** — chunked, deduplicated, content-hashed storage of file contents independent of file identity. This is git-annex, git-LFS, restic, IPFS territory.
- **Delta-hostile binary formats** — Office docs (zip-based with shifted internal offsets), PSD, compressed media, proprietary CAD. Neither transit deltas nor xdelta saves you. Only content-addressed chunking with strategy-specific block boundaries helps, and even then the gain depends on the format.

If/when Vault decides to handle large delta-hostile binaries, the design move is *not* "borrow Unison's transit layer."
It is "content-addressed chunked storage outside the niche's git history, with manifests in git" — which is essentially what git-annex does.

The benchmark plan should bucket explicitly:

- Many small text files — git is fine.
- Large append-only or delta-friendly files (logs, plain text growing) — git is fine.
- Large delta-hostile binaries — separate design; earn its complexity with measurements.
- Replaced (not edited) media — fine for either approach; bytes move once.
- Package directories with many generated files — `atomic` predicate problem, not a delta problem.

## Open questions surfaced by this read

- Where does Vault's atomic-group predicate live — team profile, local profile, or both? `.app` bundles are universal; `.sqlite-wal` pairings might be app-specific.
- How does `status` surface ignored-children-inside-renamed-directories without being noisy on the common case?
- What is Vault's catastrophic-delete threshold default, and is it per-niche or per-team policy?
- Is per-remote / per-peer trust a Vault concept at all, or does Cuttlefish/Wrasse Trust subsume it?
- Does Vault adopt a preferred-content expression DSL, or stay with whole-niche residency modes for now?
- For external 3-way merge: is the per-extension command list a team profile concern or a local-only convenience?
- How does Vault avoid the metadata-vs-content honesty problem (whereis says yes, fetch fails)?

## Near-term recommendation

Keep Vault on Git/Cod Sync.
The next useful work is not adopting any of these whole; it is writing down Vault's file semantics, importing the highest-leverage cross-cutting ideas, and proving the rough edges with micro tests.

Suggested next branch:

1. Define the parked-update review data model around the per-path decision lattice (equal / false-conflict / one-side-update / coordinated-delete / conflict).
2. Define a structured ignore profile format (Name / Path / Regex), layered team + local, with `#include` and per-pattern "don't propagate delete" flag.
3. Add default ignore patterns for OS/editor junk.
4. Add micro tests for ignored files, dirty checkout behavior, path collisions, and the rename-with-ignored-children case specifically.
5. Add a catastrophic-delete guardrail on `publish` and on parked-merge apply, with a documented threshold.
6. Define an atomic-group predicate and apply it to at least `.app` bundles and `.docx` files in the default profile.
7. Sketch (not implement) a `numcopies`-style redundancy concept for niches and decide whether it earns a follow-on branch.
8. Benchmark git bundle and pack behavior for the four representative buckets above (small text, large append-only, delta-hostile binary, replaced media), and decide whether on-disk content-addressed chunking earns a future branch.
