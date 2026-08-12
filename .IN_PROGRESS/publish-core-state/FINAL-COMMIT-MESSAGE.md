Make `TeamManager.push_team` publish completed Core state (#184)

`push_team` pushed whatever happened to be committed, so a completed Manager mutation that committed nothing of its own stayed outside the published chain with no warning.
It now commits the team `core.db` before publishing, using a new path-scoped `Repo.commit_paths` so publication names what it publishes instead of inheriting whatever sits in the index.
`get_team_sync_status` reports `needs_push` for that same uncommitted Core state, sharing one `work_tree_paths_differ_from_head` check with the commit so status and publication cannot drift apart.

Publication now returns `published` or `already_published`.
The no-op case is decided from the local `.ss_last_push` marker before any Hub session opens, so an unchanged re-push neither prompts for a PIN nor asks Cod Sync to build the empty bundle Git rejects.
The marker is written from the head captured before the remote call and only after it succeeds, so a failed publication leaves the work committed, the marker untouched, and status at `needs_push`.
The CAS error now says the participant's own published chain changed, replacing advice to pull from peers that never applied to this failure.
