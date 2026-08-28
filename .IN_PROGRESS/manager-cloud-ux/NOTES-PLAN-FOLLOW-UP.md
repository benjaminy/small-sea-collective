# Notes

This branch addresses GitHub issue #139, Manager UX for berth cloud allocation and repair.

Issue #208 already delivered the main Core-route reconciliation mechanism on `main`.
The Manager can select a registered cloud account, create or replace the Core berth allocation, ask the Hub to materialize it, reread a provider-issued final location, publish the signed route, and retry typed failures.
Its Manager-generated locator path, exposed as "Generate new location," satisfies #139's provider-facing-location requirement without accepting arbitrary locator text from the caller.
This branch should build on that operation rather than introduce a second allocation or publication path.

The remaining user-visible failure is clearest in the team push flow.
A valid Hub session and green team status can coexist with missing storage, while "Push to cloud" renders the Hub's typed error as raw JSON.
The Manager should present session authorization and current Core-route state as distinct state, and direct typed storage failures to an action that can repair them.

Unless implementation evidence requires otherwise, this branch treats #139 as Core-berth UX.
Generalizing allocation controls to arbitrary app berths would expand the current Manager surface and should be a separate design decision.
Provider onboarding beyond the existing locally testable S3/MinIO path, including Dropbox OAuth from #10, is not a prerequisite.
Treat #139's provider-account bullet as an ownership constraint for this branch: preserve the existing Manager-side S3/MinIO account setup and removal UX, and do not move account management into the Hub.
Do not invent outbound provider-console links because the current configuration does not model a management URL; provider-specific onboarding and management links remain with #10.

The linked-device allocation and signer-identity question recorded on #139 is out of scope for this branch.
File a focused follow-up issue because the ownership model is unresolved, and do not change `finalize_linked_device_bootstrap` here.
That follow-up must decide whether linked devices share the member's existing berth allocation and announcement or can own a distinct location before publishing another route or reviving `_bucket_name_for_protocol` as routing authority.

# Plan

1. Done. Established the current behavior and remaining scope.
   Trace team detail, the sidebar session indicator, push error handling, Core allocation status, and route reconciliation.
   Identify the existing partial witnesses: `test_a_creator_who_configures_storage_late_publishes_a_route` covers late local reconciliation with `_FakeSession`, while `_HubEnv` in `test_publish_core_state.py` covers MinIO-backed push after creating the allocation directly.
   Neither covers the complete repair-to-push sequence.
   Verify that reconciliation repairs the missing allocation and reproduce push's unhelpful raw error before changing UI behavior.

2. Done. Defined the smallest coherent Manager presentation.
   Specify which local and Hub-derived states drive session authorization, allocation status, route status, and storage repair guidance.
   Keep those signals separate so a valid session never implies that storage is ready.
   Keep `storage_not_configured` for absent local storage configuration, map `cloud_location_missing` to a distinct `location_missing` route reason, and map `cloud_credentials_missing` to a distinct `credentials_missing` route reason.
   Intentionally update `test_each_cloud_setup_failure_leaves_a_retryable_pending_route` in `test_invitation_route_delivery.py`, whose current parametrization collapses both failures into `storage_not_configured`.
   Give each route reason an accurate repair action in both the web UI and CLI instead of collapsing missing credentials into "Add cloud storage."

3. Done. Implemented the issue #139 remainder.
   Route typed push failures to clear Manager guidance and the existing reconciliation action.
   Push does not currently use `_ROUTE_REASON_BY_CLOUD_REASON`, so explicitly catch `SmallSeaCloudStorageRequired` on that path, translate its typed reason through shared cloud-to-route mapping, and render the corresponding repair guidance instead of raw Hub JSON.
   Adjust the team detail, Core storage, and sidebar fragments only as needed to make allocation state and recovery discoverable.
   Keep the sidebar indicator session-only, make that meaning explicit, and present current Core-route state as a separate signal rather than aggregate team health.
   Reuse `reconcile_team_route` and Hub `POST /cloud/setup`; verify #208's existing Manager-generated-location path rather than adding caller-supplied locations or direct provider I/O.
   Do not change linked-device bootstrap behavior.

4. Done. Validated the behavior skeptically.
   Add micro tests for any changed Manager state or error-mapping helpers independently of template rendering.
   Add UI or integration coverage for creating a team without storage, configuring MinIO later, selecting that account, creating and materializing the allocation, publishing the route, and then pushing successfully.
   Cover each typed reason in #139: `cloud_location_missing`, `cloud_credentials_missing`, `cloud_user_action_required`, `cloud_materialization_failed`, and `cloud_allocation_conflict`, including distinct web and CLI guidance for missing location and missing credentials.
   Verify the push response no longer exposes raw Hub JSON.
   Verify session-active and current-route-pending render as simultaneous, distinct facts, and that the sidebar indicator is explicitly a session signal.
   Run the focused Manager and Hub micro tests, then the repository's proportionate broader checks.

5. Done. Reconciled documentation and issue tracking.
   Update #139's scope or completion notes to distinguish work delivered by #208 from this branch.
   File a focused issue for linked-device allocation ownership, signer identity, and route publication without changing `finalize_linked_device_bootstrap` in this branch.
   Record only durable branch-level decisions in `DESIGN-RECORD.md` near completion.
   Draft the final condensed commit message after implementation and validation settle.

# Follow-up

- Resolve #224, which now tracks linked-device allocation ownership, signer authorization, and route-selection semantics.
- The scope split between #208 and this branch is recorded in the completion comment on #139.
- Keep #150 as downstream cross-member delivery validation; it does not block this branch.
- Do not absorb Dropbox OAuth or broader provider onboarding from #10 into this branch.

# Status

Implementation and validation are complete.

Reproduced first: with an active session and a missing allocation, `POST /teams/{team}/push` rendered
`{"error":"cloud_storage_required","reason":"cloud_location_missing"}` verbatim.

Landed:

- `ROUTE_REASON_BY_CLOUD_REASON` is public, and splits the Hub's two storage preconditions into
   `location_missing` and `credentials_missing`.
   `storage_not_configured` now means only "no account registered."
- Web and CLI guidance distinguishes the two; only reasons reconciliation can repair without new
   user input offer a no-change Retry.
- Push catches `SmallSeaCloudStorageRequired`, names the route reason, and hands the repair to the
   existing `reconcile-route` operation.
- The Core Storage section carries persistent current-route state; the per-team session badge and the
   sidebar dot are labelled as session-only.
- `packages/small-sea-manager/spec.md` records the split reasons, the push translation, and the
   session/current-route separation.

Validation: `tests/test_core_storage_ux.py` (21 tests) covers the mapping independently of rendering,
every #139 typed reason through push, the two split reasons in web and CLI, simultaneous
session-active/current-route-pending rendering, and a MinIO witness for the full
create-team-without-storage → configure → select → reconcile → push sequence.
`test_each_cloud_setup_failure_leaves_a_retryable_pending_route` was updated for the split, as planned.

No linked-device bootstrap behavior was changed.

GitHub follow-up completed:

- Filed #224, `Decide linked-device Core allocation ownership and signer identity`.
- Posted the #208/`manager-cloud-ux` delivery split and deliberate exclusions on #139.
