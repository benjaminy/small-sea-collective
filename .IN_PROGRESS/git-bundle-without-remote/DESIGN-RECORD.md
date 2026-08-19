# Design record: bundle plumbing without synthetic remotes

## Exact prerequisite equality was the wrong invariant

The plan required an incremental bundle's actual Git prerequisites to equal exactly the one prerequisite its link declares.
That cannot be implemented.
`git bundle create ^<stored-head> refs/heads/main` records a prerequisite for every boundary commit,
so merging a branch rooted before the stored head — the ordinary result of adopting a teammate's parked ref — produces two or more.
Exact equality would have made the most common Small Sea workflow unpublishable.

The implemented rule keeps the property the strict one was reaching for.
The declared predecessor must appear among the actual prerequisites, and every extra prerequisite must be an ancestor of it.
Then anyone who can satisfy the declared prerequisite can satisfy the whole bundle,
and the chain walk can still stop at `previous.head`.
A prerequisite outside that history is a hidden dependency, and the chain is rejected before any ref moves.

## Divergence is loud and stuck, on purpose

With no server serializing writes,
divergence is the ordinary outcome whenever a second device publishes between this device's last fetch and its next publish.
Cod Sync raises and does not resolve it.
Resolution needs a fetch, a merge, a work tree, and a retry across a new CAS window,
and Cod Sync may be running against a repository with no work tree at all.
How much of that should happen without asking is a per-caller question.
The user-visible result is a publication that fails and stays failed until someone pulls.
That is the intended trade: loud and stuck beats quiet and wrong.

## Absence has to be exact, all the way down

Cod Sync reads one signal as "this store is empty" or "this predecessor is gone":
an object the provider confirmed is missing.
Before this branch every non-200 became `(None, None)` in the store and every failed read became a 404 at the Hub,
so an expired session or a provider outage was indistinguishable from a chain that never existed.
The distinction now travels as a value (`CloudDownloadFailure`) from the adapter that made the request,
through the endpoint, to a typed store error.
An unknown session token also got its own exception class,
because the Hub's blanket 404 for missing resources would otherwise have told a client that an authentication failure was an empty store.

## Google Drive remains experimental, not conforming

Writable Cod Sync needs an atomic create-only first `latest-link.yaml` write.
The current Google Drive adapter looks up a file by name and creates it when absent,
but Drive names are not unique, so two publishers can both observe absence and both create a head.
The adapter remains functional for provider and integration testing,
but it is explicitly unsafe for real writable Cod Sync data.
S3, Dropbox, and `LocalFolderStore` are the conforming writable implementations.
A Google Drive design keyed by a provider-enforced unique file ID is follow-up work;
this branch does not add a runtime gate or a generalized capability layer.

## Structural validation is not authorship

Cod Sync proves that a bundle advertises the head its link declares,
that the prerequisites line up,
and that the promised ancestry holds.
It proves nothing about who wrote a commit or whether a fetched history should have local effect.
Links are signed and signatures can be verified, but no production path calls the verifier;
tightening structural validation here must not be read as having closed that gap.
Follow-up carries it.
