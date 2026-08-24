Expose Core route publication and provider changes for established teammates

Adds `TeamManager.reconcile_team_route(team_name, cloud_storage_id, new_location)`
with `reconcile-route` and `cloud-storage` CLI commands and a Core Storage
section in the team detail UI. It repairs a missing or untrusted-signer route
and carries out an intentional account or location change, with no acceptance
artifact involved. Provider I/O stays behind the Hub, and neither surface
accepts a caller-supplied locator.

Changing the account or location now replaces the berth's single allocation row
with a fresh allocation ID. The Hub treats that ID plus the selected account as
the generation it materialized against -- on the plain-success path as well as
the locator writeback and lost-race recovery -- so a superseded materialization
returns `cloud_allocation_conflict` rather than claiming a replacement that
happens to share a location string. Manager signs only a reread of the same
generation. Both route paths now share one session/materialize/reread/publish/
commit helper; the invitation path keeps its acceptance gate and reporting
unchanged.
