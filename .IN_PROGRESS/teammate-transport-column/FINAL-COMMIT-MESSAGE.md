Remove the second teammate transport authority; show the real Core route (#210)

`teammate_transport_announcement` was a teammate-wide storage-routing table that no Hub path consulted.
Manager's Teammates column read it while Hub peer routing read the berth-scoped `teammate_berth_storage_announcement`, so after issue #183 delivered a valid route Alice could hold a working route to Bob's Core storage while the UI reported `Transport missing`.
The teammate-wide table also cannot express the architecture's per-berth allocation model, in which one teammate may place different berths in different providers.

This deletes that protocol outright — its fresh-schema table, signing and selection types, Manager publication path, web endpoint, and manual form — and makes the column report the newest valid berth-scoped announcement for the team's `SmallSeaCollectiveCore` berth, named `Core route` and reading `announced` or `missing`.
The two selector properties that only the removed selector proved were ported to `select_effective_teammate_berth_storage` first, so the sole remaining routing authority did not lose coverage.
`USER_SCHEMA_VERSION` is bumped to mark the intentional pre-alpha schema break; older research workspaces hit the established delete-and-recreate boundary rather than an in-place migration.
