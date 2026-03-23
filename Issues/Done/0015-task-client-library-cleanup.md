---
id: 0015
title: Rewrite small-sea-client: fix session flow, add tests
type: task
priority: high
---

## Context

The small-sea-client library had a broken session flow, typos (e.g., `sessiom`), empty stubs, and no tests. This was one of the three blocking items identified in `Scratch/WIP.txt`.

## Resolution

Completed in commit `9e588bf`: dropped the CLI, fixed the session flow, added tests.
