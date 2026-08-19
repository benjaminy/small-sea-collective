Replace Cod Sync's synthetic Git remotes with direct bundle plumbing

Cod Sync now exchanges opaque bytes through a store while `Repo` performs the Git plumbing directly.
There are no synthetic remotes, remote-tracking refs, temporary tags, or work-tree scratch paths:
`publish()` and `fetch()` do their work in one OS temporary directory,
and the only durable ref a fetch can move is a pin the caller asked for, forward only.
The module boundary is one-way — `git.py` under `repo.py` under `protocol.py`,
with `format.py` owning the link codec and `store.py` owning transport —
so `protocol.py` runs no Git command and parses no YAML.

Two behavior changes matter to callers.
A store may only move forward: when the stored head is missing locally or is not an ancestor of local `main`,
publication stops before uploading instead of replacing the chain and reporting success,
which is what the old full-bundle fallback did to a teammate's commits.
And publication is now typed: an unchanged head is an explicit no-op rather than a string match on git's "Refusing to create empty bundle".
Version 2 of the link format follows from the first change.
Every link, including the first, gets a random id so two first publishers cannot collide on a sentinel,
and the first `latest-link.yaml` write is create-only.
There is no version-1 compatibility path.
The Hub's download endpoints now distinguish a provider-confirmed missing object from every other failure,
because Cod Sync reads a 404 as "this store is empty".
Provider upload conflicts likewise travel as structured values instead of error-string matches.
Google Drive remains available for functional testing but is explicitly non-conforming for writable Cod Sync:
its non-unique file names cannot provide an atomic first publication without a provider-ID-keyed design.
