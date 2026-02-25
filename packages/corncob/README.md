The CornCob protocol is a relatively simple hash chain thing designed to work with git.
(It can probably stretch to git-like things, but for now we are focused on git specifically.)
"COB" is an abbreviation of chain of bundles.
Git has a feature called _bundles_ which is a way to save particular slices of a repo that assume readers of the bundle already have certain prerequisite data.
One way to think about bundles is that in the git network protocol, during a pull the two sides have a little negotation about what the receiver actually needs.
A bundle is similarly pieces of a git repo, where the needed pieces are baked in at bundle creation time.

A CornCob repo is a chain of bundles.
If the chain is complete, then a reader can start at the oldest link and work their way forward in time applying the bundles to come up to the same repo state.
Alternatively, if the reader is a clone that is somewhat out of date, it can start at the newest link and work backwards until it finds a bundle whose prerequisites it already has.

### Why Store a Repo this Way?

The reason this is interesting is that it makes it possible to use relatively dumb cloud storage to share a repo.
The most recent link in the chain can be updated atomically (atomic update of a single file is the only special primitive required from the storage service).
