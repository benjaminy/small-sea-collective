# Branch Plan: NoteToSelf Self-Store Integration

**Branch:** `issue-48-note-to-self-self-integration`
**Base:** `main`
**Primary issue:** #48 "Manager — multi-device NoteToSelf sync and team discovery"
**Depends on:** #185, #187, #191 (all landed)
**Related:** #3, #6, #36, #135, #190, #215, #226, #228

**Planning status:** implemented.
Every decision in this document is settled, and Units 1-5 are all in the working tree.
Keep the splice-merge and Cod Sync units in their own commits so the framework changes are reviewable
without the Manager work around them.

**Implementation status.**
Units 1-5 are complete as written, with one deviation and one addition:

- Unit 1 could not leave `packages/splice-merge/tests/test_merge.py` unchanged.
  One existing case asserted `reconcile_deltas`' stderr directly rather than through the driver,
  so it now asserts the returned conflict set instead.
  Driver output is unchanged. See `notes.md`.
- Unit 5 factored the team-sidebar markup into `fragments/sidebar_teams_body.html`
  so the out-of-band replacement does not become a third copy of it.
- Review closed three failure-contract gaps.
  Splice Merge now refuses duplicate logical row keys instead of collapsing them,
  initial bootstrap sources run the same database admissibility preflight as later sources,
  and a SQLite timeout during post-adoption Git capture reports `recording_pending`.

`packages/small-sea-manager/small_sea_manager/note_to_self_sync.py` holds the Manager's half:
the writer reservation, the capture helper, the logical-dirty check, source admissibility, and adoption.
`fragments/note_to_self_sync.html` and three `POST /note-to-self/*` routes are the surface.
New micro tests: `packages/small-sea-manager/tests/test_note_to_self_integration.py`,
`packages/small-sea-manager/tests/test_note_to_self_sync_ui.py`,
plus the round-trip witness and refresh-durability test in `test_note_to_self_refresh.py`.

## Where #48 already stands

An earlier branch landed the discovery half of #48 (`Archive/branch-plan-issue-48-note-to-self-sync-discovery.md`):
explicit `refresh_note_to_self()`, the device-local adopted self-signal counter,
the Hub self-update watch axis, and `list_known_teams()` / `joined_locally` semantics.

The remaining scope is the second issue comment, which follows from #191's publication settlement.
Device B can now *observe* that its own cloud NoteToSelf chain has diverged from local history,
but nothing in the Manager can *combine* the two.

`packages/small-sea-manager/tests/test_note_to_self_refresh.py::test_divergent_note_to_self_push_reports_integration_required`
already witnesses the divergence and asserts the parked ref survives the process that observed it.
This branch starts exactly where that test stops.

## Problem

1. `push_note_to_self` (`manager.py:319`) lets `PublicationIntegrationRequiredError` propagate.
   Cod Sync parks the competing head under `refs/cod-sync/parked/{link_uid}` (`protocol.py:390`), and there it stays.
   The Manager web app has no NoteToSelf push, refresh, status, or integration surface on which to recover.
   `web.py:507` has similar wording, but it handles `push_team` and therefore belongs to team Core, not NoteToSelf;
   this branch must not repurpose it.
2. `refresh_note_to_self` (`manager.py:354`) merges the fetched head in place with `repo.merge` (`manager.py:373`),
   with no preflight and no conflict handling.
   A conflicted merge leaves `MERGE_HEAD` and a conflicted `core.db` in the work tree —
   the database every subsequent Manager read opens.
3. There is no restart-safe way to ask "is there outstanding self-store state to integrate?".
   The only record is the parked ref, and nothing reads it.
4. The NoteToSelf `core.db` has more than one writing process.
   The Manager owns it, but the Hub also reads and writes it directly:
   `backend.py:1075` inserts `cloud_storage` rows and `backend.py:1266` updates `berth_cloud_allocation.location`.
   The Hub is a separate long-lived local service (`architecture.md:14`, `:235`), not a library call.
   Any adoption step that operates on the *file* rather than on the *database* can therefore lose another
   process's committed write, or write into an unlinked inode.

## Goal

After this branch, one identity's two devices can each create teams, and either device can combine both histories:

1. an explicit Manager operation integrates parked self-store NoteToSelf heads into local `main`;
2. adoption is a row-level merge applied to the live database inside one SQLite transaction,
   so no file-level replacement of `core.db` ever occurs and other processes are serialized by SQLite's own lock;
3. it is restart-safe — a fresh process discovers outstanding source heads from refs alone;
4. the failure contract is *retry*, not *repair*: every refusal rolls back, and every interrupted adoption
   is corrected by running the same operation again;
5. `refresh_note_to_self` runs on the same path instead of its current in-place `repo.merge`,
   so no Manager operation can leave a conflicted `core.db` behind;
6. the adopted self-signal counter advances only for state actually incorporated;
7. the Manager UI exposes NoteToSelf sync status and offers the integration on an identity-level surface.

## The shape of the design

The earlier iteration of this plan merged *files*: evaluate a merged tree off to the side with
`git merge-tree --write-tree`, then find a safe way to get the merged `core.db` into live state.
That framing generated the branch's hardest questions — a lease discipline, a stale-base precondition,
a time-of-check/time-of-use gap, and a durable recovery record — and every one of them descended from it.

This iteration merges *rows*.
The `splice-sqlite` driver already works that way (`splice_merge/cli.py`):

```
ours_delta   = compute_delta(ancestor, ours)
theirs_delta = compute_delta(ancestor, theirs)
apply_delta(ours_path, reconcile_deltas(ours_delta, theirs_delta))
```

It never computes a delta from live to merged.
It applies *theirs'* delta onto ours.
Running that same computation with "ours" bound to the live database, inside one transaction, is the whole design.

`small-sea-manager` already depends on `splice-merge` as a workspace package, so this introduces no new coupling.

## Decisions

D1, D2, D3, D8, D9 and D10 were open in earlier iterations and are now settled.
The reasoning behind the reversals is in `notes.md`.

### D1 — NoteToSelf does not get the `splice-sqlite` merge driver

**Reversed from the previous iteration.**

The driver was only needed because the merge ran through `git merge-tree`.
An in-process merge does not invoke git's merge machinery at all,
so `.gitattributes`, the `merge.splice-sqlite.driver` line in `.git/config`,
the linked-device gap between the two, and the runnable-driver check all become work with no consumer.

Not installing it also has a positive consequence worth stating.
A manual `git merge` in the NoteToSelf repo will fail loudly on a binary file
instead of silently applying ours-wins.
#226 says the generic ours-wins driver is "a research-stage default, not an intended architecture";
declining to make it NoteToSelf's git-level default is consistent with that.

This is a deliberate divergence from team repos, which do install the driver
(`provisioning.py:3043`, called at `:4960` and `:5346`).
The justification is that after this branch NoteToSelf's merge has an owner — one Manager operation —
and team Core's does not yet.
When #226 and #228 settle Core, the two should be reconsidered together.

Nothing else merges the NoteToSelf repo: `manager.py:373` is the only caller and Unit 4 replaces it.

### D2 — Adoption is a row-level delta applied to the live database in one transaction

Capture `local_head = HEAD` and require `base = merge_base(local_head, parked)`.
Two non-empty histories with no common ancestor are an incompatible source, not a merge candidate.
Before extracting any database bytes, require the source commit to satisfy D13's exact NoteToSelf tree invariant.
Extract `base:core.db` and `parked:core.db` to temp files.
Before computing a delta, require the base and source to be readable SQLite databases at the supported
NoteToSelf `user_version`, with the same tables, ordered columns and primary keys as the live database.
This is application-level admissibility, distinct from Cod Sync's validation of the transport history.
No source schema or DDL is ever applied to the live database.

Then, on the live database:

```
PRAGMA foreign_keys = OFF
BEGIN IMMEDIATE
  validate live schema shape
  ours_delta      = compute_delta(base_json, live_json)  # read through this connection
  theirs_delta    = compute_delta(base_json, theirs_json)
  cleaned, clashes = reconcile_deltas(ours_delta, theirs_delta)
  refuse if clashes is non-empty
  apply_delta(live_conn, cleaned)
  require PRAGMA foreign_key_check to be empty
COMMIT                                      # rollback on every refusal or runtime failure
restore the connection's prior foreign_keys setting
```

Then record the result in Git.
For a divergent source, use D12's SQLite-locked capture to stage `core.db` and `write-tree`,
then `commit-tree -p <local_head> -p <parked>`
and move `refs/heads/main` with the existing forward-only compare-and-swap (`repo.py:443`).
D10 defines the distinct fast-forward recording path.

Consequences that are chosen, not discovered:

- **The committed bytes come from the live file.**
  Two devices that each merge the same pair of histories end up with logically equal, byte-different databases.
  This is not introduced here.
  The driver has the same property today, because `%A` is *ours*: device A merging (A, B) and device B merging (B, A)
  start from different files and resolve conflicts in opposite directions,
  so they already fail to converge byte-wise and, on a same-row conflict, logically as well.
- **`apply_delta` and `sqlite_to_json` must accept a connection** rather than opening their own.
  A passed connection is borrowed: neither helper commits, rolls back, closes it, nor leaves connection settings changed.
  `PRAGMA foreign_keys` is a no-op inside a transaction, so foreign keys are disabled before `BEGIN`
  and `foreign_key_check` runs before `COMMIT`; that gives deferred-constraint semantics without
  requiring the delta's rows to arrive in dependency order.
- **The live connection is the Manager's ordinary `attached_note_to_self_connection`**
  (`packages/small-sea-note-to-self/small_sea_note_to_self/db.py:130`), not a bare connection to `core.db`.
  Adoption runs on the same connection every other Manager NoteToSelf read and write uses,
  which is what makes SQLite's lock the serialization boundary against the Hub.
  Two consequences follow from that helper and should not be rediscovered during implementation.
  It ATTACHes the device-local database, so `BEGIN IMMEDIATE` takes a write lock on that file as well;
  the transaction is therefore held only for the adoption, never across Hub I/O.
  It also sets `PRAGMA foreign_keys = ON`, which is the setting the pseudocode disables and restores.
  Table discovery must stay scoped to `main`: `sqlite_to_json` reads an unqualified `sqlite_master`,
  which resolves to `main`, and then reads rows by unqualified table name, which also resolves `main` first.
  That is correct here only because the shared and device-local schemas do not overlap,
  so the source-versus-live shape check below is what keeps it correct.

### D3 — The failure contract is retry, not repair

Every question the previous iteration raised under D3 is answered by the transaction rather than negotiated.

- **Serialization** is SQLite's write lock, which holds across processes and therefore covers the Hub.
  The adoption transaction protects row application, and D12 uses a second writer reservation to protect
  Git's raw read of the committed database.
  Manager SQLite uses the default rollback journal — no `journal_mode` is set anywhere in the repo —
  so there are no `-wal`/`-shm` sidecars to keep consistent.
- **Stale base does not exist.**
  There is no precondition that the live `core.db` equals `HEAD`.
  Uncommitted local work is simply part of "ours", exactly as `push_note_to_self` already treats it
  when it stages and commits whatever is live.
- **Dirty `core.db` is not a refusal.**
  This diverges from the issue text's "clean-work-tree preflight", which was written for a `git merge` design.
  There is nothing left to preflight.
  An unresolved `MERGE_HEAD` is still an unconditional refusal: an earlier in-place merge is unsettled
  and a person must resolve it first.
- **Refusal is rollback.**
  A constraint violation, a `foreign_key_check` failure, or a D9 semantic refusal aborts the transaction.
  `HEAD`, `core.db`, the index, the parked refs and the adopted counter are all unchanged
  because nothing was committed, not because a cleanup path ran.
- **An interrupted adoption is corrected by re-running the operation.**
  The computation is `(base, theirs, live-now)` and nothing is precomputed, so a second attempt against the
  same base produces an empty delta: `reconcile_deltas` drops identical insert/insert and update/update as ordinary,
  and both-deleted as redundant.
  Probed directly; see `notes.md`.
  Therefore no durable recovery record, no watermark, and no new sidecar file.

The incomplete-recording window begins after the SQLite `COMMIT` and ends at the ref update.
D12 closes the concurrency hazard inside that window; a process death can still leave a correctly merged database
whose result is not yet in Git history.
Re-running integrates nothing new and records the commit.
That is the entire repair story, and it is exercised in validation rather than asserted.

A fetched refresh head is made durable under its immutable `refs/cod-sync/parked/{link_uid}` ref
before this transaction begins.
Therefore the same repair story applies to explicit refresh as to a head parked by publication.

### D4 — Where the parked-ref discovery helper lives

`ssc-files` already has this logic (`packages/ssc-files/ssc_files/files.py:640-691`):
list `refs/cod-sync/parked/*`, drop heads already contained in `HEAD`, drop non-maximal heads, integrate the rest.
This branch needs the same thing over the same Cod Sync ref namespace.

**Decision:** put the discovery helper in `cod-sync` next to `PARKED_REF_PREFIX` (`protocol.py:55`)
and consume it from the Manager.
Do not rewrite `ssc-files` to use it in this branch; that is a follow-up.

### D5 — Integration does not advance the adopted self-signal counter

Integration is purely local and observes no Hub notification, so it must not move the counter.
Only `refresh_note_to_self` advances it, and only after adoption succeeds.
The visible cost is that a refresh after an integration may re-fetch a head already present locally; that is idempotent.

### D6 — Parked refs are left in place after a successful merge

Discovery filters by ancestry, so an integrated head stops being outstanding on its own.
Deleting refs would add a failure mode without adding information.
This matches `ssc-files`.

### D7 — `Repo.merge` stays

Only `manager.py:373` moves off it.
`ssc-files` keeps using in-place merge deliberately: for arbitrary user files,
leaving conflicts in the checkout for a person to resolve is the right answer and rolling back is the wrong one.
Two consumers with correct and opposite adoption rules are the argument against a framework-level policy.

### D8 — This branch adds no authorship verifier

Cod Sync proves structure, bundle contents and ancestry.
It does not connect link signatures to admitted device keys (#190),
so a parked ref does not prove that another device of this identity authored the chain.

**Decision: proceed, and add no verifier seam here.**
The parked ref is created by `publish()`'s observation path, which imports through fetch (`protocol.py:390`).
If #190 lands, verification belongs there.
A verifier seam inside `integrate_note_to_self()` would be in the wrong layer and would have to be removed again.

Blocking #48 would also not close the gap it names:
`refresh_note_to_self` already gives store-authoritative bytes local effect today,
and leaving that unguarded path in place while blocking the guarded one is strictly worse.
This branch does not widen the trust posture; it narrows the blast radius of the posture that already exists.

Consequences to implement:
remove every own-device authorship claim from code and UI,
say in the result text that the source is a structurally validated stored history from the participant's
configured NoteToSelf storage whose repository tree and database shape are compatible with this Manager,
and do not cite this branch as precedent for teammate Core admissibility (#228).

### D9 — Every non-identical same-row conflict is a semantic refusal

`reconcile_deltas` currently resolves insert/insert, delete/modify, modify/delete and update/update
in favor of ours and prints a warning to stderr.
Through the git driver that information is unrecoverable — the caller sees an exit code.
In process it is nearly free: have `reconcile_deltas` return its conflicts alongside the cleaned delta
without printing them.
`cli.py` renders the returned conflicts with the current warning text and otherwise keeps driver behavior unchanged;
the Manager turns the same data into a typed result without writing policy outcomes to stderr.

NoteToSelf holds mutable identity, app-registration, cloud-location and device state.
`nickname.name`, `team.name`, `user_device.label` and `cloud_storage` rows are edited, not appended.
Reporting success while discarding the other device's intentional edit violates the project's rule that
durable ambiguity should remain visible, and "both devices are mine" reduces the authorship question,
not the conflict-of-intent question.

**Decision: refuse every non-identical same-key conflict.**
The policy is deliberately not scoped to a list of mutable tables.
A non-identical collision in an append-like table is more suspicious, not safer, and silently choosing ours
would still discard durable source state.

Byte-identical insert/insert and update/update, plus delete/delete, are redundant and do not enter the conflict set.
Everything else produces a typed semantic-refusal result that names the table, key and conflict kind for the UI.
The parked source remains outstanding until a later conflict-resolution product exists (#36).

### D10 — Fast-forward keeps its history without replacing the live database

If `local_head` is an ancestor of the source, still run the D2 SQLite transaction.
This is true even when Git reports a clean work tree: that check cannot prove that the Hub has no open SQLite connection,
so checking out or replacing the source blob would reintroduce the unlinked-inode hazard D2 removes.

After the transaction succeeds, load the source tree into the index and then advance `refs/heads/main` directly
to the source with the existing compare-and-swap.
Writing the index before the ref is intentional: a process death in between leaves the source staged against the old head,
and re-running the same operation completes the ref movement.
If the compare-and-swap reports that another writer already advanced `main` beyond the source,
realign the index to that returned current head before reporting the stale success.
At no point is the source blob materialized over the live file.

The live database may then be byte-different from `HEAD` while representing the same rows.
That is an accepted consequence of making SQLite, rather than an inode swap, the adoption boundary.
Any pre-existing local rows remain ordinary uncommitted changes on top of the new head.

Therefore NoteToSelf cleanliness is logical, not byte-level:

- add a Manager helper that validates the two schema shapes and treats
  `compute_delta(head_json, live_json) == {}` as logically clean, independent of SQLite row or page order;
- change `push_note_to_self` to run that comparison and any path-scoped commit under D12's one writer reservation; and
- leave a logically equal, byte-different database uncommitted, so a refresh followed by push creates neither a no-op commit
  nor a new self-signal.

Release the reservation before `CodSync.publish()` or any other Hub I/O.

For a divergent source, D2 still records a two-parent commit from the live bytes.
The operation never promises that the work tree remains byte-clean after it releases SQLite's write lock;
a later Hub write is preserved as the next ordinary local change.

### D11 — No side-merge primitive in this branch

`git merge-tree --write-tree` works and honors custom `merge=` drivers; that probe was correct and re-verified.
It is nonetheless not needed here.
The NoteToSelf Sync repo holds exactly one file (`provisioning.py:2206`),
team Sync repos hold `core.db` plus `.gitattributes` (`provisioning.py:4961`),
and #226 intends to replace the whole-file rule that a side merge would be evaluating.
Building it now is infrastructure for a consumer whose contract is not written.

A candidate worktree is likewise out.
It buys neither of the two things it looks like it buys:
not safety, because #226 already notes that a disposable candidate worktree does not make updating the live
checkout safe, and not legibility, because `git diff` on `core.db` says "Binary files differ".
A user-legible summary has to be computed from rows by the application that owns the schema, whatever else happens.
A candidate tree is additionally a *snapshot* of an evaluation, which goes stale as soon as local state moves;
`(base, theirs, live-now)` cannot, which is what makes leaving a parked head alone indefinitely actually safe.

### D12 — Git captures the live database under a SQLite writer reservation

Git reads `core.db` as an ordinary file.
It does not honor SQLite's rollback journal or file locks, so committing the adoption transaction and then running
`git add core.db` leaves a race: a Hub writer can spill changed pages into the database while Git is reading it.
The live database remains recoverable through its journal, but the staged blob can be an incoherent snapshot.

**Decision: every Git operation that reads the live NoteToSelf `core.db` runs while the Manager holds
`BEGIN IMMEDIATE` on `attached_note_to_self_connection`.**
`BEGIN IMMEDIATE` waits for an existing writer and prevents a new SQLite writer from starting;
unlike a file read by Git, it participates in the same cross-process locking protocol as the Hub.

For divergent adoption:

1. commit the D2 row-application transaction and restore the connection's foreign-key setting;
2. begin a fresh `IMMEDIATE` transaction;
3. stage `core.db` and call `write_tree()` while that reservation is held;
4. end the SQLite transaction, then create the Git commit and advance the ref from the captured tree.

A Hub write that wins the gap before step 2 completes in full and becomes part of the captured local state.
A Hub write that starts after step 4 remains an ordinary logical change on top of the recorded commit.
No writer can overlap the raw file read itself.

For `push_note_to_self`, acquire the same reservation before reading live rows for the logical-dirty comparison,
hold it across `commit_paths(["core.db"], ...)` when a commit is needed, and release it before opening the network publication.
This makes the comparison and the committed bytes one stable SQLite state.
It also keeps the existing rule that no SQLite transaction is held across Hub I/O.

Use the same Manager-owned capture helper for the existing direct NoteToSelf commits in initial participant creation,
device admission and app registration (`provisioning.py:2208`, `:2311`, `:5040`).
Initial creation has no competing Hub process today, but one rule for every NoteToSelf Git capture is simpler than
preserving an unsafe idiom for selected callers.
The helper protects only the raw Git read; it does not absorb the application mutation or its commit message.

A Git failure after row adoption is a recording-pending result, because the database transaction has already committed.
A Git failure during push moves no database state and leaves the current local state for a later push.
Neither path adds a lease file or durable recovery record.

### D13 — A NoteToSelf source tree contains exactly one regular `core.db`

Cod Sync validates links, bundles and ancestry, not application tree contents.
D8 therefore makes it unsafe to infer the NoteToSelf repository shape merely because a source arrived through Cod Sync.
Fast-forward adoption is especially load-bearing because `read_tree(source)` makes the entire source tree the new index and,
after the ref update, the new `main` tree.

**Decision: before any already-contained, fast-forward, divergent or initial-clone source is accepted,
the Manager requires its root tree to contain exactly one entry: a `100644` blob named `core.db`.**
An extra path, a missing path, or a different Git object type or file mode is the incompatible-source outcome.
This is Manager application policy over a generic `Repo.tree_entries(rev)` plumbing method in Cod Sync.
It is checked before any live row change, index change, checkout or ref movement.

## Work Units

Units 1 and 2 are independent and stay in separate commits.

### Unit 1 — splice-merge accepts a connection and reports conflicts

Additive changes to `packages/splice-merge/splice_merge/core.py`:

- `sqlite_to_json` and `apply_delta` (`core.py:7`, `:193`) accept an open connection as well as a path.
- `reconcile_deltas` (`core.py:125`) returns the conflicts it currently prints, alongside the cleaned delta,
  and performs no stderr I/O.
- `cli.py` prints the returned conflicts with the existing warning text and continues to apply the ours-wins delta.
- Identical update/update stops being a conflict, matching identical insert/insert and delete/delete (D9).
  This is a real behaviour change for the driver — it warns on that case today —
  and D3's retry story depends on it, because a retry sees the source's own committed update on both sides.

Tests: the change to identical update/update alters no existing assertion, since no case in
`packages/splice-merge/tests/test_merge.py` covers it, and driver output is unchanged for every case that file does cover;
the returned conflict set names table, key and conflict kind for each of the four cases;
a byte-identical insert/insert and update/update appear in neither the cleaned delta nor the conflict set;
and a borrowed connection is neither committed nor closed and retains its caller-owned settings.

### Unit 2 — Cod Sync plumbing (D4, D2, D13)

- Parked-head discovery: the maximal outstanding heads not already contained in local history.
  Tests include the ancestor/descendant suppression case `ssc-files` covers today.
- `blob_at(rev, path, dest)` — write one file out of a commit without a checkout, using a binary-safe subprocess path
  rather than Cod Sync's current `text=True` command wrapper.
- `tree_entries(rev)` — use a binary-safe, NUL-delimited `git ls-tree` subprocess path to return mode,
  object type, object id and raw path bytes without assigning application meaning to the entries.
- `read_tree(rev)`, `write_tree()` and `commit_tree(tree, parents, message)` — align the index or create history
  without touching the work tree or moving a ref.
  `advance_ref` (`repo.py:443`) and `merge_base` (`repo.py:353`) already exist and are reused as-is.

Tests: `commit_tree` produces the declared parents and moves no ref;
`blob_at` preserves an exact SQLite blob, including non-text bytes, and leaves the index and work tree untouched;
`tree_entries` preserves newline-containing and non-UTF-8 path bytes without line-oriented parsing;
`read_tree` changes only the index;
discovery suppresses an ancestor of another outstanding head.

### Unit 3 — Manager integration operation (D2, D3, D9, D12, D13)

- `TeamManager.note_to_self_conflict_status()` — outstanding parked heads, read from refs, no Hub contact.
- `TeamManager.integrate_note_to_self()` — refuse on `MERGE_HEAD`; otherwise, for each maximal parked head,
  run the D2 transaction and record the D10 commit.
- A Manager-owned schema preflight rejects an unreadable database, unsupported `user_version`, schema-shape mismatch,
  a source tree other than D13's one regular `core.db`, or histories with no common ancestor before any live row changes.
  The preflight reads its shapes with `PRAGMA table_info` over `main`, not from `sqlite_to_json` output.
  That output carries column names only inside row dicts, so an empty table exposes none of them
  and a source that dropped a column from an empty table would otherwise pass.
  It compares table set, ordered column names and primary keys; it does not compare indexes or DDL text.
- The D2 transaction runs on `attached_note_to_self_connection`, and both live reads inside it —
  the schema check and `compute_delta`'s `live_json` — go through that same connection,
  so nothing observes the database outside the transaction's snapshot.
- A logical-dirty helper validates schema compatibility and computes the row delta from `HEAD:core.db`
  to live NoteToSelf state.
  `push_note_to_self` uses it and `commit_paths(["core.db"], ...)` under one D12 reservation,
  so D10 cannot manufacture byte-only commits and Git cannot capture a concurrent SQLite write.
- Divergent integration captures the staged blob and tree under D12's post-adoption reservation.
  Commit construction, ref movement and all Hub I/O happen after releasing it.
- Replace the three direct NoteToSelf `stage`/`commit` sites in provisioning with the same capture helper.
  Their existing mutation boundaries and commit messages stay unchanged.
- The result type covers integrated, nothing outstanding, incompatible source, semantic conflict (D9),
  constraint or `foreign_key_check` refusal, post-commit Git recording pending, and per-head partial success.
  Recording-pending is a retry state, not a refusal that claims the SQLite transaction rolled back.
- Durable bookkeeping is refs and the existing adopted-count row.
  Nothing else.

### Unit 4 — `refresh_note_to_self` on the same path

Replace `repo.merge(result.observed_head)` with the same evaluation and adoption machinery,
preserving separate cases for already-contained, fast-forward (D10) and genuinely divergent histories.
Immediately after a successful fetch, create the immutable parked ref named by `FetchResult.link_uid`
before changing the live database.
If the process dies before that ref is created, no live adoption has begun and a later refresh can fetch again;
after it exists, restart discovery is entirely local.
Apply D13 before the initial clone, `read_tree`, or any live transaction.
Keep the existing pre-fetch counter snapshot rule, which guards against adopting a push this device never fetched,
and advance the counter only after adoption succeeds.
After this unit no Manager operation can leave a conflicted `core.db` in the work tree.

Retain the initial-clone case only for an unborn repository with no live `core.db` to replace.
A non-empty live database and a source with no common ancestor is the incompatible-source outcome from Unit 3.

### Unit 5 — Manager surfaces

Add `fragments/note_to_self_sync.html` as a **NoteToSelf sync** card on the index page,
above the app-bootstrap prompts.
The card displays restart-safe outstanding-source status and has explicit actions backed by
`POST /note-to-self/push`, `POST /note-to-self/refresh` and `POST /note-to-self/integrate`.
Each route returns the complete `#note-to-self-sync` fragment with its updated status, notice or error.

Push and refresh require an active NoteToSelf passthrough Hub session.
When no session is active, the card keeps those actions unavailable and says to connect through the existing
Hub-connection control; the routes enforce the same condition rather than relying only on hidden buttons.
Integration is local and remains available without a Hub session.
After refresh or integration changes the known-team set, the response includes an out-of-band replacement for
`#sidebar-teams`, following the existing acceptance-token pattern.

Leave `web.py:507` on the team-Core path; #228 owns the operation that can replace that message honestly.
Read availability from `note_to_self_conflict_status()` on an ordinary page load so the offer survives a Manager restart.
Report D9's table, key and conflict kind plainly, while using D8's transport-valid, tree-compatible and
schema-compatible wording rather than claiming a sibling device authored the source.
No row-level preview: that is #36, and per D11 it needs a NoteToSelf-specific renderer rather than a generic diff.
No CLI work — `cli.py` has no NoteToSelf push or refresh commands today.

## Validation

The skeptic asks four things:
*did two devices' NoteToSelf histories actually combine*,
*is a concurrent writer in another process ever silently lost*,
*does a fetched source remain both admissible and recoverable before it changes live state*, and
*does the branch make no stronger authorship claim than D8 can prove?*
Micro tests answer all four against real installations.
The two-device tests use MinIO and the Hub `TestClient`, following the existing pattern in
`packages/small-sea-manager/tests/test_note_to_self_refresh.py`.

**The end-to-end witness.**
Extend the existing divergence scenario: device A publishes `SharedProject` then `OnlyOnA`,
device B bootstraps, commits `OnlyOnB`, and its push is refused with the head parked.
Then:

- a *freshly constructed* `TeamManager` reports the parked head as outstanding — restart-safe discovery from refs alone;
- `integrate_note_to_self()` combines them;
- `list_known_teams()` on device B contains `SharedProject`, `OnlyOnA`, *and* `OnlyOnB`;
- the follow-up `push_note_to_self()` succeeds and the resulting cloud head has both pre-merge heads as ancestors;
- device A refreshes and sees all three teams.

That last step is what makes the claim real: the merge round-trips through the cloud to the other device
rather than merely looking plausible locally.

**Adoption under concurrency (D2, D3).**
- A second connection — opened separately, as the Hub's would be — inserts a row after `base` is captured
  and before the transaction begins.
  Assert the row survives integration.
  This is the test that would have caught a live-to-merged delta, which deletes anything absent from merged.
- A second connection attempting to write *during* the transaction is serialized by SQLite, not lost.
- A reader spanning the transaction observes either the old logical state or the complete adopted state,
  never a partially applied row set.
- Put a barrier inside divergent staging while D12's second transaction is held.
  A Hub-like connection attempting to write must wait until `write_tree()` has captured a blob that passes
  `PRAGMA integrity_check` and contains the complete pre-barrier logical state; after release, the writer completes
  and its row remains as the next logical-dirty change.
- Put the same barrier inside `push_note_to_self`'s path-scoped commit.
  The captured commit is a valid SQLite snapshot, the competing write completes after the reservation is released,
  and publication performs no Hub call while the reservation is held.
- Existing participant creation, device-admission and app-registration micro tests keep witnessing their original commits
  after those call sites move onto the shared capture helper.
- A connection opened before a fast-forward can write afterward and its row remains in the live database;
  the live `core.db` inode is unchanged throughout adoption.
- A genuine divergence has both pre-merge heads as parents.
- A fast-forward leaves `main` at the fetched source and the index at its tree without materializing its blob.
  A byte-different but logically equal live database is reported clean by the Manager helper,
  and a following push creates no commit, upload or self-signal.

**Failure contract (D3).**
- Idempotence: run `integrate_note_to_self()` twice.
  The second reports nothing outstanding (D6's ancestry filter).
- Interrupted adoption: commit the SQLite transaction, then fail before the ref update.
  From a *freshly constructed* `TeamManager`, re-running integrates nothing new, records the commit,
  and leaves `list_known_teams()` correct.
  The source delta includes an update, not only inserts, so the test proves identical update/update is retry-neutral.
  Assert no new file, table or watermark appeared under the participant directory,
  the way #185's tests assert no Core sync sidecar exists.
- Fast-forward interruption: fail after loading the source tree into the index but before moving `main`.
  A fresh Manager completes the same fast-forward and leaves the index aligned with the resulting head.
- Refresh durability: after `fetch()` returns, assert its immutable parked ref exists before injecting a failure
  at the start of live adoption; a fresh Manager reports that source without Hub contact.
- Constraint refusal: two devices allocating the same berth violates
  `idx_berth_cloud_allocation_berth` (`shared_schema.sql:51`).
  `reconcile_deltas` passes it through as an ordinary insert because the primary keys differ,
  and `apply_delta` raises `IntegrityError`.
  Assert a typed refusal, and that `HEAD`, `core.db`, the index, the parked ref and the adopted counter are unchanged.
- Unresolved `MERGE_HEAD` present: refuse.
- Multiple incomparable parked heads: exercise per-head partial success.
- Two structurally valid Git histories with no common ancestor produce an incompatible-source result and move nothing.

**Semantic conflict (D9).**
- A real same-row NoteToSelf conflict — the same team renamed on both devices — produces a typed refusal
  rather than a stderr warning.
- A non-identical insert collision in an append-like table refuses under the same rule.
- Identical insert, update and delete outcomes are redundant and do not refuse.

**Source admissibility.**
- A source with a missing table, extra table, changed primary key, unsupported `user_version`, corrupt `core.db`,
  or no `core.db` produces an incompatible-source result before the live transaction changes a row.
- A source tree with an extra path, a non-blob `core.db`, or a `core.db` mode other than `100644`
  produces the same result before the index, work tree or `main` moves.
- A schema-identical source that violates a live unique index reaches the distinct constraint-refusal result.
- Every refusal leaves `HEAD`, the live database, the index, the parked ref and the adopted counter unchanged.

**Source trust (D8).**
- The result and UI say the source is a transport-valid, tree-compatible and schema-compatible stored history,
  not a sibling device's authored chain.
- No test asserts that Cod Sync proved which device authored the history.

**Manager UI.**
- Add the route and fragment micro tests in `packages/small-sea-manager/tests/test_note_to_self_sync_ui.py`.
- An ordinary index-page request renders the NoteToSelf card from a freshly constructed Manager and offers
  offline integration when a parked source is outstanding.
- Without an active passthrough session, push and refresh are unavailable and their POST routes return the same
  connect-to-Hub explanation without attempting transport; integration still works.
- With an active session, the push and refresh POST routes call the corresponding Manager operations and return
  the updated card fragment.
- Successful refresh and integration return an out-of-band sidebar replacement containing newly known teams.
- Semantic refusal renders D9's table, key and conflict kind and makes no device-authorship claim.

**Regressions that must keep passing.**
- All of `packages/small-sea-manager/tests/test_note_to_self_refresh.py`,
  including the device-local-table leakage test and the no-op-publication signal test.
- `packages/splice-merge/tests/test_merge.py` unchanged — Unit 1 is additive.
- `test_identity_bootstrap.py`, `test_merge_conflict.py`, `packages/ssc-files/tests/test_sync.py`
  (unchanged behaviour after the D4 move), `tests/test_watch_notifications.py`.
- Finally `uv run pytest -q`.

**Repo integrity.**
- `splice-merge` gains a return value and a connection parameter; it gains no policy.
- Cod Sync gains discovery over its own ref namespace and five plumbing operations; it gains no application policy.
- The Manager gains one operation, one status read, one logical-dirty check, one source-tree rule,
  one stable-capture helper and one adoption rule for a database it already owns.
- Nothing new couples the Manager to `ssc-files`.
- All cloud access stays on the existing Hub-mediated NoteToSelf session; no new transport.
- Every durable record is a Git ref, except the adopted-count row that already existed.

## Out of Scope

- Automatic or background integration, and any merge policy that runs without a person asking (#36).
- Row-level preview of a parked head in user-legible terms (#36).
  D11 records why it needs a NoteToSelf-specific renderer.
- A Cod Sync side-merge primitive and any candidate worktree or checkout (D11).
- A canonical byte representation for logically equal SQLite databases.
  D10 makes Manager publication depend on logical equality instead.
- Automatic join or local clone of a team discovered through NoteToSelf; discovery stays distinct from join.
- Team Core integration and its admissibility contract (#226, #228).
- Reconsidering the merge driver for team repos alongside D1.
- Rebase-versus-merge semantics for unpublished local commits (#135).
- Cod Sync link-authorship verification policy (#190); D8 explains why no seam belongs here.
- Automatic publication retry.
- Migrating existing playgrounds.
  This is a research repo with no installed base: recreate the playground.

## Follow-ups

For `follow-up.md` as the work confirms them:

- #226 and #228: this branch answers the adoption-boundary question for one store —
  the boundary is a SQLite transaction, not a checkout — and demonstrates a typed semantic-refusal outcome.
  It deliberately does not settle Core admissibility.
- Move `ssc-files` onto the shared parked-ref discovery helper from D4.
- Reconsider the `splice-sqlite` driver for team repos once #226 lands (D1).
- Consider a canonical logical representation for Git if a future design requires byte-clean SQLite work trees
  after safe cross-process adoption.
