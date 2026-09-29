# #235 capstone: gaps against main (d123c5a)

Read from the issue body, its four comments, and the code on main.
I did not run the tests.
Issue states come from `gh issue list`.

## Test shortcuts named in the issue

| Shortcut | Verdict | Evidence |
| --- | --- | --- |
| Device B credential row inserted directly in `test_note_to_self_refresh.py` | Gone | `_wire_device_b_credentials` (line 170) calls `list_cloud_storage` and `connect_cloud_storage_credentials`. It is a helper, not a SQL insert. |
| `_copy_team_baseline` in `test_linked_device_bootstrap.py` | Still present | Defined at line 40, called at lines 108, 227, 331, 374, 421, 476, 549, 627, 697, 796. `test_chaos_s12.py:26` imports it, and `test_sender_key_rotation.py:38` has its own copy. The capstone does not use it. |
| `LocalFolderStore` in `ssc-files/tests/test_sync.py` | Still present | About 30 uses, for example lines 50, 58, 148, 656. Those are component tests. The capstone does not use them. |
| `publish_storage_announcement_for_session` | Still present in Hub and Manager tests | Defined at `test_support.py:66`. Used in `test_cloud_api.py`, `test_notifications.py`, and `test_invitation.py:81`. The capstone does not use it. |
| #278 caveats: shared Hub backend, unsigned history, cold niche discovery | Resolved for the capstone | Two Hub backends, signed commits, and B discovers the niche from its own fetched registry (capstone step 11). |

`LocalFolderStore` also appears in `packages/small-sea-manager/small_sea_manager/provisioning.py:2085` (identity bootstrap `_store_from_descriptor`).
The capstone reaches identity bootstrap through the Hub, so it does not touch that path.

## Listed issues

| Issue | State | Note |
| --- | --- | --- |
| #180 | closed | Payload v2 landed. |
| #181 | closed | Capstone guards Files against opening Manager databases. |
| #190 | closed | Capstone signs Files commits and verifies each fetch through `/session/verify`. The baseline bundle is not yet history-verified (see #294). |
| #266 | closed | Files signs with the session workhorse key. |
| #224 | closed | |
| #237 | closed | Capstone step 7 uses `connect_cloud_storage_credentials`. |
| #238 | closed | |
| #139 | open | The app-berth slice landed. The repair UX and repair states in its body remain. |
| #226, #228 | open | Not used by the cold-start baseline. Capstone step 13 does call `fetch_teammate_core` and `integrate_core_sources`. |
| #263 | open | Finite-history rule. The capstone does not test it. |
| #48 | closed | Foundation. |
| #5 | closed | Audit already done. |
| #3, #6, #35, #36, #227, #150 | open | Left out on purpose. |

## What `tests/test_files_two_device_capstone.py` covers

- Steps 1 to 14, plus a step 15 that has each Hub verify its device's final Files history.
- Two roots, two independently loaded Hub apps and backends, one MinIO.
- A boto3 spy attributes every S3 call to a Hub. A `sqlite3.connect` guard, with a negative self-check, catches Files opening Manager databases.
- Four refusal cases: unsigned, wrong-berth key, missing authority, ambiguous authority.
- None of `LocalFolderStore`, `_copy_team_baseline`, `_wire_device_b_credentials`, or `publish_storage_announcement_for_session` appears in it.
- Documentation exists: `packages/ssc-files/Documentation/two-devices-one-participant.md`.

Shortcuts it still uses:

- Each Hub is an in-process ASGI client, not TCP.
- The signing key is read with `backend.signing_key(token)` and written to a file for `ssh-keygen`. The `wrong_berth` case also calls `backend.open_session`.
- `create_new_participant`, `redistribute_sender_key`, and `receive_sender_key_distribution` are `Provisioning` library calls, not `TeamManager` methods.
- Step 13 has A fetch its own teammate's Core (`fetch_teammate_core`) so A learns B's signing delegation.
- The cold-start baseline arrives as a git bundle in the bootstrap response. It is checked at the head only.
- No test of a changed authority view (needs #286).

## Findings from tonight's probes (task 99 and 100, #294)

- `test_core_peer_fetch_encrypted.py::test_admission_import_alone_does_not_let_the_inviter_read_the_invitee` (task 99) asserts that after the invitee imports the admission package, the invitee reconciles and yields a redistribution artifact, but the inviter's fetch of the invitee's chain still raises `PeerSenderKeyUnavailableError`.
  This bears on #235: sender-key redistribution is what lets A read B, and the capstone delivers it by calling `receive_sender_key_distribution` by hand.
  In the product the Hub runtime watch delivers it, and no test of #235 exercises that path.
- The next test in that file (task 100), `test_delivering_the_redistribution_artifact_lets_the_inviter_read_the_invitee`, is a scratch probe on main.
  Its docstring says `PLACEHOLDER`, it prints results, and it asserts nothing, despite a commit message that says it proves a result.
  It shows nothing yet about whether delivery makes the read work.
- `test_bootstrap_history_unverified.py` holds three strict xfails for #294: a bad ancestor beneath a good head is accepted by invitation acceptance, linked-device bootstrap, and identity bootstrap.
  Linked-device bootstrap and identity bootstrap are both on the #235 path.
  The #235 body says #190 must be honored on every import path, and these xfails show the two bootstrap imports do not do so.
- #294 also says NoteToSelf has no persistent trust anchor for device rows, so later NoteToSelf refresh (capstone step 8) is unverified.

## What #235 still needs, smallest first

1. Rewrite the #235 body. Its shortcut list is out of date: the credential insert is gone, Files is signed, and #180, #181, #190, #237, #238 are closed. Its "Files publications remain unsigned" comment is also out of date. No owner decision.
2. Turn task 100's scratch probe into a real assertion, or delete it. No owner decision.
3. Replace the capstone's direct `Provisioning` calls (participant creation, sender-key redistribution and receipt) with `TeamManager` methods, or state that library calls are acceptable. Owner decision on which.
4. Run the full suite, and search the capstone for the forbidden shortcuts (clean today). No owner decision.
5. Decide the fate of #139: close after splitting the repair UX into its own issue, or keep it open. Owner decision.
6. Record which part of #3 this slice completes. #5 is already closed. No owner decision.
7. Decide whether the cold-start baseline is the supported first slice, so #226 and #228 do not block. The documentation already says it is. Owner decision.
8. Decide whether TCP Hubs and Hub-side signing (no exported key) are required for completion. Owner decision.
9. Verify history in the bootstrap paths (#294, three xfails), and add a changed-authority-view case (#286). #294 needs an owner decision on the NoteToSelf trust anchor.
10. Test redistribution through the Hub runtime watch instead of by hand, which needs #228 or an equivalent. Owner decision on scope.
