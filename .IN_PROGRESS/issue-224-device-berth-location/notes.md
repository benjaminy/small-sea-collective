# Issue 224 working notes

## Current framing

Issue #224 is useful history, not a current-state specification.
Several assumptions in its provenance predate the established-teammate route flow and steady-state NoteToSelf integration.

The current architecture distinguishes these objects:

- A berth is the logical `team scope x app` resource and authorization boundary.
- A `berth_cloud_allocation` is this participant's selected physical storage for one berth.
- A `teammate_berth_storage_announcement` is the team-visible route to that participant's published replica of the berth.
- A device is a local actor holding its own team key, sender state, provider credentials, and checkout.

Calling a berth itself a cloud location obscures the fact that every teammate may publish a different replica of the same logical berth.
The physical object at stake in #224 is the participant's berth replica or allocation.

## What the repository does now

`berth_cloud_allocation` lives in shared NoteToSelf state and is unique by `berth_id`.
It has no device coordinate.
Provider credentials live in the device-local NoteToSelf database and refer to the shared `cloud_storage` row.

Peer routing is scoped to `(teammate_id, berth_id)`.
The Hub peer API accepts a teammate ID, not a device ID, and route selection has no device dimension.
`signer_key_id` records which trusted team device attested the route; it does not currently confer ownership of that route.

One teammate's devices publish into one Cod Sync store and one CAS-protected chain.
Cod Sync already treats concurrent device publications as competing writes to that shared chain and requires integration when histories diverge.
It does not require a distinct storage location per device.

`finalize_linked_device_bootstrap` now records the new team-device key, certificate, prekey bundle, and sender state only.
It neither allocates storage nor publishes a storage announcement.
Its comment says the device shares the teammate's route, but the linked-device micro tests do not exercise allocation, credentials, route selection, or cloud I/O.

Steady-state NoteToSelf integration landed after issue #224 was filed.
That makes allocation changes genuinely shared between sibling devices.
A concurrent update of the same allocation row becomes a semantic conflict; two independently inserted replacement rows for one berth should violate the unique berth index and be refused.
There is not yet a focused micro test proving those allocation-conflict cases.

## Immediate product gap

A linked device receives shared cloud-account and allocation rows but deliberately receives no provider credentials.
The existing multi-device tests insert credentials for the inherited `cloud_storage.id` directly into SQLite.

The Manager has no supported equivalent.
"Add cloud storage" always creates a new shared `cloud_storage` row, and current guidance tells a device with missing credentials to add and select a replacement account.
That turns a device-local credential gap into a participant-wide route change.
"Remove" likewise deletes both the local credential and the shared account row.

If devices are subordinate to a participant-owned berth replica, the Manager needs to distinguish:

- connect or disconnect this device's credentials for an existing shared account;
- add or remove an account from the participant's shared configuration;
- move the participant's berth allocation to another account or location.

## Working design position

One participant should have one published replica location per berth.
Linked devices should share that allocation and route rather than acquire device-specific storage locations.

Any currently trusted team-device key may attest or repair the participant's route.
The signer identifies the actor and supplies verifiable authority; it does not become the allocation owner.
Designating one device as owner would make a subordinate device part of the public storage address and would require a separate ownership-transfer and dead-device recovery protocol.

Linked-device bootstrap should not publish a new route merely because a device joined.
The route changed only if the participant's allocation changed.
A new device without local credentials is locally unable to use the shared storage until the user connects credentials for the already-selected account.
It must not silently create a different allocation.

Future device-addressed P2P streaming should use a separate, explicitly device-scoped discovery object.
It should not add a device coordinate to berth storage routing.

## Unsettled design work

The current announcement selector chooses the greatest UUIDv7 across every valid sibling-device announcement.
That is adequate for serial updates but silently chooses between concurrent sibling-device route changes.
The Core route reconciliation design record explicitly leaves this case open.

The leading option is to serialize the participant's allocation decision through the existing NoteToSelf Cod Sync chain before publishing its team-visible projection.
An honest device that loses the NoteToSelf CAS race would have to fetch and integrate rather than publishing its competing route.
This keeps sibling coordination participant-private and reuses the conflict handling already required for the shared allocation row.
The failure gap between publishing NoteToSelf and publishing the team route is retryable: peers retain the old route while the participant's devices know the new allocation still needs announcement.

A stronger alternative is to give route changes signed causal predecessors.
That would form a route-selection DAG for each `(teammate_id, berth_id)`: one active tip is usable, concurrent tips are an explicit conflict, and a later signed selection can name and resolve all tips.
This is needed only if peers must independently detect equivocation or concurrent publication by still-trusted sibling devices.
It adds a second conflict protocol for state whose source of truth already lives in shared NoteToSelf.

Questions still to answer:

- Is participant-private NoteToSelf serialization sufficient, or must peers independently detect concurrent or equivocal route publications through a route DAG?
- Should every trusted device be allowed to change the shared route, or should local NoteToSelf policy restrict which devices expose that action without changing peer verification?
- Must route reconciliation publish the allocation change to NoteToSelf successfully before publishing the team-visible route?
- Should a route announcement bind the private allocation generation ID as well as the public locator?
- When a signer is later untrusted, should selection fall back to an older route signed by another still-trusted device, or treat the participant's current route as unresolved?
- Is the missing "connect credentials to this device" flow part of #224 or a required follow-up that #224's validation should expose?
