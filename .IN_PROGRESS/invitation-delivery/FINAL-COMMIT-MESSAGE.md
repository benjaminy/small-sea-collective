Deliver the invitee's first Core storage route to the inviter (#183)

A signed `teammate_berth_storage_announcement` is the only source of peer
storage routing, which makes first contact circular: reading the invitee's Core
chain to find their announcement requires already knowing their storage.
The invitee's signed route now rides beside the `admission_acceptance` in the
courier token, and the inviter verifies it under the acceptance's own
self-certified device key before inserting it as an ordinary row.

Route publication moved out of `provisioning.accept_invitation` and into
`TeamManager`, which materializes through the Hub, rereads any provider-issued
locator, and signs only the final one.
Joining is now a two-phase local ceremony: the local join is one-shot, the route
is a derived pending state with a retryable preparation step, and the signed
acceptance is held in a new device-local table so it can be exported later
without being re-signed.
The acceptance token is withheld until a route is ready, so the invitee never
spends the proposal on a token the inviter cannot route back to.
No route-sidecar failure can cost the invitee an otherwise valid admission.

Note for developers: `LOCAL_SCHEMA_VERSION` goes 10 → 11 and device-local
migrations are still unimplemented, so every existing workspace must delete and
recreate `device_local.db`, losing sender-key state and with it existing team
memberships. That is the established pre-alpha idiom, but it is worth knowing
before pulling this.
