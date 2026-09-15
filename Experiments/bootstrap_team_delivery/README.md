# Deliver team evidence to an empty sibling device

This experiment compares a fresh per-team human comparison with a signed exchange through an already recognized identity-device relationship.
Both deliver the same selected team evidence into an initially empty store.
Neither reads a team key from a preinstalled baseline or treats an unsigned NoteToSelf row as authority.

## Predictions before execution

Both valid paths should retain the authenticated response before fetching, verify the offered team evidence, and adopt only the explicitly selected team.
Changing the team, attempt, fresh key, baseline bytes, identity-to-team binding, or response signature should prevent adoption.
An unavailable pinned snapshot should leave the commitment available for a later retry.
An identity-linked sibling without a team enrollment grant should authenticate delivery but fail to authorize enrollment.
An authenticated false projection should preserve both claimed and reconstructed authority.
An attacker replacing the entire exchange should fail independent authentication; a deliberately weakened baseline should accept that same substitute.
A correctly authenticated malicious sibling with valid team authority can still offer an incomplete view.

## Run

From the repository root:

```sh
.venv/bin/python Experiments/bootstrap_team_delivery/probe.py
```

## Scope

Real Ed25519 signatures and SHA-256 digests protect local JSON artifacts.
The human comparison is an independently supplied full digest, not a reviewed short-string wire ceremony.
Identity recognition is an explicit input supplied by the previous stage; this probe does not reimplement it.
One anchor grants one sibling team key enrollment authority for a named teammate and berth; the sibling separately signs the fresh-key enrollment.
That simplified extension is not the current membership schema or a general Constitution DAG evaluator.
The anchor and policy arrive as authenticated response fields; they are never inferred from the fetched event list.
The harness supplies a simulated local anchor-decision record naming that exact response, team, anchor, policy and deciding device.
The receiver has no default adoption: absent or mismatched records prevent adoption.
This tests binding and preservation of an explicit local input, not whether a human actually chose it.
The in-memory delivery fixture is untrusted, has no network, and contains no secrets.
Future Small Sea transport must go through the Hub.
No sender keys are released, Git history adopted, or runtime provisioning APIs changed.

## Results and choice

Both routes passed 36 named cases, plus checks that an unrecognized requester cannot invoke the honest responder and that changing each signed response field invalidates its signature.
The weakened self-consistency baseline accepted the whole-exchange substitute rejected by both authenticated routes.
Select the identity-signed route as the working design to avoid another ceremony when the identity binding is already accepted.
Keep fresh per-team comparison available for an independent check or when that prior binding is unavailable.
This choice changes who authenticates delivery, not what establishes team authority.

The fixture signs the request under the identity key and the fresh team signing key, and names the intended sibling and a local recognition reference.
Encryption keys, ciphertext delivery and durable databases remain outside this probe.
The event list is a complete selected fixture, not the general frontier/parked-event representation.

The signed route retains the prior identity-recognition record, including missing historical proof when recognition was a local decision.
A matching identity signature does not promote that decision into a verified historical chain.
Both routes still require the separate anchor adoption and team enrollment evidence.
No model verdict authorizes future key release by another device.
