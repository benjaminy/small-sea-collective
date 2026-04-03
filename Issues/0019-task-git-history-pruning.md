---
id: 0019
title: Git history pruning (squash old commits to bound repo size)
type: task
priority: low
---

## Background

The Cod Sync protocol promotes a good pattern for managing durable Small Sea data in an application:
- Make a folder for each team.
- Make the folder into a git repo.
- Sync any changes in the folder as a chain of deltas (git bundles) using Cod Sync.

By default, git repositories keep all historical commits.
For many applications that don't have a particular need to access historical/archival state, this history is pure overhead.
(Some small window of recent history is important to keep to support 3-way merge.)

## Goal

Every once in a while, reduce this overhead by pruning historical data out of the repo.
We would prefer to do this without changing the hashes for any existing git commits.
This is a tricky non-standard use of git.
The idea is to do partial clone (with `--filter=blob:none`) to get a clone of the repo with only the commit DAG but no content data.
Then checkout a window of recent commits that we know might be needed going forward to ensure the new clone has all the blobs it might need to rehydrate any of those commits.
