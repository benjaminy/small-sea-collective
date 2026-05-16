# Design Record: Berth Cloud Location Semantics

Issue #134 settles the cloud-location model exposed by #123.
The old model mixed participant cloud accounts, provider-facing storage names, Hub sessions, and peer routing.
The new model separates them:

- Cloud account locators live in shared NoteToSelf.
- Device cloud credentials live in device-local NoteToSelf.
- Local berth cloud allocations choose where this participant stores one berth.
- Member berth storage announcements tell peers where one member stores readable data for one berth.

The central decision is that Hub sessions and cloud provisioning are separate.
A valid session authorizes an app to act in a berth, but file operations may still fail because no storage location exists or because this device lacks credentials.

Provider-facing storage locations are explicit allocation state, not formulas derived from `berth_id`.
The Manager records desired or finalized locations.
The Hub performs provider I/O and may write provider-issued final locators back to the allocation row as a narrow exception because it is recording provider reality, not making policy.

Peer routing is scoped to `(member_id, berth_id)`.
This matters because different teammates, and even different same-member sibling devices during a race, may store the same berth in different providers or locations.
Valid member-berth storage announcements take precedence over legacy `team_device(protocol, url, bucket)` fallback.

Materialization is lazy but explicit.
Team creation and app activation do not pre-allocate the whole app/team cross-product.
The Hub materializes a recorded allocation through `/cloud/setup` or first storage use, returns stable repairable errors, and publishes no peer-visible announcement until the location is successfully materialized.

Concurrency is handled conservatively.
V1 assumes one Hub per device/participant root.
Local Manager-like writers coordinate through SQLite and conditional updates.
Cross-device first-use races may create orphaned provider objects, but peers select newest valid announcements by UUIDv7 and should not silently route to the wrong location.
