# From a parked Core source to a merge of main (issue #228)

All paths are under `packages/small-sea-manager/small_sea_manager/` unless they start with `packages/`.
Citations were read at commit 97a7b34.
Nothing here was run; the ordering claim in the last section rests on reading code and one existing test.

## Summary

- No code path merges a parked Core head into `main`.
  The only path that gives a parked head any local effect is `integrate_core_events`, which copies signed `constitution_event` rows and never touches git history.
- That path already has a Manager entry point, `integrate_core_sources` (`manager.py:1828`).
  No web route, CLI command, or other production code calls it; only tests and one experiment do.
  The "fetch and park, never integrate" banner at `manager.py:1655` is out of date on this point.
- The issue's ordering constraint is real for the shipped probe flow but overstated as written.
  The invitee's own certificate can also reach the invitee through the admission package, which does not use the Core chain.

## Where parked refs come from

| Source | Written by | Ref |
|---|---|---|
| `teammate` | `fetch_teammate_core` (`manager.py:1658`), through Cod Sync `fetch(pin_to_ref=...)` (`manager.py:1691`) | `refs/small-sea/core-peer/<teammate>/latest` (`manager.py:75`, `78-84`) |
| `teammate`, divergent | `_record_core_divergence` (`manager.py:1731`) | `.../observations/<link_uid>` (`manager.py:87-89`) |
| `self_publication` | Cod Sync's publication settlement, `_park` (`packages/cod-sync/cod_sync/protocol.py:452`, `692`, `574`) | `refs/cod-sync/parked/<link_uid>` (`protocol.py:57`, `65`) |

A refused push (`PublicationIntegrationRequiredError`) is what creates a `self_publication` ref.
The web push route catches it and offers no action, because "Manager has no integration operation yet" (`web.py:794-797`).
`push_team` (`manager.py:1620`) lets the error propagate.

## Every entry point that touches parked refs

Reads and writes of the ref namespaces, outside tests:

1. `fetch_teammate_core` writes teammate refs (`manager.py:1658-1729`, `_record_core_divergence` at `1731`).
   It imports objects without a checkout and does not move `main` (docstring, `manager.py:1660-1677`).
   It passes `core_history_verifier` (`manager.py:1692`), so commit signatures are checked against the local trusted-key set when the head is fetched (`git_verification.py:38-56`).
2. `list_core_source_heads` reads both namespaces (`manager.py:1775-1826`).
   It computes `is_maximal` by strict descent within one source (`1810-1817`) and `contained_in_main` by ancestry (`1823`).
   It writes nothing.
3. `integrate_core_sources` (`manager.py:1828-1841`) loops over maximal heads of both kinds and calls `provisioning.integrate_core_events` for each.
   It does not check `contained_in_main` and does not build a verifier.
4. `integrate_core_events` (`provisioning.py:6784-6857`), described below.
5. NoteToSelf has its own parked-ref consumers (`note_to_self_sync.py:623`, `679`, `687`).
   They belong to #48 and are not part of the Core path.
6. `packages/ssc-files/ssc_files/files.py:688-723` and `sync.py:889` merge parked refs for Files berths.
   They are a precedent for a real merge over parked heads, not a Core entry point.

The only callers of `integrate_core_sources` are `packages/small-sea-manager/tests/test_chaos_s12.py:148`, `test_chaos_s13.py:41`, `test_chaos_s14.py:150`, `tests/test_files_two_device_capstone.py:403`, and `Experiments/files_two_homes/test_two_homes.py:193`.
`web.py` has an integrate route only for NoteToSelf (`web.py:583-589`).

## What `integrate_core_events` does, step by step

1. Opens the team repo through `core_signed_repo`, so any commit it makes is signed by this device's team key (`provisioning.py:6786-6788`; `git_signing.py:52-56`).
2. Reads `core.db` at the parked SHA into a temporary file with `repo.blob_at` (`6791`).
3. Opens it read-only and checks that `constitution_event` has `event_id`, `event_type`, `encoded` (`6793-6796`).
   A missing or unreadable file returns `refused / bad_source_db` (`6796`, `6804`).
4. Selects every `encoded` value and decodes each with `decode_event` (`6797-6807`).
   A decode failure returns `refused / bad_event` (`6809`).
5. Opens the live `core.db` in the work tree and starts `BEGIN IMMEDIATE` (`6811-6815`).
6. For each event calls `store_and_project` (`6819`; `constitution_projection.py:273-278`).
   That runs `check_event`, `add_event`, and `apply_event`, which verifies signatures, stores new events, and writes `key_certificate`, `teammate`, `team_device`, `integration_mode_change`, `workhorse_delegation` rows.
   An invalid event, conflicting event, or projection error rolls back and returns `refused` (`6820-6830`).
7. If no event was new, rolls back.
   If the live `core.db` differs from `HEAD`, it commits that difference as "Record uncommitted Core database" (`6831-6842`) and returns `no_change`.
8. Otherwise commits the transaction, stages `core.db`, and makes one ordinary one-parent commit "Integrated Constitution events from <sha>" on the current branch (`6843-6852`).
   If the commit fails it returns `integrated` with `code: git_record_failed` (`6849`).

Consequences for #228:

- The parked head never becomes an ancestor of `main`.
  The commit at step 8 has one parent, so `contained_in_main` stays false for that head forever (`manager.py:1823`).
  Re-running the operation re-reads the parked blob and finds every event `already_present`.
- The live `core.db` is edited in place under `BEGIN IMMEDIATE` and committed afterward.
  There is no candidate copy.
  A refusal in step 6 rolls back cleanly, but a valid source's row-level effects are applied directly to the live database.
- Only `constitution_event` and its projections move.
  The other tables in `inventory.md` (`teammate.display_name`, `app`, `team_app_berth`, `team_setting`, the four admission tables, `admission_revocation`, `device_prekey_bundle`) receive nothing from a peer through this path.
- No signature check on the parked commits happens here.
  Verification ran at fetch time for teammate refs.
  Nothing verifies `self_publication` refs, which Cod Sync parked from a store it does not attribute to a device (`manager.py:1799-1802`).
  `integrate_core_events` also does not check that the source chain descends from the local anchor.

## Merge driver and checkout: what exists

- The `splice-sqlite-merge` driver is configured on every team Core repo (`provisioning.py:2694`, `5589`, `5999`, `3190`).
- `Repo.merge` exists (`packages/cod-sync/cod_sync/repo.py:813-824`) and raises on unresolved conflicts (`repo.py:41`).
  No non-test code calls it on a Core repo.
- No candidate checkout exists for Core.
  `integrate_core_events` never checks out `main`, and I found no worktree helper on the Core path.
- The nearest precedent is NoteToSelf adoption (`note_to_self_sync.py:391-447`, `565-605`).
  It reads base and source blobs into a temp directory, applies row deltas to the live database, and then records either a fast-forward (`_record_fast_forward`, `565`) or a two-parent commit built from the live database's bytes (`_record_merge`, `587-597`).
  It never writes the source blob over the live file, because the Hub may hold the live database open (`note_to_self_sync.py:571-576`).
  The two-parent commit is what makes the source an ancestor of `main`.

## What a #228 implementation would have to add

The issue asks for a candidate checkout and a separate adoption step.
Measured against the code above:

1. **A merge, not only a union.**
   Decide, per table class in `inventory.md`, what a peer's change to a non-`constitution_event` table means.
   The characterization tests show the driver keeps ours on every conflict (`tests/test_core_merge_characterization.py`, for example `test_insert_with_same_key_and_different_content_keeps_ours`, `test_conflicting_updates_keep_ours_and_warn`, `test_two_endorsements_by_one_teammate_make_the_merge_fail`).
   That default is what #226 must replace or accept.
2. **A candidate.**
   Build the merged `core.db` outside the live work tree (a temp directory with base, ours, theirs blobs, as `note_to_self_sync.py:407-419` does), run the driver or a purpose-built merge there, and run admissibility checks on the result before touching live state.
3. **An adoption step.**
   Move `main` and the live `core.db` to the accepted result.
   Model it on `_record_merge`, so the parked head becomes a second parent, and on the write reservation that keeps the Hub away from the file (`note_to_self_sync.py:157`, `587`).
   The failure modes differ from candidate cleanup: adoption can leave the live database ahead of `main`, while candidate cleanup only leaves a temp directory.
4. **Verification of the source (#190).**
   `integrate_core_sources` passes parked heads to `integrate_core_events` without any verifier.
   For `self_publication` refs nothing has verified the commits at all.
   #228 must run `core_history_verifier(...).verify_history` (`git_verification.py:38`) on the head before use, or state why the fetch-time check suffices.
5. **Consume `contained_in_main` and supersession correctly.**
   Skip a head that `main` already contains.
   Today that never becomes true after integration (step 8 above).
6. **A caller.**
   No route or command reaches `integrate_core_sources`.
   #228 needs one, or it must say the operation stays test-only until #36 and #35 land.
7. **Typed outcomes.**
   `integrate_core_events` returns dicts with `outcome` and `code`.
   NoteToSelf returns typed `SourceOutcome` values (`note_to_self_sync.py:118-150`).
   Add typed outcomes only if #226's design needs them, as the issue says.

## The inviter-reads-invitee ordering claim

**Verdict: confirmed for the flow the #185 probes use; not confirmed as a statement about Small Sea in general.**

What is confirmed:

- The shipped probes only read invitee-reads-inviter, and say so (`tests/test_core_peer_fetch_encrypted.py:8-14`).
  The two probes use two teams with opposite invitation directions (`test_core_peer_fetch_encrypted.py:207-330`).
- The invitee gets the inviter's sender key inside the invitation token (`manager.py:897-899`; `provisioning.py:3226-3230` saves it as a peer key).
  The inviter's key for the invitee has no such carrier.
  It arrives only through `redistribute_sender_key` (`provisioning.py:4351`), which the invitee's Hub runs from `reconcile_runtime_state` (`server.py:243-262`) and the inviter's Hub receives through `_process_runtime_inbox_from_teammate` (`server.py:282-352`).
- `reconcile_runtime_state` returns early, with `local_device_trusted: False` and no artifacts, unless the local device's own key is among the trusted keys for the local teammate in the local `core.db` (`provisioning.py:4599-4611`).
  An invitee's cloned `core.db` predates the invitee's membership certificate.
  In the probe flow (`_invite`, `test_core_peer_fetch_encrypted.py:100-113`) the invitee never imports the admission package and never integrates the inviter's chain, so this check fails and no redistribution artifact exists.
- The receive side also requires the sender's device key to be trusted in the receiver's Core (`provisioning.py:4487-4497`).
  That holds for the inviter after finalization, which is why one direction works.

What the issue text overstates:

- It says the certificate must "travel back over the Core chain".
  The admission package also carries it.
  `export_admission_package` includes every `constitution_event` in the inviter's Core, including the invitee's membership certificate (`provisioning.py:6581-6636`).
  `import_admission_package` stores those events and commits (`provisioning.py:6688-6780`).
  `finalize_admission` and `complete_invitation_acceptance` both return the package token (`provisioning.py:6562-6570`, `7010-7013`).
  After that import, `reconcile_runtime_state` should find the invitee's own key trusted and produce artifacts.
- I did not run that sequence.
  It also needs the invitee to hold the inviter's device prekey bundle in `core.db`, or `redistribute_sender_key` skips the target (`provisioning.py:4398-4402`), and it needs a running Hub watch to fire the reconcile.
- The accurate statement is: the inviter cannot read the invitee until the invitee's Core contains the invitee's own certificate, and today that happens through the admission package or through Core integration.
  A probe that imports the package before the read would test whether the package route is enough.
  If it is, #228 is not the only way to make the reverse direction reachable, and the issue's validation section should say so.
