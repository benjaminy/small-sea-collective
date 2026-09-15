# An exchange can bind bytes without establishing authority

This runnable probe uses real Ed25519 signatures and SHA-256 commitments to separate three questions:
who endorsed an exchange, whether the delivered bytes match it, and whether a projection matches the selected signed grants.
It is a small research model, not a Small Sea protocol implementation.

Run from the repository root:

```sh
.venv/bin/python Experiments/bootstrap_exchange/probe.py
```

The command asserts the expected outcome of ten scenarios and prints the actual verdicts as JSON.
It uses disposable generated keys and no network or persistent database.

## Prediction and observed results

| Scenario | Expected and observed result |
| --- | --- |
| Valid exchange | Projection agrees under the model's single-anchor policy. |
| Append whitespace to delivered JSON | B2 rejects even though parsed state is unchanged: the agreement binds exact bytes. |
| Introducer authenticates a false berth projection | B2 passes; B6 preserves the claimed `finances` berth and reconstructed `notes` berth and pauses. |
| Attacker replaces the whole self-consistent exchange | B1 rejects against the independently retained comparison commitment. |
| Same attack with the independent comparison disabled | The naive baseline accepts. This is the trust loop the experiment is meant to expose. |
| Authentic exchange includes an attacker-signed grant | B4 rejects against the independently pinned authority key. |
| Authentic snapshot omits an explicitly selected grant | B4 pauses and names the missing event ID. |
| No recorded matching comparison | B1 pauses. |
| Response belongs to another local attempt | B1 rejects despite the response's valid signature and comparison. |
| Honest-looking selected view omits an unmentioned grant | The newcomer still accepts the selected view; the experiment's separate possession of the omitted event demonstrates the limitation. |

The valid control checks the same delivery and reconstruction path as the attacks.
The naive baseline changes one condition: it skips the independently retained comparison, while keeping signature, digest and grant checks.
The false-projection case uses an authentic response over the actual false bytes; it cannot pass merely because the signature check was skipped.

## What is simplified

The comparison channel supplies a full digest of the completed signed response.
The model assumes that channel identifies the intended human and that a matched comparison is recorded correctly.
It does not implement or measure a short authentication string, human comparison errors, replay storage, or physical key custody.
A compared introducer key and an authority anchor are distinct fields; a matched exchange authenticates the choice of anchor, not its honesty.

The authority policy is deliberately fixed: one pinned key signs independent grants.
The frontier is a selected set of those grant IDs, not a general Constitution DAG.
There is no parent traversal, removal, quorum, historical authority, key rotation or identity delegation.
A missing selected grant models an explicit evidence dependency, not full ancestry verification.
A grant's berth and purpose remain in the reconstructed projection, but this probe does not wire a scoped runtime operation or release encryption keys.

The JSON encoder serves these generated fixtures only.
The parser is not hardened for untrusted wire input: it omits version enforcement, duplicate-key rejection, size limits and structural validation.
Do not use this code as an intake API.
Those omissions do not make the reported replacement attacks pass; they limit what the experiment can claim about other inputs.

## What changed our understanding

Binding exact bytes and reconstructing authority catch different failures.
A valid signature on a response does not repair a false projection, and a self-consistent collection of signatures does not supply its own independent anchor.
Conversely, reconstruction cannot discover an event whose existence was never independently established.
The omission control needs no cryptographic attack: the introducer simply offers less evidence.

The next useful implementation question is where the real bootstrap path can retain an authenticated commitment and comparison result before it adopts fetched state.
The unresolved identity-anchor policy remains separate; this model does not choose it for the project.
