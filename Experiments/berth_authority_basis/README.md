# Berth authority basis probe

A signature that verifies says who signed a commit.
It does not say whether that signer was allowed to write in this berth, for this purpose, under the Constitution view this device currently holds.
This probe separates those two questions with real SSH-signed Git commits and the real `SshCommitVerifier`.

It is a research probe.
It is not runtime policy, and the header names and record shapes below are experimental, not an approved interface.
It is a concrete input to [#266](https://github.com/benjaminy/small-sea-collective/issues/266); it does not close it.

## How to run

```
.venv/bin/python Experiments/berth_authority_basis/probe.py
```

Everything happens in a temporary directory that is deleted afterward: disposable SSH keys, disposable repositories, and `GIT_CONFIG_GLOBAL=/dev/null` so no machine Git configuration changes.

## What it builds

One Git repository standing in for berth A, and three disposable ed25519 keys:

- `A_old`: signed in berth A, later removed.
- `A_new`: signs in berth A now.
- `B`: signs in berth B, and never had authority in berth A.

Commits, each signed for real, each carrying an experimental trailer that claims a berth and a Constitution basis digest (`X-Experimental-Berth`, `X-Experimental-Basis`):

| commit | signer | claim |
| --- | --- | --- |
| `old` | `A_old` | berth A, basis 1; made while `A_old` still had authority |
| `current` | `A_new` | berth A, basis 2 |
| `no_basis` | `A_new` | berth A, no basis trailer |
| `unknown_basis` | `A_new` | berth A, an unavailable basis |
| `ambiguous_basis` | `A_new` | berth A, two different basis trailers |
| `backdated` | `A_old` | berth A, basis 1, author and committer dates set before the removal, but written after it |
| `cross_berth` | `B` | berth A, basis 2 |
| `tampered` | — | `current`'s signed object with one byte of the message changed |

The local authority records — which key holds which berth and purpose, which key was removed, and which exact commits a human already accepted — are **written into the probe as explicit simulated inputs**.
They are not derived from the fetched signer list, and nothing here reads a global current view or a real-world clock.

## The three judgments compared

1. **Historical union.** Accept any commit signed by any key that ever signed for this berth.
2. **Current only.** Accept only commits signed by a key holding berth-A authority now.
3. **Bounded contextual.** Accept a commit if it is in the finite, exact commit-and-signer acceptance set recorded by a human; otherwise judge it under current local berth and purpose policy, and pause when the basis claim is missing or unjudgeable.

## Predictions, written before running

- The independent check (`SshCommitVerifier` with all three public keys) reports `G` and a signing fingerprint for all seven real commits, including `backdated` and `cross_berth`. Cryptography does not distinguish them.
- `tampered` fails that same verifier with `SignatureInvalidError`, not with a policy answer.
- `old`: union accepts, current-only **rejects** a valid ancestor a human already accepted, bounded accepts via the acceptance record.
- `backdated`: union **accepts new work by a removed key**, current-only rejects, bounded rejects. The cited basis and the early dates are attacker-chosen signed bytes; they do not prove the commit existed before the removal.
- `cross_berth`: all three signature checks pass, but bounded rejects for berth scope. A valid signature from another berth implies nothing here.
- `no_basis`: bounded returns a *pause* distinct from both "rejected" and "bad signature".
- The initial view-change fixture reassigned key B to A. The orchestrator rejected that fixture because berth keys must remain distinct.
  The revised case removes `A_new` within A: `current` changes from accepted to rejected while a fresh verification returns the same fingerprint.
  The signed evidence did not change; the local decision did.

## Limitations

- The removal record, the basis digest, and the berth claim are simulated. #263 still owes real signed removal evidence, and no Constitution document is hashed here.
- A commit trailer is a claim by the signer about itself. Binding a basis this way proves the signer said it, nothing more.
- Only one repository, one linear-ish history, and hand-written records. This is not a general event-DAG authority policy and should not be read as one.

## Observed results and additional checks

The orchestrator reproduced the probe with Git 2.54.0 (Apple Git-157).
The seven valid commit signatures verify, including the backdated, cross-berth, missing-basis and ambiguous-basis commits.
Tampering with the signed message produces `SignatureInvalidError`, separately from all policy results.

The probe now exercises both naive key sets through the actual SshCommitVerifier as well as through the policy table.
The historical union accepts the backdated commit; the current-only verifier rejects the current head because its old ancestor uses the removed key.
The finite acceptance record names the signer, berth, purpose, deciding device and decision view; copying it to another berth does not authorize that berth.
Withdrawing the acceptance makes the old work require a new local decision, so acceptance is revisitable.
Missing, unavailable and multiply claimed basis inputs pause separately from invalid signatures.

A signed basis citation identifies what the signer claimed, not when the commit was created.
The context checks are simulated local policy; they do not implement the Constitution evaluator or claim a globally current authority view.
The next #266 design step is to define the actual signed work-to-basis binding and the local policy evidence it consumes, without reducing those inputs to one timeless key set.

## Independent review limits

A second reviewer reproduced the actual Git results and identified an overstatement in the contextual verdict.
The contextual policy checks a handwritten grant mapping and membership in a set of basis labels; it does not evaluate authority from a Constitution view.
The verdict now says exactly that.
`decision_view` is retained as simulated provenance, not checked as an authority proof or required to equal today's view.
The experiment provides actual commit-to-signature evidence and simulated local acceptance decisions, not actual Constitution-derived authorization.
The per-commit policy table also differs from the full-history current-only verifier: the table permits the current commit, but the actual verifier rejects that head because its old ancestor is unrecognized.
