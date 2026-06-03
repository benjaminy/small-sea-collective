# Issue 138 Plan: Remove Legacy `team_device` Transport Fallback

GitHub issue: https://github.com/benjaminy/small-sea-collective/issues/138

Branch: `issue-138-remove-team-device-transport-fallback`

## Goal

Stop using `team_device(protocol, url, bucket)` as peer storage routing data.
After this branch, peer-readable storage for a berth should be discovered through signed `member_berth_storage_announcement` rows scoped to `(member_id, berth_id)`.
`team_device` should remain about device identity and any still-valid device-key responsibilities, not cloud storage placement.

## Current Understanding

Issue #134 settled the Manager-owned berth cloud-location model.
Issues #136 and #137 implemented berth cloud allocation and the `member_berth_storage_announcement` read/publish primitives.
Issues #144 and #145 hardened and cleaned up test support around that announcement path.

The remaining transitional path lives in three places:

- `small_sea_hub.backend` can load `team_device(protocol, url, bucket)` through `_legacy_transport_for_member`.
- `wrasse_trust.transport` lets both member transport and member-berth storage selection return status `legacy-fallback`.
- `small_sea_manager.provisioning` still has helpers and admission/bootstrap flows that can write or read transport fields on `team_device`.

Specs also still describe legacy fallback as temporary.
Those docs need to be brought back into agreement with the code.

### Key finding from code inspection (read before implementing)

The replacement path is only half-live, which changes the shape of this branch:

- **Production never publishes berth storage announcements.**
  `publish_member_berth_storage_announcement` (`provisioning.py:3352`, exposed at `manager.py:372`) is called **only from tests**.
  There is no web route and no automatic provisioning call.
  Three production flows allocate Core berth cloud storage, and they handle it three different (all wrong) ways:
  - `create_team` (`provisioning.py:4377`) writes the allocation into the **legacy `team_device` fields** via `_upsert_team_device_row(protocol=…, url=…, bucket=…)`.
  - the device-link/bootstrap path (`provisioning.py:2867`) does the same.
  - `accept_invitation` (`provisioning.py:4780`) calls `_auto_allocate_berth_cloud_if_available` but discards the result — it writes **neither** `team_device` fields **nor** an announcement, so the invitee's self storage is not discoverable through *any* channel today, not even the legacy fallback.
  So today the legacy `team_device` fallback is the only peer-storage discovery channel that works at all, and it only covers the creator/device-link cases; the Hub read side (`member_berth_storage_announcement`) is live, but nothing populates it outside tests.
  Removing the fallback without wiring production publishing into all three flows will break real peer discovery — the invitee path is already the weakest.
  This is why Step 4 is a build step, not a verification step.

- **The Hub-side member-level (non-berth) transport path is dead code.**
  `_effective_peer_transport` (`backend.py:1896`) has zero callers; its non-berth branch in `_effective_peer_transport_selection` is unreachable because `_download_peer_file` always passes `berth_id` (`backend.py:1573`).
  This can be deleted wholesale rather than reasoned about.

- **`member_transport_announcement` is NOT dead — it backs a live Manager web feature.**
  The `announce_transport` endpoint (`web.py:594` → `manager.py:361` → `provisioning.py:3285`) and the members listing (`_effective_transports_by_member` at `provisioning.py:5694` → `members.html`) use it.
  Issue #138 is about the `team_device` legacy fallback, not about retiring signed member-transport announcements.
  Do not delete that feature in this branch.

## Non-Goals

- Do not redesign Manager cloud-location provisioning (multi-provider selection, location policy).
  That belongs to #139 and nearby follow-up work.
  In scope, however, is the minimal "publish a `member_berth_storage_announcement` for the berth that was just allocated" wiring at every flow that produces a Core berth allocation (create_team, device-link/bootstrap, and accept_invitation).
  That wiring **is** the "replacement publishing" the issue names ("stop writing transport fields onto `team_device` … once replacement publishing exists"), and without it the fallback cannot be removed safely.
- Do not add compatibility shims for old pre-alpha databases unless a specific local test fixture requires a narrow cleanup.
- Do not remove team-device identity rows, team-device keys, or certificate trust checks.
  Only remove storage-routing semantics from `team_device`.
- Do not implement orphaned provider-location cleanup.
  That belongs to #140.

## Implementation Plan

1. Map the fallback surface.

   Search for `legacy-fallback`, `member_transport_announcement`, `team_device(protocol`, direct `team_device` selects of `protocol`, `url`, or `bucket`, and `_upsert_team_device_row` calls that pass cloud fields.
   Classify each occurrence as one of:

   - peer storage resolution behavior to remove,
   - admission/bootstrap writing behavior to stop,
   - schema/documentation cleanup,
   - test setup that should be rewritten around member-berth storage announcements,
   - unrelated identity use that should stay.

2. Remove fallback from Hub peer cloud-file resolution.

   Update berth-scoped peer resolution so `member_berth_storage_announcement` is the only accepted peer storage source.
   Delete `_legacy_transport_for_member` and stop passing `legacy_fallback` into `_select_member_berth_storage` / `_effective_peer_transport_selection`.
   Because the Hub member-level (non-berth) path is dead (see Key finding), also delete `_effective_peer_transport` (`backend.py:1896`), the non-berth branch of `_effective_peer_transport_selection`, the Hub's `_load_member_transport_announcements`, and the now-unused `MemberTransportAnnouncement` import.
   This reduces Hub peer resolution to berth-storage-only.
   Make missing or invalid announcements produce the existing missing-storage behavior rather than silently routing through `team_device`.

3. Remove fallback from shared transport selection helpers.

   Remove the `legacy_fallback` parameter and the `legacy-fallback` status branch from **both** `select_effective_member_berth_storage` and `select_effective_member_transport` in `wrasse_trust.transport`.
   Do **not** remove `select_effective_member_transport`, `MemberTransportAnnouncement`, or the `member_transport_announcement` table itself — they back a live Manager web feature (`announce_transport` + members listing) and are out of scope for #138.
   The only behavioral change here is that a member with no valid signed announcement now resolves to `missing` instead of `legacy-fallback`.
   On the Manager side, drop `_legacy_transport_by_member` (`provisioning.py:3170`) and stop passing `legacy_fallback` from `_effective_transports_by_member` (`provisioning.py:3204`); members with no signed announcement will simply display `transport_status: missing`.
   Whether member-level transport announcements should remain a concept at all is a separate question — fold it into the #123 grooming in Step 7, not into a deletion here.

4. Wire production publishing, then stop writing transport fields onto `team_device`.

   This is a build step, not a verification step (see Key finding): production currently has no path that publishes `member_berth_storage_announcement`, so the order matters.

   1. Publish a signed `member_berth_storage_announcement` for the just-allocated Core berth in **all three** allocation-producing flows, instead of writing `protocol`/`url`/`bucket`:
      - `create_team` (`provisioning.py:4377`),
      - the device-link/bootstrap path (`provisioning.py:2867`),
      - `accept_invitation` (`provisioning.py:4780`, which today discards the allocation and publishes nothing — capture the allocation record and publish from it).
      Keep this minimal: publish the announcement for the berth that was just allocated. Larger provisioning redesign stays in #139.
   1a. **Transaction-safe mechanics (do not just reuse `publish_member_berth_storage_announcement` as-is).**
      That helper opens its own `_sqlite_engine` + `engine.begin()` (`provisioning.py:3375-3377`), but all three call sites are already inside an open write transaction on the same `core.db` (e.g. `create_team`'s `with team_engine.begin() as conn` at `provisioning.py:4357`), so nesting it risks `database is locked` and may also miss the git stage/commit of the row.
      Factor out an in-transaction core that takes the live `conn` (sign + INSERT only), have the public helper wrap it with its own engine, and call the in-transaction core from the provisioning flows before they stage/commit `core.db`.
   2. Then simplify `_upsert_team_device_row` so callers cannot store `protocol`, `url`, or `bucket` (drop those keyword parameters and the columns from its INSERT/UPDATE — see Step 5 for the schema coupling).
   3. Remove cloud-storage lookups in device-link or bootstrap flows whose only remaining purpose was to populate those fields.
   4. Only after (1) is in place, prove (not assume) that admission and bootstrap leave peers able to discover storage — see the end-to-end micro test in the Validation Plan.

5. Reconcile schema and migrations.

   Because the project is pre-alpha, prefer the clean schema if tests and current DB initialization allow it.
   Remove `protocol`, `url`, and `bucket` from the `team_device` table definition if they are no longer used. The active schema surface is **two** places, not just the packaged SQL file — clean both:
   - `sql/core_other_team.sql:86-92`, and
   - the inline `CREATE TABLE IF NOT EXISTS team_device (…)` in `provisioning.py:2297`, plus the raw `INSERT OR REPLACE INTO team_device (… protocol, url, bucket …)` migration/import path at `provisioning.py:2362`.
   This is coupled to Step 4: dropping the columns requires updating `_upsert_team_device_row`'s INSERT/UPDATE column lists (`provisioning.py:1114-1135`) and the raw INSERT at `2362` in the same commit, or inserts break.
   It will also break raw-SQL tests that read/write those columns directly — update them in Step 6: `test_session_flow.py:381` (INSERT), `test_create_team.py:252` (SELECT), `test_sender_key_rotation.py:196` (INSERT), plus the `_upsert_team_device_row` callers in `test_manager.py:145`, `test_runtime_watch.py:136`, `test_admission_proposals.py:58`, and `test_linked_device_bootstrap.py:165`.
   If migration cleanup would create noisy or risky churn, leave old columns tolerated but prove no code reads or writes them for routing.
   Record the final choice in the design record.

6. Update tests and docs.

   Rewrite tests that expected `team_device` fallback routing so they expect missing storage unless a valid member-berth storage announcement exists.
   Update admission/bootstrap tests to prove peers can still discover storage after the replacement announcement flow.
   Rewrite `test_member_transport.py` assertions that expect `transport_status == "legacy-fallback"` (lines 54, 133) to expect `announced` or `missing`.
   Remove the now-dead `legacy-fallback` branch in `templates/fragments/members.html` (lines 45-48).
   Update `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md` (legacy-fallback prose at manager spec 621-626, 858; hub spec 283), and any related prose so there is no documented storage-routing authority on `team_device`.

7. Revisit #123.

   Issue #123 asks whether `member_transport_announcement.bucket` should be authoritative for S3 berth routing.
   This branch should either close it as superseded by berth-scoped storage announcements or rewrite it into any remaining non-berth transport question.
   Add that recommendation to `FOLLOW-UP.md` if the GitHub issue itself is not updated during the branch.

## Validation Plan

The skeptical-reviewer standard for this branch is that tests fail if legacy `team_device` storage fields can still affect peer storage routing.

### Targeted Micro Tests

- Add or update a Hub peer cloud-file micro test that inserts only `team_device(protocol, url, bucket)` for a peer and no valid `member_berth_storage_announcement`.
  The test should prove the Hub refuses or reports missing peer storage instead of fetching from the legacy endpoint.
- Add or update a Hub peer cloud-file micro test with both stale `team_device` fields and a valid member-berth storage announcement.
  The test should prove the announcement wins because it is the only routing source, not merely because it has precedence.
- Add or update a Manager provisioning micro test proving admission/bootstrap no longer writes `protocol`, `url`, or `bucket` to new `team_device` rows.
- Add or update an admission/bootstrap integration micro test proving a newly accepted peer can still discover readable berth storage when the proper member-berth storage announcement is present.
- **(Decisive test for this branch.)** Add an end-to-end micro test that runs the **real** `create_team` + invite/accept flow with **no manual `publish_member_berth_storage_announcement` call**, and proves for **both roles** — the creator (Alice) and the invitee/acceptor (Bob) — that: (a) a peer can resolve/download that member's berth storage, and (b) no `protocol`/`url`/`bucket` was written to any `team_device` row.
  Both roles are required: a test that only checks Alice's creator announcement can pass while Bob's accept/push path (the path that publishes nothing today, `provisioning.py:4780`) stays broken.
  This is the actual proof that Step 4's production publishing wiring works; the existing `test_peer_transport.py` cases publish announcements manually and therefore only exercise the read side.
- Add or update a `wrasse_trust` micro test proving `select_effective_member_berth_storage` returns `missing` when no valid berth-scoped announcement exists, even if a caller tries to supply old fallback-shaped data.

### Regression Test Runs

Run the focused tests first:

```sh
uv run pytest packages/wrasse-trust/tests/test_transport.py
uv run pytest packages/small-sea-hub/tests/test_peer_transport.py
uv run pytest packages/small-sea-hub/tests/test_cloud_api.py
uv run pytest packages/small-sea-manager/tests/test_member_transport.py
uv run pytest packages/small-sea-manager/tests/test_invitation.py
uv run pytest packages/small-sea-manager/tests/test_hub_invitation_flow.py
```

Then run broader package tests if the focused suite passes:

```sh
uv run pytest packages/wrasse-trust/tests
uv run pytest packages/small-sea-hub/tests
uv run pytest packages/small-sea-manager/tests
uv run pytest packages/shared-file-vault/tests
```

### Static Checks

- `rg -n "legacy-fallback|legacy_fallback|_legacy_transport" packages`
  should show no remaining references in production code.
- The reliable signals that storage routing no longer rides on `team_device`: the `_upsert_team_device_row` signature no longer accepts `protocol`/`url`/`bucket`, and there are no `SELECT … protocol … url … bucket … FROM team_device` reads for routing.
  Note: `rg "protocol, url, bucket"` will also match legitimate **caller-supplied** coordinates that are not `team_device` routing — `proxy_cloud_file`/`bootstrap_cloud_file` (`backend.py:1487`, `1530`), `server.py:1026`, and `cod_sync/protocol.py:800-807`. Treat those as expected, not as violations.
- `rg -n "team_device" packages/small-sea-manager/spec.md packages/small-sea-hub/spec.md`
  should show identity-focused language only, with no storage-routing fallback claim.
- Any remaining `member_transport_announcement` references are expected — they belong to the live member-transport feature, which this branch keeps (see Step 3 / #123 in Step 7).

### Integrity Argument

This change should reduce coupling between identity and storage placement.
The Hub remains the gateway for all Small Sea internet traffic, but it no longer invents or recovers peer storage routing from identity rows.
The Manager remains the owner of berth cloud-location provisioning and announcement publishing.
Other packages consume berth-scoped announcements rather than reaching into Manager-only storage state for routing.

## Risks And Watch Points

- Some tests may still be using `team_device` fields as convenient setup.
  Prefer rewriting them through allocation plus `publish_member_berth_storage_announcement`, not preserving the fallback.
- `member_transport_announcement` is **not** obsolete: it backs a live Manager web feature (`announce_transport` + members listing). Do not remove it as part of this branch.
  The Hub-side member-level path, by contrast, is dead and should be removed (Step 2).
  Record any remaining uncertainty about the long-term role of member-level transport announcements in `FOLLOW-UP.md` and tie it to #123.
- The biggest correctness risk is removing the fallback before production publishing is wired (Step 4).
  Sequence Step 4.1 (wire publishing) before Step 2/Step 4.2 (remove fallback / stop writing), and gate on the decisive end-to-end test, or real peer discovery silently regresses to `missing`.
- Schema cleanup can snowball.
  Keep the branch focused on behavior first, then schema/docs once behavior is proven.
- Peer routing errors should stay legible.
  Removing fallback should not turn missing storage into a vague internal failure.

## Completion Artifacts

At wrap-up, create:

- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/FOLLOW-UP.md` if #123 or related cleanup cannot be fully resolved in this branch.
- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/design-record-issue-138-remove-team-device-transport-fallback.md`
- `.IN_PROGRESS/issue-138-remove-team-device-transport-fallback/review-note.md`
