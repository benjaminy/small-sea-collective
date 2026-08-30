# Notes

## Side-merge feasibility probe (2026-08-30)

Question from the user: can an app merge "off to the side" so the merge does not disturb
other work on the live checkout, then swap the result in atomically?

Probed against `git version 2.50.1` with throwaway repos.
Findings:

**`git merge-tree --write-tree` is the primitive, and it is better than a side worktree.**

- It honors custom `merge=` drivers from `.gitattributes`, so `splice-sqlite` runs exactly as it does in a work-tree merge.
  Verified with a driver that writes a marker: the marker appears in the resulting tree.
- It needs no work tree, no index, and no branch checkout.
  `git --git-dir=... merge-tree --write-tree A B` works with the cwd outside the repository entirely.
- On success it writes a tree object and prints its SHA.
- On driver failure it exits 1 and names the conflicted paths.
- In both cases `HEAD`, the index, and the work tree are untouched, and no `MERGE_HEAD` is left behind.
  A conflicted side merge needs no rollback, because nothing moved.

So no `git worktree add` machinery, no disposable clone, and no candidate checkout directory are required
for the *evaluation* half of the problem.
This is a small addition to `cod_sync.repo.Repo`, not a subsystem.

**The unsolved half is adoption, not evaluation.**

Turning a merged tree into local effect is two steps with different risks:

- The commit is easy: `git commit-tree <tree> -p HEAD -p <parked>` and a ref update.
  Git ref updates are atomic.
- Materializing the merged `core.db` into the live work tree is the actual problem.
  `git cat-file blob <tree>:core.db > tmp && os.replace(tmp, live)` is atomic at the directory entry,
  but any SQLite connection already open across the swap keeps the old inode and can write into an unlinked file.

Relevant current facts:

- Manager SQLite uses the default rollback journal, not WAL (`provisioning.py:41`), so there are no `-wal`/`-shm` sidecars to keep consistent with the main file.
- Manager connections are short-lived context managers (`attached_note_to_self_connection`), but they ATTACH the device-local DB, so one connection spans two files.
- Concurrent access inside the Manager is real, not hypothetical: the admission-events poller runs every 0.2s against the same team DB while user mutations run (#215).

Two candidate adoption designs:

1. **File swap under a lease.** Replace the live file, with a serialization discipline that guarantees no connection spans the replace.
   Requires every DB accessor to participate.
2. **Delta application in one SQLite transaction.** Compute the merged DB off to the side, diff it against the live DB,
   and apply the difference inside a single transaction.
   `splice-merge` already has `compute_delta` / `reconcile_deltas` / `apply_delta` (`packages/splice-merge/splice_merge/core.py:193`), and `apply_delta` already ends in one `conn.commit()`.
   SQLite's own transaction gives real atomicity against concurrent connections, which is stronger than a file swap.
   Cost: the adopted bytes come from the live file rather than from git's merged blob (SQLite page layout differs),
   so the commit has to be built from the live file, not from `merge-tree`'s tree.

Option 2 looks better, and it is closer to what `splice-merge` is already built to do, but it deserves an explicit decision.

**Related tracker state.**
#226 already names "the boundary between isolated candidate evaluation and the final checked-out-`main` adoption step"
as an open question for team Core, and explicitly warns that a disposable candidate worktree does not by itself make
updating the live checkout safe.
#228 scopes the team-Core integration implementation and puts NoteToSelf (#48) out of its scope.
Neither issue covers the *mechanism* as reusable infrastructure; there is no issue for that yet.

## Undefined or broken merge driver is a loud failure, not corruption

Probed separately, because the plan's D1 depends on it.
When `.gitattributes` names a `merge=` driver that is not configured on this device,
git falls back to its built-in merge, detects that `core.db` is binary, and reports a conflict —
it does not write a corrupt database.
A configured-but-unrunnable driver exits nonzero and also becomes a conflict.

So the runnable-driver check in D1 buys a clear diagnosis, not data safety.
That is still worth having, since a linked device gets the tracked `.gitattributes` and no `.git/config` driver line,
and "merge conflict" is a bad way to learn that.

## Reversal: merge rows into the live DB, not files into a tree (2026-08-30, later session)

The probes above are all still true.
The design they were serving is not.

The previous plan merged *files*: build a merged tree with `git merge-tree --write-tree`,
then find a safe way to land the merged `core.db` in live state.
Every hard question in that plan's D2 and D3 — a lease discipline, a stale-base precondition,
a time-of-check/time-of-use gap, a durable recovery record — descended from that framing.

The observation that dissolved it is that `splice-sqlite` does not work that way either.
Reading `packages/splice-merge/splice_merge/cli.py`:

```
ours_delta   = compute_delta(ancestor, ours)
theirs_delta = compute_delta(ancestor, theirs)
apply_delta(ours_path, reconcile_deltas(ours_delta, theirs_delta))
```

It applies *theirs'* delta onto ours.
It never computes a delta from live to merged.
The previous plan's Option B invented a different and strictly worse operation:
a live-to-merged delta deletes anything present in live and absent from merged,
which is exactly the data-loss hazard its D3 then spent a page defending against.

Binding "ours" to the live database and running the driver's own algorithm inside one transaction
removes the hazard instead of guarding it.

### Why this matters more than it first appears: the Hub is a second writing process

The previous notes named the admission-events poller as the concurrency witness.
That was the wrong one — that poller runs against *team* DBs, not NoteToSelf.

The real concurrent writer is the Hub, which is a separate long-lived local service and writes
the participant's NoteToSelf `core.db` directly:
`backend.py:1075` inserts `cloud_storage` rows,
`backend.py:1266` (`_writeback_locator`) updates `berth_cloud_allocation.location`.

That rules out the file-swap option outright rather than on style grounds,
and it means the serialization D3 was trying to design already exists: SQLite's write lock works across processes.
No `journal_mode` is set anywhere in the repo, so this is the default rollback journal and there are no
`-wal`/`-shm` sidecars to keep consistent with a replaced main file.

### Probe: re-applying theirs' delta is idempotent

This is what replaces the durable recovery record.
Base has one team; ours adds `OnlyOnB` and renames the shared team; theirs adds `OnlyOnA` and renames it differently.
Applying theirs' delta to ours twice, recomputing `ours_delta` from the live DB each time:

```
attempt 1: delta empty? False  ops={'team': {'inserts': 1, 'deletes': 0, 'updates': 0}}
attempt 2: delta empty? True   ops={}
final teams: [('00','RenamedByB'), ('AA','OnlyOnB'), ('BB','OnlyOnA')]
```

`reconcile_deltas` already drops identical insert/insert as ordinary and both-deleted as redundant,
so the second pass produces nothing.
Because the computation is `(base, theirs, live-now)` with nothing precomputed,
an adoption interrupted anywhere is corrected by running the same operation again.
The failure contract is therefore retry, not repair, and it needs no new durable state.

Wart: an update/update whose values are identical still prints "true conflict, keeping ours".
Harmless, but it makes the retry path noisy.
Recorded as a follow-up.

### Probe: `apply_delta` raises on a UNIQUE index, and that is reachable

`berth_cloud_allocation` has a uuid7 primary key and a `UNIQUE` index on `berth_id` (`shared_schema.sql:51`).
Two devices allocating the same berth produce rows with different primary keys and the same `berth_id`,
so `reconcile_deltas` sees no conflict and passes it through as an ordinary insert:

```
cleaned delta: {'berth_cloud_allocation': {'inserts': [...], 'deletes': [], 'updates': []}}
apply_delta RAISED: IntegrityError UNIQUE constraint failed: berth_cloud_allocation.berth_id
rows after: [('AA','BEEF','loc-A')]
```

Through the git driver this becomes an undiagnosable binary conflict.
Inside a transaction on the live DB it is a clean typed refusal that rolls back — the "nothing moved"
guarantee falls out of the transaction rather than out of a cleanup path.

This is also the concrete case that makes D9 worth deciding rather than deferring.

### `git merge-tree` re-verified, and dropped anyway

Re-confirmed on git 2.50.1 that `--write-tree` honors a custom `merge=` driver from `.gitattributes`:
a marker-writing driver's output appears in the resulting tree.
The earlier probe was correct.

It is dropped because nothing in this branch needs it.
The NoteToSelf Sync repo holds exactly one file (`provisioning.py:2206`);
team Sync repos hold `core.db` plus `.gitattributes`;
and #226 intends to replace the whole-file merge rule that a side merge would be evaluating.
It should get its own issue and land when a consumer's contract is written.

### Why not a candidate worktree either

Discussed with the user and worth recording, because the instinct is a reasonable one.
"Sandbox" bundles two jobs that come apart here: evaluation and safety.

It buys neither.
Not safety — #226 already notes that a disposable candidate worktree does not by itself make updating the
live checkout safe; it relocates the moment where the problem has to be solved.
Not legibility — `git diff` on `core.db` says "Binary files differ",
so a user-legible summary has to be computed from rows regardless.
Any such diff is necessarily application-specific: it needs the schema and the meaning of each table,
which is the Manager's knowledge, not cod-sync's or splice-merge's.

The stronger objection is staleness.
A candidate tree is a *snapshot* of an evaluation, and it goes stale the moment local state moves,
which invites showing a user one thing and applying another.
`(base, theirs, live-now)` recomputes from current state every time,
so a parked head can sit untouched for weeks and every preview of it stays correct.
That is what actually makes "leave it parked as long as you like" true rather than aspirational.

## Review correction: fast-forward is also an adoption (2026-08-30)

The row-merge iteration still had one file-level exception.
D10 proposed checking that `core.db` matched `HEAD`, advancing to a descendant source and materializing
the source blob into the live path.
That contradicted the reason for choosing SQLite as the adoption boundary.
A Git cleanliness check does not prove that the Hub has no connection open on the existing inode,
and it cannot close that race before a checkout or replacement.

SQLite's backup API was probed as a possible way to keep both connection safety and exact source bytes.
It did preserve an observer connection opened before the backup: the observer saw the new rows and could
write afterward into the live database.
It did not leave the destination byte-identical to the source.
So it does not make a safe live adoption into a byte-exact Git fast-forward.

The revised decision separates history from live representation:

- apply a fast-forward source through the same live SQLite transaction as a divergent source;
- advance `main` to the source commit without writing its blob over `core.db`;
- align the index to the source tree; and
- define Manager cleanliness by schema and rows, so `push_note_to_self` does not commit a byte-only difference.

This preserves source ancestry and avoids publication ping-pong without reintroducing an inode swap.
The cost is an intentionally byte-dirty Git work tree after some logically clean refreshes.
A canonical Git representation could remove that cost later, but it is not needed to establish safe adoption.

## Review correction: three retry and admissibility gaps (2026-08-30)

**Identical updates are not conflicts.**
The earlier probe called `reconcile_deltas`' identical update/update warning harmless.
That stopped being true once D9 selected semantic refusal: after SQLite commits a source update and the process
dies before moving the Git ref, the retry sees that same update on both sides.
Unit 1 now makes identical update/update redundant, just like identical insert/insert and delete/delete.

**Refresh must park before adoption.**
`CodSync.fetch()` imports objects but creates no ref unless its caller supplies one.
The Manager therefore creates the immutable link-scoped parked ref immediately after fetch and before the live
transaction.
A crash before parking has changed no live state and can refetch; a crash after parking is locally restart-safe.

**Transport validity is not database admissibility.**
A structurally valid Cod Sync chain can still point at a missing, corrupt, unrelated or schema-incompatible `core.db`.
In particular, the generic delta algorithm interprets a table absent from a version as deletion of the ancestor's rows.
The Manager now requires a common ancestor and matching supported NoteToSelf schema shape before applying any delta.
This is deliberately application policy rather than a new rule in Cod Sync or splice-merge.

## Review correction: Git needs its own SQLite-protected capture (2026-08-31)

The row-transaction design protected live adoption but initially stopped protecting the database at `COMMIT`.
The next step for a divergent source was `git add core.db`.
That is not a SQLite read: Git opens the database as an ordinary file and neither takes SQLite's locks nor consults
its rollback journal.

In rollback-journal mode, a writer can spill changed pages into the main database before its transaction commits.
SQLite readers remain safe because they honor the file locks and journal.
A raw file reader that overlaps those writes can capture a mixture of the old and new states.
That means the live database could remain healthy while the Git blob published to another device is incoherent.
The same pre-existing race existed in `push_note_to_self`, and D10's logical-dirty comparison did not close it.

D12 adds a second, short `BEGIN IMMEDIATE` transaction after divergent row adoption.
The transaction makes Git's `stage`/`write_tree` capture stable by preventing another SQLite writer from starting.
The transaction ends before commit construction, ref movement or network work.
Push uses the same reservation across its logical comparison and path-scoped commit, then releases it before publication.
The existing direct NoteToSelf commits for participant creation, device admission and app registration use the same helper,
so the branch does not leave a second raw-capture idiom beside the fixed paths.
No new lock file is necessary because the Hub and Manager already coordinate through SQLite.

## Review correction: source-tree and UI contracts are application policy (2026-08-31)

Schema validation alone did not protect the fast-forward path.
`read_tree(source)` adopts every path in the source tree, while Cod Sync validates only transport structure and ancestry.
Because #190 has not connected stored history to an admitted device, the Manager cannot assume an incoming tree has the
one-file layout created by provisioning.
D13 now requires exactly one regular `100644` blob named `core.db` before any source affects live state, the index,
the work tree or `main`.
Cod Sync supplies only a generic tree-entry reader; the one-file rule remains in the Manager.

The earlier surface description also left route placement and Hub gating to the implementer.
Unit 5 now chooses an index-page NoteToSelf sync card with three POST actions.
Push and refresh require the existing passthrough Hub session, integration remains available offline,
and refresh or integration can update the team sidebar through the UI's existing out-of-band fragment pattern.

## Implementation notes (2026-08-31)

### Deviation: one existing splice-merge test had to change

The plan promised `packages/splice-merge/tests/test_merge.py` would be untouched,
on the reasoning that driver output is unchanged for every case it covers.
That was right about the driver and wrong about the test file.
`test_divergent_insert_under_one_id_still_warns_and_keeps_ours` called `reconcile_deltas` directly
and asserted on `capsys` stderr, so removing stderr I/O from that function necessarily broke it.
It now asserts the returned conflict set instead, and is renamed accordingly.
The sibling identical-insert case dropped `capsys` for the same reason.
No behaviour the driver produces changed: `cli.py` prints the same four warning strings
from the returned conflicts.

### Two probes that had not been run before

`git ls-tree -z` and `git read-tree` / `write-tree` / `commit-tree` all work with `--git-dir` alone
and no work tree, which is what lets Unit 2 stay index-and-object-only.
`git commit-tree` needs a committer identity like any other commit,
so it inherits the same developer-global-config assumption `Repo.commit` already had.

`BEGIN IMMEDIATE` on the attached Manager connection behaves as the plan assumed, including across
processes: a second connection's `BEGIN IMMEDIATE` fails with "database is locked" while the first is held.
`PRAGMA foreign_keys` toggles outside the transaction and `PRAGMA main.foreign_key_check` reads inside it.
Python's `sqlite3` needs `isolation_level = None` on any connection this module drives manually,
otherwise pysqlite interleaves its own implicit `BEGIN`.

### A test caught a hole in the test, not the code

The first version of the unrelated-history test passed `parents=[]` to a helper that did
`parents or [base_sha]`, so the "unrelated" commit silently kept its parent and adopted as a fast-forward.
Recorded only because the failure looked like a production bug and was not:
`merge_base` returning `None` is refused exactly as designed once the commit really has no parents.

### Deliberately not done

`fragments/acceptance_token.html` still inlines its own copy of the sidebar markup.
Unit 5 needed an out-of-band sidebar replacement and factored `fragments/sidebar_teams_body.html` out
rather than write a third copy, but moving the acceptance fragment onto it is unrelated to this branch.

`push_note_to_self` still assumes the NoteToSelf repository has a commit:
`live_differs_from_head` reads `HEAD:core.db`, so an unborn repository fails loudly rather than
falling back to a whole-index commit.
Every path that reaches push creates that commit first
(`create_new_participant`, or `bootstrap_existing_identity`'s checkout),
so the fallback would be unreachable code guarding an impossible state.

## Review fixes (2026-08-31)

SQLite permits duplicate `NULL` values in a non-integer primary key.
`compute_delta` previously built its row index with a dict comprehension,
so two such rows collapsed into one and a merge could record source ancestry after applying only one row.
Splice Merge now raises on every duplicate logical row key and uses `IS` for a single nullable key,
which preserves the one-row case without inventing an identity for an ambiguous set.
The Manager translates an ambiguous source or ancestor into `incompatible_source`
and ambiguous live state into `constraint_refused`.

The unborn-repository branch had checked only the one-file Git tree before checkout.
It now compares the source database with a temporary database built from this Manager's supported schema,
and both identity-bootstrap entry points park and adopt the fetched head through that same path.

Finally, the second writer reservation can time out after row adoption has already committed.
That failure is a `sqlite3.Error`, not a Cod Sync `RepoError`, so the recording boundary now maps both
to `recording_pending` and preserves the UI's truthful partial-success report.

The focused review-fix run passed 78 micro tests.
The broader Splice Merge, Cod Sync, Manager and `ssc-files` regression set completed across an interrupted
and resumed run with no failures: 399 tests passed before interruption and the remaining selection passed 168.
