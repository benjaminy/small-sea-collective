---
id: 0006
title: Fill in small-sea-manager spec skeleton
type: spec
priority: low
---

## Context

`packages/small-sea-manager/spec.md` is a skeleton with section headers but almost no content. Most sections (Data Model, Operations, Sync & Merge, SQL Schemas, API Surface) are empty or have single placeholder lines.

## Work to do

- Data Model: describe Participant, Team, Invitation records
- Operations: Create Participant, Create Team, Invitation flow (send, accept, decline)
- Notification Services: what events are emitted, to whom
- Sync & Merge: how manager data syncs across devices
- SQL Schemas: the actual table definitions
- API Surface: the public interface exposed to application code

## References

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-manager/small_sea_manager/manager.py` — current implementation for reference
- `packages/small-sea-manager/small_sea_manager/provisioning.py` — schema definitions
