<img src="../../Documentation/Images/cod-sync.png">

# Cod Sync

The Cod Sync protocol is a relatively simple hash chain thing designed to work with git.
(It can probably stretch to git-like things, but for now we are focused on git specifically.)
"COD" is an abbreviation of chain of deltas.
Git has a feature called _bundles_ which is a way to save particular slices of a repo that assume readers of the bundle already have certain prerequisite data.
One way to think about bundles is that in the git network protocol, during a pull the two sides have a little negotation about what the receiver actually needs.
A bundle is similarly pieces of a git repo, where the prerequisites are baked in at bundle creation time.

A Cod Sync repo is a chain of bundles.
The oldest link is a sufficiently complete snapshot of the whole repo.
A reader can work their way forward from there, applying bundles to come up to the current repo state.
Alternatively, if the reader is a clone that is somewhat out of date, it can start at the newest link and work backwards until it finds a bundle whose prerequisites it already has.

### Why Store a Repo this Way?

The reason this is interesting is that it makes it possible to use relatively dumb cloud storage to share a repo.
The most recent link in the chain can be updated atomically (atomic update of a single file is the only special primitive required from the storage service).

### History Compaction

The Cod Sync format has two challenges with long-lived repos with a nontrivial amount of churn:

1. The chain of bundles grows forever, such that starting at the beginning and coming up to date would take a really long time.
2. The repo itself by default keeps all its commits, which is unnecesary overhead for apps that only care about recent states.

Challenge 1 is pretty easy to deal with.
Every once in a while (exact schedule TBD), a chain compaction can be run:

1. Decide what link is the oldest that you want to keep.
2. Make a complete snapshot from that point in time.
3. Rebuild a new chain from that snapshot.

This is conceptually pretty simple.
It could be fairly expensive, but there's no need to do it frequently.

The repo itself is trickier to handle, but not impossible.
The core trick is that git can do clones that only contain:

1. The metadata for all commits (so that e.g. commit hashes can stay stable)
2. The blob data necessary to rehydrate some specified subset of commits

Using this we can make a repo that churns at a relatively high rate, but only accumulates a modest amount of space overhead (i.e. the commit metadata).
