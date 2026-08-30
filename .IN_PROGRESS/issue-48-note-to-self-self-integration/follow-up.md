# GitHub issue changes after this branch

## #48 — Manager: multi-device NoteToSelf sync and team discovery

Close it.
Both halves are now done: the earlier branch landed discovery, and this one lands integration.
Note in the closing comment what the branch actually settled, because the issue text asked for
something narrower than what was built:

- The issue's "clean-work-tree preflight" was written for a `git merge` design and does not exist.
  Adoption is a row-level merge inside one SQLite transaction, so a dirty `core.db` is ordinary
  local work, not a refusal. An unresolved `MERGE_HEAD` is still an unconditional refusal.
- NoteToSelf cleanliness is logical, not byte-level.
  A refresh can leave a database that holds exactly `HEAD`'s rows in different bytes, and
  `push_note_to_self` deliberately does not commit that.
- The Manager makes no claim about which device wrote an integrated history (see #190 below).

## #226 — team Core merge policy

Add a comment recording what this branch answered for one store, and what it did not:

- The adoption boundary for a SQLite-backed store is a SQLite transaction, not a checkout.
  A candidate worktree relocates the problem rather than solving it, and #226's own warning about
  that is confirmed rather than worked around.
- A typed semantic refusal is demonstrably cheap once the merge runs in process:
  `reconcile_deltas` returns its conflicts instead of printing them, and the caller decides.
- NoteToSelf deliberately does *not* install the `splice-sqlite` driver (D1),
  which is a divergence from team repos. When #226 settles Core's merge rule,
  the two should be reconsidered together rather than left silently different.
- This branch is *not* precedent for teammate Core admissibility. Self-store adoption only
  widens the blast radius of a trust posture that `refresh_note_to_self` already had.

## #228 — team Core integration implementation

Add a comment pointing at `note_to_self_sync.py` as a worked example of the shape,
and at `web.py`'s `push_team` handler, which still says the Manager cannot combine histories.
That message is honest for team Core today and #228 owns replacing it.

## #181 — Hub direct NoteToSelf Core DB access vs Manager exclusivity

Add a comment, because this branch changes what the issue is choosing between.

The issue is written about Hub *reads*, and its option 2 offers to "narrow the mandate to permit
specific read-only Hub access".
That option is no longer available as stated: the Hub writes the participant's NoteToSelf `core.db`
directly, inserting `cloud_storage` rows (`backend.py:1075`) and updating
`berth_cloud_allocation.location` (`_writeback_locator`, `backend.py:1266`).
Whichever boundary #181 picks has to cover writes.

The Hub being a co-writer is now load-bearing rather than incidental.
Every Git read of the live NoteToSelf database — publication, merge recording, and the three direct
provisioning commits — runs under a SQLite writer reservation (D12) precisely because a second
process can have uncommitted rollback-journal pages in that file, and adoption is a row merge inside
a SQLite transaction (D2) because SQLite's cross-process write lock is the only serialization both
sides already honour.
If #181 resolves toward option 1 and the Hub goes through a Manager-owned API, that reasoning should
be revisited rather than inherited: the reservation stays harmless, but it would no longer be
protecting against anything, and the argument against file replacement would need a new basis.

## #190 — Cod Sync link-authorship verification

Add a comment: this branch adds no verifier seam, on purpose.
The parked ref is created by `publish()`'s observation path, which imports through fetch,
so verification belongs there rather than inside an application's integration operation.
The Manager's result text and UI say a source is a transport-valid, tree-compatible and
schema-compatible stored history, never that a sibling device authored it;
whoever lands #190 should check those strings still under-claim.

## #36 — conflict resolution product

Add a comment: a semantic refusal now reports table, key and conflict kind,
and the parked source stays outstanding until someone resolves it.
That report is the input a resolution UI would build on.
Row-level preview stays out of scope here because it needs a NoteToSelf-specific renderer:
`git diff` on `core.db` says "Binary files differ", and any legible summary has to be computed
from rows by the application that owns the schema.

## New issue — move `ssc-files` onto the shared parked-ref discovery helper

`cod_sync.protocol.outstanding_parked_heads` is now the shared implementation of
"maximal outstanding parked heads not already in local history".
`ssc_files.files._outstanding_parked_refs` / `_parked_self_refs_to_merge` still have their own copy
over the same ref namespace.
Moving them is mechanical but changes a package this branch does not otherwise touch.

Note when filing it that `ssc-files` keeps `Repo.merge` deliberately (D7):
for arbitrary user files, leaving conflicts in the checkout for a person to resolve is right
and rolling back is wrong. Only the discovery half should be shared.

## New issue — canonical logical representation for SQLite in Git

Optional, and only if a future design needs byte-clean work trees after safe cross-process adoption.
Today the cost is an intentionally byte-dirty work tree after some logically clean refreshes,
and `live_differs_from_head` absorbs it.
