# Bootstrap adversarial observations

The initial four micro tests demonstrated unsafe behavior on the starting branch.
The current ten cases preserve the remaining metadata-substitution observation and check the focused fixes below.
Run them from the repository root:

```sh
.venv/bin/python -m pytest Experiments/autonomous_bootstrap/adversarial/test_bootstrap_observations.py -q
```

The fixture disables Git commit signing only in each test process's environment.
It creates disposable identities and a localfolder remote; it needs no service or internet access.
The initial run failed because the user's global Git configuration requires GPG signing; the fixture now avoids that unrelated dependency.
The successful run reported `4 passed in 3.82s`.

## Findings

1. The valid control joins the identity and reproduces the authorizer's second confirmation string.
2. A replacement fetched database can alter a device label while preserving the original welcome bundle and second confirmation string.
3. More seriously, a replacement fetched database can replace the joining device's signing public key with an unrelated generated key while preserving the original welcome bundle and second confirmation string.
4. An interruption after source adoption but before final signature verification leaves a usable identity.
   A normal `TeamManager` can create a team while the pending identity join still exists.

The substitution cases change the source database and publish it through the ordinary localfolder publisher to model the bytes a storage attacker could supply.
They do not reauthorize the join, modify the welcome bundle, or use the authorizer's signing secret after the original authorization.
This experiment establishes acceptance of substituted fetched state; it does not claim a full downstream impersonation exploit.
The interruption case patches the finalization entry point to fail before verification, which models process death at that boundary rather than a real power loss.

## Design choices

Bind a digest of the exact authorized baseline to the signed welcome transcript.
Then accept that baseline before consuming newer remote state.
This is simple to reason about and makes the second confirmation cover the admitted database, but the transport must still retrieve the pinned baseline when the remote advances.
A signed commit identifier can name the baseline if the implementation specifies which Git object and tree it authenticates; hashing the canonical manifest or exact database bytes avoids relying solely on Git's object identifier as the trust anchor.

Alternatively, carry authenticated device records and other minimum trust material in the welcome bundle and verify the fetched database against them.
This allows remote progress without retrieving a historical database, but every field used to establish authority must be listed and checked.
It leaves ordinary unbound metadata outside the confirmation and creates a larger proof obligation as the schema grows.

For interruption safety, write the existing blocked marker before making the participant's state discoverable, and remove it only after every required verification succeeds.
That is a small experiment-friendly fix, provided both Manager and Hub access paths enforce it.
Staging the entire participant outside its active directory and publishing it with a final rename offers a clearer visibility boundary, but requires more changes to path references and retry behavior.
A failed or interrupted bootstrap may remain paused for a human; automatic recovery is not needed to close this trust gap.

## Focused fix, 2026-09-13

The Manager now writes a blocked marker before installing bootstrap state.
Manager entry and Hub discovery, session confirmation, and existing-session lookup enforce that marker.
Finalization also checks both joining public keys against the locally retained request before clearing the marker.
The original interruption and joining-key observations now assert rejection.

The focused validation passed 45 cases in 30.74 seconds, including existing identity bootstrap, device link, linked-device bootstrap, Hub session flow, and the original seven experiment cases.
It required loopback service access for two Hub transport cases; the first sandboxed run had 43 passes and two port-permission failures.

This fix does not authenticate the whole snapshot or record a human comparison.
The label-substitution observation still passes.
A bootstrap interrupted after adoption remains blocked with its evidence preserved; the existing public retry refuses an already installed shared database.
No automatic recovery or deletion of the failed installation was added.

The short review added three malformed-marker cases and explicit connection closing in finalization.
