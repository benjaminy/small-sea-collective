# Follow-up for issue 224

Drafts for GitHub changes.
Nothing here has been posted.

## Comment on #224

Decision: a linked device shares the teammate's Core berth allocation and storage announcement.
It does not own a distinct location.

The physical object is the participant's berth replica, selected by the `berth_cloud_allocation` row in shared NoteToSelf state.
Every trusted team device of that participant is an equal representative of that replica.

Answers to the three questions in the issue body:

- A linked device shares the participant's allocation and route.
  Bootstrap does not allocate storage and does not publish a route, because the route changes only when the allocation changes.
- Any currently trusted team-device key may publish the route.
  Selection stays `(teammate_id, berth_id)`-scoped.
  `signer_key_id` identifies the actor and supplies verifiable authority; it does not confer ownership of the allocation or the route.
- Any trusted device may rotate or repair the route.
  There is no owner device.
  Designating one would put a device coordinate into the public storage address and require an ownership-transfer and dead-device recovery protocol that the project has no need for.

Sibling coordination goes through the existing NoteToSelf Cod Sync chain.
A device publishes the allocation change to NoteToSelf first, then publishes the team-visible route as a projection of it.
A device that loses the NoteToSelf CAS race fetches and integrates rather than publishing a competing route.
The gap between the two publications is retryable: peers keep the old route, and the participant's devices know the new allocation still needs announcing.

Rejected alternative: a signed route-selection DAG per `(teammate_id, berth_id)` so peers can independently detect concurrent or equivocal publication by still-trusted siblings.
That is a second conflict protocol for state whose source of truth already lives in NoteToSelf, and it serves a threat model the project does not target.
If one of a participant's devices misbehaves, the remedy is untrusting it.

Smaller points settled the same way:

- When a signer is later untrusted, selection falls back to the newest announcement from a still-trusted device, which is what the current selector already does.
- Binding the allocation generation ID into the announcement is deferred; nothing needs it yet.
- Local Manager policy may restrict which devices expose the route-change action, but peers do not verify that.

Work unblocked by this decision is tracked separately: #NNN (device credential connection) and #NNN (allocation-conflict micro tests).
Closing this issue as decided.

## New issue: Manager flow to connect a device's credentials to an existing shared cloud account

Labels: type:task

A linked device inherits the participant's shared `cloud_storage` and `berth_cloud_allocation` rows through NoteToSelf integration but deliberately receives no provider credentials.
The Manager has no supported way to supply them.

"Add cloud storage" always creates a new shared `cloud_storage` row, and current guidance tells a device with missing credentials to add and select a replacement account.
That turns a device-local credential gap into a participant-wide route change.
"Remove" deletes both the local credential and the shared account row.
The existing multi-device tests sidestep this by inserting credentials for the inherited `cloud_storage.id` directly into SQLite.

Per the decision on #224, devices are subordinate to a participant-owned berth replica, so the Manager needs to distinguish three operations:

- connect or disconnect this device's credentials for an existing shared account;
- add or remove an account from the participant's shared configuration;
- move the participant's berth allocation to another account or location.

Scope of this issue is the first operation, plus separating it from the other two in the Manager surface and guidance.
A device with no credentials for the selected account is locally unable to use the shared storage until the user connects them.
It must not silently create a different allocation.

Validation: a linked-device micro test that inherits the allocation, connects credentials through the Manager, and performs cloud I/O against the shared location without any new `cloud_storage` row or announcement appearing.

Related: #224, #139.

## New issue: Micro tests for concurrent sibling-device allocation changes

Labels: type:task

Steady-state NoteToSelf integration makes `berth_cloud_allocation` genuinely shared between a participant's devices, but no micro test exercises what happens when two devices change it concurrently.

Cases to cover:

- Two devices update the same allocation row from a common ancestor.
  Expected: a semantic conflict surfaced by integration, not a silent last-writer-wins.
- Two devices each insert a replacement allocation row for the same berth.
  Expected: the unique index on `berth_id` refuses the second on integration.
- A device that loses the NoteToSelf CAS race does not publish a team-visible route for its rejected allocation.

The third case pins the ordering settled on #224: NoteToSelf publication precedes route publication, and a losing device integrates instead of announcing.
The current announcement selector picks the greatest UUIDv7 across sibling announcements and would otherwise silently choose between concurrent route changes; the Core route reconciliation design record leaves that case open.

Related: #224, #208.
