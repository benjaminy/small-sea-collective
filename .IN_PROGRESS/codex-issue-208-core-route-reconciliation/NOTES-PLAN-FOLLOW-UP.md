# Notes

GitHub issue: #208, "Expose Core route publication and provider changes for established teammates."

Add one established-teammate operation that reconciles the device-local Core storage allocation through the Hub, rereads any provider-issued final locator, publishes a signed berth-scoped route, and commits it.
The operation repairs a missing route and supports intentional provider, account, or Manager-generated location changes without requiring an admission acceptance artifact.
Provider I/O stays behind the Hub.
Neither the Manager method nor the web or CLI surface accepts a caller-supplied locator string.

Peer delivery of the new announcement is separate sync work.
Receiver-side invitation-sidecar repair remains #202.
Copying existing stored data and handing peers from the old location to the new one are not part of this local route-publication operation.

## Design positions

These are the decisions the plan below assumes.
Each is falsifiable; if tracing contradicts one, stop and revise this section before implementing.

**P1. Allocation state and peer route announcements remain separate.**
`berth_cloud_allocation` is the device's current own-storage decision for a berth.
`teammate_berth_storage_announcement` is the append-only, signed peer-routing history.
An intentional allocation change updates the first before it appends to the second, so a failed materialization or publication does not delete the previous route announcement.
No pending or staging columns belong in the announcement table for this issue.

The selector orders announcements by descending `announcement_id`, not by insertion order.
The current UUIDv7 generator is random within a millisecond, so merely inserting the replacement last does not guarantee that it sorts last.
Publisher-side replacement must allocate an ID strictly greater than the effective announcement for that teammate and berth.
Rows the selector rejects do not bound publication.
This issue guarantees that ordering for serial publication on one device; cross-device publication ordering remains deferred.

**P2. An intentional allocation change creates a new allocation generation.**
`berth_cloud_allocation` continues to hold at most one row per berth.
Changing the selected account or generating a new location atomically replaces that row with a fresh allocation ID and `created_at` value.
Reconciliation without a requested change preserves the current row and allocation ID.

The fresh ID is a generation token, not history for its own sake.
The Hub currently conditionally writes a provider-issued locator using only allocation ID and expected location.
Its success and lost-race recovery must also prove that the allocation generation and cloud-storage selection are the ones it materialized.
A materialization started against a superseded allocation must return `cloud_allocation_conflict`; it must not write into or declare success for the replacement merely because the two providers use the same location string.
This applies to both `materialized` and `materialized_with_locator` outcomes.
The no-locator path must re-resolve and validate the original allocation identity before returning success instead of bypassing the generation check.

The Manager carries the resolved allocation ID and cloud-storage selection through the shared route pipeline as the expected generation.
After Hub setup it may sign only a reread row with that identity; a replacement observed before publication returns `allocation_conflict` and is not announced by the stale operation.
If a later replacement is visible in the final state read-back, the operation reports `allocation_conflict` rather than claiming that the route is ready.

This is local allocation/materialization correctness and belongs in #208.
It is distinct from the deferred case of two devices publishing routes concurrently.

**P3. Public callers select registered storage and may request a generated replacement location.**
The Manager operation is:

`reconcile_team_route(team_name, cloud_storage_id=None, new_location=False)`

- With an existing allocation and no change arguments, reconcile that allocation without replacing it.
- With a different registered `cloud_storage_id`, replace the allocation and generate a fresh provider-compatible location.
- With `new_location=True`, replace the allocation and generate a fresh location on the selected account, or on the current account when no ID is supplied.
- With no allocation, use the requested registered account; use the sole registered account when exactly one exists; report `storage_not_configured` when none exists; and report `storage_choice_required` when more than one exists and none was selected.
- Supplying the current `cloud_storage_id` without `new_location=True` is a no-op selection, not a location rotation.

There is deliberately no raw `location` parameter.
The web surface presents registered accounts, and the CLI exposes their IDs through a listing command.
The Hub still materializes the generated location, and Manager still rereads the durable final locator before signing.

A malformed or unregistered `cloud_storage_id` is invalid input, not route state.
The Manager raises `ValueError` before changing an allocation or contacting the Hub.
The web surface renders that input error, and the CLI presents it as a nonzero usage/input failure.
Typed retryable route outcomes continue the existing `prepare-route` convention: the command exits normally and writes the pending reason and retry guidance to stderr.

This makes the two route paths differ on purpose.
`prepare_team_route` keeps `_auto_allocate_berth_cloud_if_available`, which takes the first registered account by rowid without asking.
Silent selection is acceptable while an invitee is racing to publish a first route; it is not acceptable when an established teammate is deliberately choosing where their Core data lives.
Do not change the invitation path to match, and do not reuse the auto-allocate helper for the established operation.

**P4. A change request is one-shot; reconciliation of the resulting allocation is retryable.**
Replacing the allocation is durable before provider materialization begins.
If the operation fails after replacement, the current allocation is already the requested new generation while the old announcement remains available.
Retrying with no change arguments resumes that allocation without generating another location.
The web retry action must omit the original `new_location` intent, and CLI output must tell the caller to retry as `reconcile-route TEAM` without change flags.

Repeating `--new-location` is a new rotation request, not an idempotent retry.
Persisting an operation-intent token solely to make that exact command replay-idempotent would add workflow state that is not needed to answer the current research question.

The divergence is accepted for this research change, and it is wider than peer route selection alone.
`_require_own_storage_announcement` admits a Hub cloud read or write only when the current allocation matches either the effective selection or an announcement signed by the current device.
Between allocation replacement and successful publication neither holds, so this device's own Core uploads and downloads for that team fail with `announcement_missing` until a retry converges.
Materialization is not gated that way, so the retry path itself stays open.
Peers meanwhile keep selecting the old announcement and reading the old location.
Rollback, cleanup of abandoned provider locations, copying existing data, and peer handoff belong in focused follow-up work.

**P5. Share the route pipeline, not invitation policy or reporting.**
Refactor the common session/materialize/reread/publish/read-back/commit sequence into one private helper.
The helper consumes an already-resolved allocation and returns a route outcome without reading an acceptance artifact.

`prepare_team_route` keeps its existing acceptance-artifact gate, current-allocation behavior, join-state report, and export coupling.
`reconcile_team_route` resolves the allocation intent first and then calls the shared helper without an acceptance path.
Its route-ready shortcut is evaluated only after the requested allocation change has been applied, so an existing ready route cannot suppress an intentional provider or location change.

**P6. Established-route reporting is route-specific.**
Extract a small route report containing `route` and `route_reason`.
The established operation uses it directly.
The invitation `_report` extends it with join, admission, and acceptance fields and remains the only route path that calls `eligible_acceptance_artifact`.

Reuse these existing route reasons:

- `storage_not_configured`
- `hub_session_unavailable`
- `user_action_required`
- `materialization_failed`
- `allocation_conflict`
- `route_preparation_error`

Two reasons are new and belong only to the established operation:

- `storage_choice_required`
- `current_device_untrusted`

Both need entries in the `_ROUTE_HELP` tables in `cli.py` and `web.py`.
Those tables resolve unknown reasons to generic retry text, so omitting an entry degrades quietly instead of failing.

Publication and commit failures remain `route_preparation_error`, matching invitation preparation.
The logs retain the more specific diagnostic.

**P7. Repairing an announcement from an untrusted signer requires a different current trusted signer.**
Effective peer selection skips announcements whose signing key is no longer trusted.
An established device with a different current trusted key can append a replacement, and that is the repair case covered here.

If the current device key itself is not trusted, signing another row cannot repair peer selection.
The established operation reports `current_device_untrusted` without contacting the provider or publishing.
That check needs no new trust query.
`derive_team_join_state` already sets `admission` to `finalized` exactly when the current device public key is in the trusted set for this teammate, so the gate is `state["admission"] != "finalized"`.
The shared invitation helper does not impose this check because an invitee deliberately prepares its first route before admission finalizes.

**P8. “Ready” ends at local publication and commit, not data migration or peer delivery.**
Materialization proves that the Hub accepted the allocation and durably recorded any provider-issued final locator.
It does not prove that existing Core history has been copied to the replacement location or that peers have learned the new announcement.
The operation commits the new local announcement but does not push it, copy provider data, revoke the old location, or claim external reachability.

“Ready” is also narrower than “peers will pick this row.”
`derive_team_join_state` calls the route ready when this device holds a valid announcement matching the current allocation, disregarding trust and ordering.
Effective peer selection instead takes the highest-ordered announcement from any currently trusted device.
The two agree for the single-device repair cases in scope here, and can disagree once a second device publishes, which is the deferred concurrency item.

## Deferred

Named here so they remain visible and can be filed as focused issues rather than expanding #208.

- Concurrent route publication by two devices of the same teammate, including a durable ordering rule across clocks and replicas.
  Today this surfaces as a `route_preparation_error`: publication returns another trusted device's row unwritten, and the signer read-back check rejects it.
  That is an acceptable stop for now, not a bug to patch around in #208.
- Provider migration and handoff: populate the replacement location, deliver the new route through the old route or another channel, decide when the old location may be retired, and define rollback/orphan cleanup.
- Durable operation intents if exact replay of a failed `new_location` request must become idempotent.

# Plan

1. Lock down allocation-generation and Hub conflict semantics with failing micro tests.

   - Replacing an account or location produces a fresh allocation ID atomically and preserves the one-row-per-berth invariant.
   - Selecting the current account without `new_location` preserves the allocation ID and location.
   - A malformed or unregistered selected account fails before the current allocation is deleted or replaced.
   - A plain `materialized` result fails with `cloud_allocation_conflict` if the allocation was superseded while the provider call was in flight.
   - A stale materialization cannot write its final locator into a replacement allocation, including when old and new locations are textually equal.
     Textual equality is contrived: it needs a provider-issued locator to collide with a freshly generated `ss-` or `pending-` name.
     Let that case confirm the identity check rather than shape it.
   - Lost-race recovery succeeds only when the reread row is the same allocation generation and cloud-storage selection that was materialized.

   Verify: the new cases fail against the current in-place assumptions while the existing locator-writeback race tests remain meaningful.
2. Implement the narrow provisioning and Hub changes from P2 and P3.
   Add an allocation-intent helper next to `add_berth_cloud_allocation_by_berth_id`, leaving duplicate-add behavior unchanged.
   Validate a requested account inside the replacement transaction before mutating the current allocation.
   Strengthen both the no-locator success path and locator writeback/conflict recovery around allocation identity in `_handle_materialization_outcome` and `_writeback_locator`.
   `BerthCloudRecord` already carries `cloud_storage_id`, so comparing generations needs no new query.
   Keep the `_make_storage_adapter_from_record(self, ss_session, cloud)` signature: Manager and Hub tests patch that method at class level to inject materialization outcomes.
   Verify: step 1 is green, and the Manager remains the only component choosing or replacing an allocation.
3. Make serial route replacement ordering deterministic.
   Add the smallest validity-preserving UUIDv7-after-lower-bound helper needed by announcement publication rather than changing selector semantics.
   Bound it by the effective selected announcement, not by rows the selector skips.
   It belongs inside `_insert_teammate_berth_storage_announcement`, which mints the ID immediately before signing it.
   Do not apply it where a row arrives already signed: `announcement_id` is inside the canonical signed bytes, so renumbering an imported peer or sidecar row would invalidate its signature.
   Verify: with time frozen to the same millisecond, a replacement announcement sorts after and is selected over the previous valid row.
4. Extract the shared route pipeline per P5 and the route-only report per P6.
   Pass the expected allocation ID and cloud-storage selection into the helper, reject a mismatched post-materialization reread before signing, and translate a mismatch in the final state read-back to `allocation_conflict`.
   Apply those generation checks to both callers while leaving `prepare_team_route`'s acceptance gate, reporting, and non-racy behavior unchanged.
   Do not edit `packages/small-sea-manager/tests/test_invitation_route_delivery.py` merely to accommodate the refactor.
   Verify: that existing micro-test file passes unmodified, including provider-skip, publication-failure, and commit-retry cases.
5. Add `packages/small-sea-manager/tests/test_core_route_reconciliation.py` with focused failing cases:

   a. an established teammate with no acceptance artifact repairs a missing announcement;
   b. a creator who configured storage after team creation publishes a valid route;
   c. zero, one, and multiple registered-account selection produce `storage_not_configured`, automatic selection, and `storage_choice_required` respectively, while malformed and unregistered selected IDs fail before mutating an existing allocation;
   d. an intentional account change is not skipped merely because the old route is ready;
   e. an intentional location rotation gets a fresh allocation generation and publishes the locator reread after materialization, not the Manager-generated provisional value;
   f. publication failure after replacement leaves the old effective route selected and blocks this device's own Hub cloud reads and writes with `announcement_missing` per P4; retry without change arguments preserves the replacement allocation ID and location and converges;
   g. commit failure leaves the materialized announcement in the work tree; retry commits it without recontacting the provider;
   h. an old untrusted signer is repaired by the current trusted device that has not itself published, while an untrusted current device returns `current_device_untrusted` without provider or publication calls;
   i. repeated reconciliation with no requested change is a no-op for allocation and announcement identity;
   j. replacement after Hub setup but before Manager publication returns `allocation_conflict` and does not announce the unmaterialized replacement, while a replacement detected by the final state read-back is not reported ready.

   Verify: each case has a specific pre-implementation failure and all are green after the Manager operation lands.
6. Expose reachable CLI and web actions.

   - Add `cloud-storage`, a CLI command that lists registered storage IDs.
   - Add `reconcile-route TEAM [--cloud-storage-id ID] [--new-location]`.
   - Add a Core-storage section to the team detail UI with the current allocation, a registered-account selector, a “generate new location” control, and a reconcile button.
   - Add `POST /teams/{team_name}/reconcile-route` with only the registered account ID and boolean change intent as form inputs.
   - Render the route-only result in the team detail UI; do not reuse the invitation acceptance fragment.
   - Add the two new P6 reasons to the `_ROUTE_HELP` table in each surface.

   Verify: CLI tests prove ready and typed pending results exit zero, invalid selected IDs exit nonzero without changing the allocation, and stderr renders guidance for both new reasons; web tests prove the form is visible, submits its selection, renders each typed route outcome or input error, and offers a retry without replaying `new_location`.
7. Confirm Hub own-storage operations accept repaired and replaced allocations using the local harness in `packages/small-sea-hub/tests/test_cloud_api.py`.
   Verify: a write and read through the Hub succeed against the final allocation and location, with local mocks or MinIO and no direct provider I/O from Manager.
8. Run the repository checks that exist for this scope:

   - `uv lock --check`
   - `uv run pytest packages/small-sea-manager/tests`
   - `uv run pytest packages/small-sea-hub/tests`
   - `git diff --check origin/main`

   Review the diff against P1-P8, the issue scope, and the research-stage deferrals.
   Verify: every command is green, every changed line traces to a position or validation above, and invitation behavior remains unchanged.

# Follow-up

## Status

Plan steps 1-8 are implemented and green.
`uv lock --check`, `uv run pytest packages/small-sea-manager/tests` (240 passed), `uv run pytest packages/small-sea-hub/tests` (124 passed), and `git diff --check origin/main` all pass.
`packages/small-sea-manager/tests/test_invitation_route_delivery.py` passes unmodified.
Spec updates landed in `packages/small-sea-manager/spec.md` and `packages/small-sea-hub/spec.md`.

One deviation from P7: `current_device_untrusted` reports `route: pending` even when the local state derives the route as ready.
An announcement this device signs cannot repair peer selection, so reporting it ready would be false for the purpose of the operation.

## Deferred items to file

Searched the existing issue list; none of the three is already filed.

1. Concurrent route publication by two devices of the same teammate, including a durable ordering rule across clocks and replicas.
   Nothing open covers it.
2. Provider migration and handoff: populate the replacement location, deliver the new route, retire the old one, and define rollback and orphan cleanup.
   Overlaps #140 (orphaned provider location cleanup), #150 (cross-member announcement delivery), #185 (Manager Core peer fetch/integration), and #202.
3. Durable operation intents, if exact replay of a failed `--new-location` request must become idempotent.

Also worth a human decision: #139 ("Build Manager UX for berth cloud allocation and repair") is largely satisfied by the Core Storage section added here.
