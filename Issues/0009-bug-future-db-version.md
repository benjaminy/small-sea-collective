---
id: 0009
title: Replace "DB FROM THE FUTURE" print with real error handling
type: bug
priority: low
---

## Context

Two places in the codebase handle the case where a database's `user_version` is newer than the running code supports — a forward-compatibility check. Both currently just print a TODO message and presumably continue in an undefined state.

## Work to do

- Decide on the correct behavior: raise an exception? Return an error code? Show a user-facing message?
- Replace both `print("TODO: DB FROM THE FUTURE!")` calls with real handling
- Consider whether this error should be surfaced differently in Hub vs in the manager/provisioning layer

## References

- `packages/small-sea-hub/small_sea_hub/backend.py:214` — `_check_schema_version()`
- `packages/small-sea-manager/small_sea_manager/provisioning.py:282` — `ensure_note_to_self_schema()`
