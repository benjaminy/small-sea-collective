# Autonomous exploration results

The six-hour continuation window ended at 2026-09-13 10:03:23 UTC.
The 10:25 UTC wake performed closeout only and paused the heartbeat.
Work was paced across checkpoints with a quota pause, not six hours of continuous model execution.
The earlier twelve-hour objective was not achieved.

All changes remain local on `issue-262-bootstrap-transcript`.
No GitHub state was changed, no past session logs were read, no branch ceremony was used, and no Fable model was used.
The working tree was clean at the start of closeout.

## Fixes and evidence

| Local commit | Result | Validation |
| --- | --- | --- |
| `e9e26a0` | Incomplete identity bootstrap stays blocked through Manager and Hub; fetched joining keys must match the local request. | 45 focused existing/experiment cases passed before the short review; all ten final experiment cases passed afterward. |
| `9677925` | A trusted team-device key authenticates the full stored prekey bundle and scope before encryption. | 51 combined bootstrap, team-creation, rotation and binding cases passed. |
| `5ea84c7` | Prekey consumption and received-key persistence commit together; validation precedes mutation and concurrent consumption losers are rejected. | 30 focused cases passed, including rollback before/after a changed record write, retry and two receiver threads. |
| `898070c` | The invitation draft creates its final commitment after newcomer keys exist and assigns authority interpretation to Manager/extension policy. | Short Opus review assessed; local links and diff whitespace checked. This is design text, not runtime wiring. |
| `243ea45` | The architecture now distinguishes simultaneous active-tip limits from historical concurrency width. | Model passed 614,400 exhaustive cases and 436,852 randomized trials in an hour-long local run. |
| `26703c3` | Preserved reproductions of shared prekey selection and interrupted receipt. | Three original observation cases passed; the interruption case was subsequently replaced with rollback/retry assertions. |

These suites overlap and ran at different checkpoints; their counts must not be added together or presented as a final full-suite total.
The broader baseline reached 348 passes and two local-port permission errors, not a clean full-suite result.
Focused Hub transport validation was rerun with loopback access and passed.
The DAG model does not test production enforcement or cryptographic correctness.

## Open risks

- The full fetched bootstrap snapshot is still not authenticated by the runtime ceremony, and the human comparison result is not recorded.
  The joining-key check closes one locally checkable inconsistency, not the whole trust loop.
- Senders sharing a published bundle still choose the same first one-time prekey.
  Atomic receipt makes the loser explicit but does not allocate distinct prekeys to concurrent senders.
- An authentic old prekey bundle can still pass the binding check.
  Freshness and replay policy remain separate work.
- Berth-scoped signing authority, explicit bootstrap anchors and removed-author acceptance remain design/implementation dependencies.
  Signed timestamps do not prove physical creation time.
- Two Git verification corner cases remain expected failures: quiet legacy grafts can hide ancestry, and signature-display configuration can contaminate the report.
  Those require local metadata/configuration control; they are not ordinary remote-bundle attacks.

## Suggested next work

Review the local commits as separate changes.
Then choose a small, explicit asynchronous prekey retry/allocation policy, with its forward-secrecy and availability costs stated.
Complete the bootstrap snapshot/ceremony design before treating the invitation transcript as enforced.
The final invitation ordering is a proposed local design choice, with alternatives and limitations recorded in the document and review assessment.

The experiment directories contain reproductions and reports.
`FINDINGS.md` records the evolving findings; this file is the closeout summary.
