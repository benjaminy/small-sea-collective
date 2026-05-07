The Unison File Synchronizer seems like a spiritual cousin to Vault.
Certainly closer than Dropbox or OneDrive.

https://github.com/bcpierce00/unison/

A couple questions:

1. Could Unison be adapted to use Small Sea as a "backend"
   This seems like a long shot.
   But if Unison has an internal concept of a whole-folder delta, maybe it could use something like cod-sync with its own deltas instead of git's bundles.

2. At the very least, Vault should almost certainly *cough* borrow good ideas from Unison.
   What are the most promising ideas, maybe around conflict resolution or ignoring dumb files like .DS_Store or anything really?

## Current read

The "use Unison as a Small Sea backend" idea is a long shot, but for a more specific reason than just "pairwise vs. multi-peer."
Unison's archive is the *synchronizer's own opinionated memory of last-sync state*, keyed on `hash(root1, root2)` and stored at archive format version 23.
It is not a transport-shaped delta package that another sync system can swap in for git bundles.
Asking whether Unison could be a backend is roughly like asking whether rsync's delta state could be a backend: the granularity is wrong.

For an N-peer team, Unison's model would, in the worst case, want N(N-1)/2 archives.
Vault's per-niche `checkouts.db` plus parked peer refs is structurally simpler than that *and* strictly richer in expressive power: Vault can represent "Alice and Bob disagree with each other" simultaneously, which Unison's pairwise archive literally cannot.
This is worth claiming as an advantage of the Vault design, not just a non-fit with Unison.

So the recommendation is unchanged in direction: do not pursue Unison-as-backend.
Borrow ideas aggressively instead, and the most valuable ideas are not the algorithm — they are the discipline Unison has about how to talk to humans about filesystem changes.

## What Unison actually is, briefly

Unison is a pairwise replica reconciler with three pieces worth distinguishing:

- An *archive* per pair of roots that records the last-synchronized state of each path.
- A *reconciler* (`recon.ml`) that classifies each path's state on both replicas against the archive and assigns a default action.
- A *transfer* layer (`transfer.ml`) with rsync-style rolling-checksum chunk reuse against the receiver's existing copy of the same file.

The reconciler is the part Vault should learn from.
The archive is the part Vault should not adopt.
The transfer layer is a transit optimization (not a storage layer) and it is mostly orthogonal to what Vault is doing on top of git.

## The decision lattice (highest-leverage idea)

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

## Ideas to borrow, in priority order

### 1. Path-level reconciliation UX (the lattice above)

Vault already has a compatible primitive in `fetch -> parked ref -> merge`.
The next design step is a "review parked changes" state before merge that exposes the lattice:

- paths added, modified, deleted, or renamed by the peer;
- paths that will auto-merge cleanly (the "safe" categories);
- paths that are likely to conflict;
- whether a peer update is already merged, ready to merge, or only hinted by Hub signal count.

This fits Small Sea's human-scale rule: preserve ambiguity and make it visible rather than silently picking a winner.

### 2. False-conflict short-circuit

Unison's `markEqual` step: when two replicas changed the same path to the same content, silently mark synced and never bother the user.
Vault inherits this at the *blob* level via git for free.
The interesting version is at the *workflow* level: "Alice's commit X touches paths I also touched, but the resulting tree is byte-identical to mine → auto-merge with no review noise."
This is the difference between a noisy parked-review surface and a quiet one.

### 3. Catastrophic-delete guardrail (`confirmbigdel`)

Unison refuses to propagate a change that empties an entire replica or top-level path without confirmation, and aborts in batch mode.
This is a $1 idea with $1000 of value.
Vault should refuse to publish a commit that deletes more than some threshold percent of tracked paths without explicit confirmation, and refuse to merge a peer commit that does the same, even if git would accept it cleanly.
The motivating scenario: a teammate `rm -rf`s their checkout, runs `publish`, and ten people lose their files at the next pull.

### 4. Atomic-group predicate

Unison has an `atomic` preference: a pathspec for directories whose contents are treated as a unit during reconciliation.
Macros: `.app` bundles, `.docx` zip rewrites, SQLite + WAL pairs, package directories, photo bundles.
Without this concept, sync constantly catches half-written package directories mid-edit.
Vault will need an analogue or its first user will notice.

### 5. Cross-platform path-hygiene as a single object

Unison's `case.mli` exposes one object with `compare`, `hash`, `normalizePattern`, `caseInsensitiveMatch`, `normalizeFilename`, `badEncoding`.
Decades of scar tissue boil down to: pick a normalization mode at sync-pair-creation time, store it in the archive, never let one side's view of a path silently shadow the other's.
Vault should have one of these, not scattered ad-hoc Unicode logic.

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

### 6. Ignore machinery (but not Unison's default list)

Unison ships **zero** baked-in default ignores.
Grepping the source confirms it: there is no list of `.DS_Store`, `Thumbs.db`, etc. anywhere in the codebase.
Those names are folklore from sample profiles, not from Unison itself.
Borrow the machinery, own the default list as a Vault decision.

The Unison ignore *machinery* worth copying:

- Three pathspec types: `Name <name>` (matches anywhere by basename), `Path <path>` (anchored relative path), `Regex <regex>`.
- `ignore` paired with `ignorenot` for explicit overrides.
- A clear documented order of evaluation (depth-first, parent skips win over child overrides — see footgun below).

Likely Vault default ignore list (Vault's call, not Unison's):

- `.DS_Store`
- `._*`
- `.Spotlight-V100`
- `.Trashes`
- `Thumbs.db`
- `desktop.ini`
- editor backup files like `*~`
- temporary swap files like `.swp` and `.swo`

Open questions:

- Are ignores team-shared state, local-only state, or layered?
  Layered seems right: a team profile for shared policy, plus a local profile for device/editor noise.
- Does `publish` silently skip ignored files, or does `status` show an "ignored" section?
  Showing ignored files somewhere is probably right early; surprise invisibility is dangerous.
- Should ignores be applied before Git sees the files via `.gitignore`, through Vault's own status/publish filtering, or both?
  Git-native ignores are convenient, but Vault should own the user-facing semantics.

### 7. Backup of overwritten content on conflict resolution

When Unison propagates one side's content over the other (after a user resolves a conflict, or via `prefer`/`force`), it can keep the overwritten content as a `.bak` file according to the `backup` and `backuplocation` preferences.
This is *not* the Dropbox/iCloud `Alice's conflicted copy.txt` pattern, which is a separate design lineage (see correction below).
For Vault, the backup-on-overwrite idea is worth borrowing in spirit; the implementation may just be "git already has the old commit reachable, plus a one-shot stash, plus visible UI surfacing of where the overwritten content went."

### 8. Three-way merge with last-synced ancestor

Unison's `merge` preference invokes an external program (kdiff3, diff3, etc.) on `CURRENT1`, `CURRENT2`, and `CURRENTARCH` — the last common version, kept via the `backupcurrent` preference.
Vault gets the common ancestor for free from git.
Wiring up a per-extension external merge command (`*.txt`, `*.md`, `*.json`) for conflict resolution is straightforward and high-value.

### 9. Rsync-style transit deltas

Lower priority because git pack files already do delta compression, but worth knowing the shape: rolling checksums against the receiver's existing copy of the same file before transfer.
This is a transit optimization, distinct from the on-disk question discussed below.

## Specific corrections to earlier draft thinking

### Conflict preservation: Unison's pattern is not Dropbox's

The earlier draft suggested borrowing "keep both with conflict-suffixed filenames" from Unison.
That mixes lineages.
Unison **does not** write `Alice's conflicted copy.txt` files.
On a conflict it either propagates one direction (with optional `.bak` of the overwritten content) or it leaves the path alone and reports the conflict in the UI.
The side-by-side conflicted-copy filename pattern is Dropbox/iCloud, not Unison.
Vault should pick consciously between these, not import the choice as folklore.

For pre-alpha, the conservative Unison-style stance — "do nothing automatically on conflict; show the user the conflict; offer a small set of resolution actions" — is probably the right baseline:

- keep my version (drops peer change);
- take the peer version (drops my change);
- keep both as conflict-suffixed filenames (Dropbox-style; explicit, never the default);
- open an external merge tool for text files;
- leave unresolved and show the blocked paths.

The important rule is unchanged: Vault should never silently resolve content conflicts just because most shared-folder tools try to feel automatic.

### The rename-with-ignored-children footgun

The single most important concrete lesson if Vault adopts "ignore = invisible to sync," from Unison's own caveats section:

> If a directory D contains an ignored child P on the local replica only, and D is renamed to D' on the remote replica and propagated, P is **deleted**.
> Unison sees rename as delete-plus-create; the create does not include the invisible children, since they are invisible to it.

Vault needs to either inherit this footgun knowingly, or close it.
A likely closure: at the user-facing layer, `status` and `publish` warn about ignored children inside any renamed directory.
At the git layer, `.gitignore` produces the deletion behavior anyway, so Vault must not rely solely on git-level ignores for safety.

### Large mutable files: transit vs. storage are different problems

The earlier draft sketched a "Hub stores encrypted content chunks, manifests in git" design under a section about Unison-influenced large-file handling.
That conflates two separate layers:

- **Transit-time deltas** — rsync's rolling-checksum approach reuses bytes the receiver already has of *the same file*. Unison does this. Git pack files do their own version of this between revisions.
- **On-disk content addressing** — chunked, deduplicated, content-hashed storage of file contents independent of file identity. This is git-LFS, restic, IPFS territory. Unison does not do this.

Neither layer helps with the actually painful category: **delta-hostile binary formats**.
PSDs, Office documents (zip-based with shifted internal offsets), proprietary CAD, anything compressed on disk.
Git's xdelta loses on these.
Rsync's rolling checksums lose on these.

The benchmark plan should bucket explicitly:

- Many small text files — git is fine.
- Large append-only or delta-friendly files (logs, plain text growing) — git is fine.
- Large delta-hostile binaries (Office, PSD, compressed media) — neither transit nor xdelta saves you; the question is content-addressed chunking on disk, not Unison-style anything.
- Replaced (not edited) media files — fine for either approach; bytes move once.
- Package directories with many generated files — `atomic` predicate problem, not a delta problem.

If Git/Cod Sync is weak on the delta-hostile-binary case, the likely future design might still be content-addressed chunking on the Hub — but it should earn its complexity with measurements, and it is **not** a "Unison-borrowed" design.

## Per-peer sync memory: Vault is structurally richer than Unison

Vault already has local peer sync state in `checkouts.db`.
That feels like the right analogue to Unison's archive memory, but at commit/ref granularity instead of per-path replica snapshots.

The useful product question is:

> What does this device believe about Alice's registry head and Alice's head for this niche?

That should become a first-class status concept, driving clearer UI labels:

- no fetched data from Alice;
- fetched Alice at commit X;
- Alice commit X is ready to merge;
- Alice commit X is already merged;
- Alice has signaled newer data since the last successful fetch.

A point worth claiming: Vault's per-peer-per-niche memory is *strictly richer* than Unison's pairwise archive.
Unison's archive collapses to one truth per pair of roots.
Vault's parked refs let it say "I see Alice and Bob disagreeing with each other," which Unison cannot represent.
That is a feature, not just a non-fit with Unison.

## Open questions surfaced by this read

- Where does Vault's atomic-group predicate live — team profile, local profile, or both? `.app` bundles are universal but `.sqlite-wal` pairings might be app-specific.
- How does `status` surface ignored-children-inside-renamed-directories without being noisy on the common case?
- What is Vault's catastrophic-delete threshold default, and is it per-niche or per-team policy?
- Does Vault adopt a Unison-style `ignore` / `ignorenot` override mechanism, or a single ignore set with no escape hatches?
- For external 3-way merge: is the per-extension command list a team profile concern or a local-only convenience?

## Near-term recommendation

Keep Vault on Git/Cod Sync for now.
The next useful work is not adapting Unison.
It is writing down Vault's file semantics, importing the decision-lattice as the parked-review data model, and proving the rough edges with micro tests.

Suggested next branch:

1. Define the parked-update review data model around the per-path decision lattice (equal / false-conflict / one-side-update / coordinated-delete / conflict).
2. Define a structured ignore profile format (Name / Path / Regex), layered team + local.
3. Add default ignore patterns for OS/editor junk.
4. Add micro tests for ignored files, dirty checkout behavior, path collisions, and the rename-with-ignored-children case specifically.
5. Add a catastrophic-delete guardrail on `publish` and on parked-merge apply, with a documented threshold.
6. Define an atomic-group predicate and apply it to at least `.app` bundles and `.docx` files in the default profile.
7. Benchmark git bundle and pack behavior for the four representative buckets above (small text, large append-only, delta-hostile binary, replaced media), and decide whether on-disk content-addressed chunking earns a future branch.
