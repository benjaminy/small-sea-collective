# Findings and next decisions

## Demonstrated and fixed locally

**Incomplete identity bootstrap was usable before verification.**
Commit `e9e26a0` creates the existing local block marker before installing state and enforces it through Manager and Hub access.
It also checks the joining public keys against the locally held request.
The focused existing suite passed 45 cases before the short review; the final ten experiment cases passed after review additions.
The review added malformed-marker handling and explicit connection closing.
Whole-snapshot authentication is still absent.

**The active-tip explanation overstated the bound.**
The Constitution model demonstrates that rolling merges can integrate more historical concurrent branches than the simultaneous active-tip cap.
The architecture wording now states the narrower property.
The one-hour run passed 614,400 exhaustive cases and 436,852 randomized trials.
These counts test a model, not production enforcement or cryptography.

## Prekey binding fixed; related questions remain

**Stored prekey substitution discloses sender keys.**
The reproduction decrypts the actual distribution with attacker-controlled keys while the artifact names a trusted recipient.
The scoped fix now signs the full bundle and scope under the trusted team-device key and verifies before encryption.
The combined bootstrap, team-creation, rotation and prekey checks passed 51 cases in 28.32 seconds.
Authentic stale-bundle replay and concurrent one-time-prekey consumption remain open.
The X3DH and team signing keys are distinct by design.
Checking equality would reject valid recipients; checking only a public device id would not authenticate the bundle.

## Demonstrated and still open

**Snapshot contents are not bound to the original confirmation.**
A changed device label still passes with the original welcome and confirmation string.
The joining-key check closes one inconsistency with independently held local evidence, not the broader substitution problem.

**Local Git configuration can alter verification.**
Legacy grafts can hide ancestry and `log.showSignature` can contaminate the report.
These are local-metadata/configuration cases, not ordinary remote-bundle attacks.
The experiment README records the two expected failures and successful command-control probes.

## Design questions to resolve with concrete alternatives

The invitation draft freezes a commitment before the invitee's fresh keys exist, while requiring that commitment to include them.
Separate an authenticated offer from the later newcomer-bound exchange, or require a request before the offer.
Neither choice requires a central service.

Authority chains belong to the selected extension and local policy, not the Constitution core.
Technical-origin binding must remain distinct from recognizing a founder or other authority anchor.
A permanent founder key should not become a mandatory protocol authority by accident.

Reconstruction must name the extension version, policy inputs, and selected view.
A policy difference must not silently cause the recipient to use the sender's broader projection for key distribution.
Prefer acting on locally reconstructed state; retain the delivered projection for comparison and diagnosis.

A removed key can sign later work against old parents.
Signed timestamps and a claimed basis cannot establish physical creation time.
Any local decision to accept its history should identify the finite objects accepted and grant no standing authorization for unseen work.
Do not adopt the initial Opus review's self-parent or all-tips rules without testing their costs and their compatibility with local choice.

A first-device authority anchor for NoteToSelf needs a recovery story, but that does not automatically justify making every team decision depend on a second identity DAG.
Identity device enrollment and per-team device enrollment are intentionally separate decisions.
