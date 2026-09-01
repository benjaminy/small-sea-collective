# Design record

Three things this branch learned that the code cannot show, because two of them are
designs that were rejected and one is a mistake that was caught before it landed.
Everything else worth knowing — why adoption is a SQLite transaction, why cleanliness is
logical rather than byte-level, why the failure contract is retry — is in
`note_to_self_sync.py`'s module and function docstrings, where a reader will already be.

## Never compute the merge delta from live to merged

An earlier iteration of this work built a merged tree with Git and then looked for a safe way
to land the merged `core.db` in live state.
Its adoption step was: diff the live database against the merged database, apply that difference.
That operation deletes every row present in live and absent from merged.
Since "merged" was computed from a snapshot taken before the live database moved, any row a
concurrent writer added in between was silently destroyed.
The plan of the day then spent a page defending against exactly that hazard with a stale-base
precondition and a durable recovery record.

`splice-sqlite` never did this.
It computes `theirs_delta = compute_delta(ancestor, theirs)` and applies it onto ours, so a row
it has never heard of is a row it cannot touch.
Binding "ours" to the live database and running the driver's own algorithm removes the hazard
rather than guarding it, and the lease, watermark and recovery record all disappear with it.

The direction of the delta is the whole safety argument.
Anyone reopening this — for team Core, or for any other SQLite-backed store — should check that
first and stop reading if it is wrong.

## The file-level adoption family is closed, and not for the reason it looks like

Two designs in this family look right and are not.
Both were probed rather than argued down, and the probe results are the durable part.

**Replacing the live file, even from an exact source blob.**
The obstacle is that the Hub is a second long-lived process writing the same database, so any
connection open across the swap keeps the old inode and writes into an unlinked file.
SQLite's backup API was probed as the way to keep both connection safety and exact source bytes.
It does preserve an observer connection opened beforehand — the observer sees the new rows and
can write afterward — but it does **not** leave the destination byte-identical to the source.
So it buys connection safety at the cost of the byte-exactness that was the only reason to want it.
That is why a fast-forward advances `main` without materializing the source blob, and why the work
tree is intentionally byte-dirty afterward.

**Evaluating the merge off to the side.**
`git merge-tree --write-tree` was probed twice on git 2.50.1 and works better than expected:
it honors custom `merge=` drivers from `.gitattributes` (verified with a marker-writing driver),
needs no work tree, index or checkout, works with the cwd outside the repository entirely, and on
driver failure leaves `HEAD`, the index, the work tree and `MERGE_HEAD` all untouched, so a
conflicted side merge needs no rollback.

It was dropped anyway, and the reason generalizes: evaluation was never the hard half.
Adoption was.
A side merge produces a tree, and turning a tree into live effect lands back on the file-replacement
problem above.
Reach for `merge-tree` when a consumer needs a *tree* — not when it needs live state to move.

## A parked head is safe to leave alone because nothing is precomputed

A candidate tree is a snapshot of an evaluation, and it goes stale the moment local state moves.
That invites showing someone one thing and applying another.
Because the computation here is always `(base, source, live-now)` and nothing is stored between
runs, a parked head can sit untouched for weeks and every preview of it is still correct when
it is finally shown.

This is what makes "integrate it whenever you like" a real property rather than a hopeful one,
and it is a constraint on #36: a preview UI may cache nothing it does not recompute at display time.
