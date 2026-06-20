# Follow-up

## Role-aware ordinary replication

The vocabulary survey exposed a runtime gap behind the proposed terminology.
`small_sea_hub.server._refresh_session_peers(...)` currently adds every teammate to `watched_peers` without consulting the session berth's `berth_role` row.
The watcher therefore observes peer signals broadly even though the intended `read-only` model would avoid fetching and integrating that teammate's ordinary berth publications.

Do not mechanically add a role filter before the proposal-discovery design is settled.
A proposal-only teammate still needs a lightweight route for announcing a merge request to potential integrators.
Issue #162 tracks that broader design.

When implementation work begins, distinguish at least:

- observation of lightweight proposal announcements
- fetching ordinary berth publication bodies
- integrating fetched ordinary changes
- fetching and integrating an explicitly selected proposal

Micro tests should prove that a proposal-only teammate's ordinary changes are not fetched while their merge-request announcement remains discoverable.

## Remaining vocabulary surfaces

This conservative branch intentionally leaves runtime identifiers, UI labels, and serialized role values unchanged.
A later implementation branch may want to revisit:

- the `read-only` / `read-write` values stored in `berth_role`
- the Manager UI's “Core berth role” and related action labels
- exception messages such as “requires read-write permission on the Core berth”

Those changes should follow a settled runtime model rather than lead it.
