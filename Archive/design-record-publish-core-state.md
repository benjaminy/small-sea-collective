# Design record — publish Core state (#184)

## Publication commits the team `core.db`, and does so path-scoped

`push_team` now commits `core.db` before publishing.
Before this branch a Manager mutation that committed nothing of its own could stay outside the published chain with no warning, reachable through `publish_teammate_berth_storage_announcement`, `announce_teammate_transport`, and `set_team_admission_policy`.

The commit is path-scoped (`git commit -- core.db`), not the whole-index `Repo.commit` that `push_note_to_self` uses.
Publication decides what enters the shared history, so it must name what it publishes rather than inherit whatever a previous operation left staged.
`Repo` therefore grew `commit_paths` as a separate method rather than a `paths=` argument on `commit`: `commit` commits the index, while `commit_paths` commits work-tree content at the named paths.
Its no-op check is `git diff --quiet HEAD -- <paths>`, scoped to the work tree.
Copying `commit`'s `--cached` idiom would guard the wrong thing — with `core.db` modified but unstaged, `--cached` reports a no-op while the scoped commit would still commit it.

An empty `paths` argument is rejected outright by both `commit_paths` and `work_tree_paths_differ_from_head`.
Git treats an empty pathspec as no restriction, so `git commit --` with no paths commits the whole index — silently doing the exact thing `commit_paths` exists to prevent, and doing it on the publication path.
Returning "nothing to do" instead would be the wrong guard: a caller that computed an empty path list has a bug, and swallowing it would hide an unpublished mutation rather than surface it.
`ValueError` is raised before the check runs, so neither method can be the quiet route to a whole-index commit.

The two publication idioms deliberately end this branch different.
Changing `push_note_to_self` is #48's business, once that issue is reconciled with the implemented push/refresh flow.

## Outgoing status and publication share one Git check

`get_team_sync_status` calls the same `work_tree_paths_differ_from_head` that the scoped commit delegates to.
A second independent check would let the two drift, and the failure is silent in both directions: the UI reports `needs_push` forever, or reports `synced` over unpublished work.
Neither step's own tests would catch it.

The no-HEAD branch stays first, because `git diff HEAD` is fatal where HEAD does not resolve.

## `already_published` is a local observation, not a remote fact

The no-op decision is made before the Hub session opens, because preparing `core.db` is purely local and a publication with nothing to send should not prompt for a PIN.
It means only that this installation's `.ss_last_push` marker confirms the current HEAD.
A missing marker does not prove the remote lacks HEAD, so publication is still attempted in that case, which leaves a markerless clone exposed to Cod Sync's empty-bundle defect.
That defect is filed separately rather than worked around by reading remote history into the Manager.

Deciding the no-op before session opening reorders the publication commit ahead of session opening.
A denied PIN or failed `ensure_cloud_ready` now leaves a fresh unpublished local commit where the old `push_team` would have failed before committing anything.
That is intended: the work is preserved.
After a previous successful publication its status is `needs_push`; after a failed first attempt it remains `never_pushed`, because no successful-publication marker exists.

## The CAS message states a fact, not a remedy

The old advice said to pull from peers, but a CAS conflict concerns the participant's *own* published chain, so pulling from peers is not the recovery.
The new message says what happened and that the local commit remains unpublished.
No recovery and no exception taxonomy were added; same-participant concurrency and integration semantics belong to #135.

CAS covers only the narrow etag race.
The adjacent hazard is quieter and untouched here: when the remote head is not present locally, `push_to_remote` falls back to a full `initial-snapshot` bundle and replaces the link, dropping remote-only commits silently.

## Publication witnesses read back through the Hub

The end-to-end test fetches what the Hub-backed remote actually received and looks for the announcement row there.
Asserting on local HEAD or mock call counts would let a commit-after-push implementation pass.
The witness mutation must be a helper that commits nothing of its own; one that commits its own `core.db` passes without the fix.
This shape needs MinIO, because the Hub has no `localfolder` storage adapter.
