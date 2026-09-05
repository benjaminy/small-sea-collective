# Follow-up for issue 224

Drafts for GitHub changes.
Nothing here has been posted.

Posting order: create the credential-connection and allocation-coordination issues, replace `#CREDENTIALS` and `#COORDINATION` below with their numbers, update #139 and #235, then comment on #224 and close it as completed.

## Comment on #224

Decision: a linked device shares the teammate's Core berth allocation and storage announcement.
It does not own a distinct location.

The physical object is the participant's berth replica, selected by the `berth_cloud_allocation` row in shared NoteToSelf state.
Every trusted team device of that participant is an equal representative of that replica.

Answers to the three questions in the issue body:

- A linked device shares the participant's allocation and route.
  Bootstrap does not allocate storage or publish a route merely because a device joined.
  Repair may republish an unchanged location when its announcement is missing or its signer is no longer trusted.
- Any currently trusted team-device key may publish the route.
  Selection stays `(teammate_id, berth_id)`-scoped.
  `signer_key_id` identifies the actor and supplies verifiable authority; it does not confer ownership of the allocation or the route.
- Any trusted device may rotate or repair the route.
  There is no owner device.
  Designating one would introduce ownership-transfer and dead-device recovery rules that the project has no need for.

Required coordination: use the existing NoteToSelf Cod Sync chain to settle the shared allocation before publishing its team-visible route.
A device whose NoteToSelf publication requires integration or has an unresolved outcome must not announce its candidate allocation.
Integration conflicts require resolution before publication can continue.
This ordering is not implemented by `reconcile_team_route()`, which currently resolves the allocation locally and proceeds to route publication and commit without publishing NoteToSelf.
#COORDINATION owns implementation and validation, including retries after an interrupted announcement and preventing a delayed projection from superseding a newer allocation's route.
During the gap, peers retain their previous valid route; this does not guarantee that the old storage remains reachable.

Rejected alternative: a signed route-selection DAG per `(teammate_id, berth_id)` so peers can independently detect concurrent or equivocal publication by still-trusted siblings.
That would add a second conflict protocol for state whose source of truth already lives in NoteToSelf.
For this decision, peers rely on current device trust rather than independently detecting sibling equivocation.
A misbehaving device must be untrusted; ordinary concurrency between honest devices still requires #COORDINATION.

Smaller points settled the same way:

- When a signer is later untrusted, selection falls back to the newest announcement from a still-trusted device, which is what the current selector already does.
  That may select an obsolete location; an authorized device can repair the route from the shared allocation.
- Binding the allocation generation ID into the announcement is deferred; nothing needs it yet.
- Local Manager policy may restrict which devices expose the route-change action, but peers do not verify that.

Work unblocked by this decision is tracked separately: #CREDENTIALS (device credential connection) and #COORDINATION (allocation and route coordination).
#235 applies this ownership rule to both Core and Files berths and validates the resulting two-device flow.
Closing this decision issue as completed; the linked implementation work remains open.

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

This is the focused implementation issue for #235's existing credential-enrollment requirement and the device-local credential repair portion of #139.
Generic app-berth provisioning remains in #139; the complete two-device Files witness remains in #235.

Validation:

- A linked-device micro test inherits the allocation, connects credentials through the Manager, and performs cloud I/O through that device's Hub against local MinIO without a new shared account, allocation, or announcement.
- Disconnecting credentials leaves shared account and allocation rows intact and leaves the sibling device able to use them.
- The Manager surface and missing-credential guidance direct the user to connect the selected account on this device.

Related: #224, #139, #235.

## New issue: Coordinate shared berth allocation changes through NoteToSelf before route publication

Labels: type:task

Per #224, one participant's devices share one allocation and route per berth, and any currently trusted team device may rotate or repair that route.
NoteToSelf carries the shared allocation; the team announcement projects its finalized locator for peers.

Today `reconcile_team_route()` resolves the allocation locally, materializes it through the Hub, then signs and commits the route without publishing NoteToSelf first.
The current announcement selector picks the greatest UUIDv7 across valid sibling announcements, so it can silently select a route from an allocation that lost the NoteToSelf publication race.
The Core route reconciliation design record leaves sibling concurrency open.

Scope:

- Settle publication of the allocation, including any provider-issued final locator, through NoteToSelf before allowing its team-visible announcement.
  All provider I/O must remain behind the Hub.
- Preserve Cod Sync's typed publication outcomes.
  A refused or unresolved publication must not authorize an announcement; integration and any semantic conflict resolution remain explicit operations.
- Ensure the route describes the allocation currently selected by the coordinated flow.
  Successful publication of an earlier NoteToSelf commit is insufficient if a sibling has since superseded that allocation.
  Define how delayed or retried route publication avoids reverting the newer selection.
- Make the gap between the two publications retryable from existing state, including when a different trusted sibling performs the repair.
  Keep any previous valid announcement available until replacement, without claiming that its provider location remains reachable.
- Apply the rule to the existing Core route flow and define its use by the app-berth provisioning work in #139 and #235.
- Update Manager and Hub documentation to describe participant-owned shared allocations and distinguish local publication/commit from successful cloud publication.
  In particular, replace the Hub spec's claim that a device owns its write location and siblings may independently select different locations.

Existing evidence to reuse:

- `test_a_unique_index_violation_is_a_constraint_refusal` in `packages/small-sea-manager/tests/test_note_to_self_integration.py` already proves that two allocation rows for one berth are refused atomically during integration.
- The same file covers generic update/update semantic conflicts.
- `test_divergent_note_to_self_push_reports_integration_required` in `test_note_to_self_refresh.py` witnesses publication divergence between two installations.

Validation:

- Exercise concurrent allocation changes through Manager entry points on two devices sharing a common ancestor.
  The losing device must not sign or publish a route for its rejected allocation, and integration must surface the allocation conflict without silently choosing one.
- Inject an unresolved NoteToSelf publication outcome and verify that no candidate route is announced.
- Delay one device after its allocation publication, let a sibling publish a successor allocation and route, then resume the first device.
  Its delayed announcement must not replace the successor's route.
- Interrupt after successful NoteToSelf publication but before route publication, then retry from the same device and from another trusted device.
  Repair must announce the current finalized allocation without creating another allocation generation.
- Cover provider locator writeback so the announced locator matches the allocation published through NoteToSelf.
- Reuse the existing conflict micro tests, extending them only for missing allocation-specific behavior or Manager orchestration.
  Keep provider tests local with mocks or MinIO.

Related: #224, #208, #191, #139, #235.

## Update #139

Append to the scope:

- Device-local credential connection and disconnection for an existing shared cloud account are tracked in #CREDENTIALS.
  Do not repair missing credentials by requiring a replacement shared account or berth allocation.
- Generic app-berth provisioning must follow #224's shared-allocation ownership decision and the publication coordination implemented in #COORDINATION.

Add #CREDENTIALS, #COORDINATION, #224, and #235 to Related.
Leave #139 open for its remaining generic app-berth provisioning and repair scope.

## Update #235

Replace its #224 critical-path bullet with:

- #224 settles ownership: Core and Files each have one participant-owned allocation and route shared by the teammate's trusted devices.
  #COORDINATION implements allocation and route publication ordering, including sibling concurrency and interrupted publication.
  Apply that behavior to both berths in this capstone.

Under "Enroll local credentials for a synced cloud account", identify #CREDENTIALS as the implementation issue and retain the capstone's existing no-shortcuts requirements.

In "Completion and issue hygiene", remove #224 from the list of issues to close when the capstone passes, because its design decision is already completed.
Add:

- Close #CREDENTIALS and #COORDINATION only when their full stated contracts are satisfied, including credential disconnection and concurrency/failure witnesses beyond the capstone's happy path.
