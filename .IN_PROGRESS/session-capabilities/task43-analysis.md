# Task 43: who may set up a workhorse signing key, and when

Five gpt-6-astra (medium) reviews, one per option (a)–(d) and one asked for an alternative.
Raw outputs: session scratchpad `t43-analysis/last-{a..e}.txt`.

## The problem

Task 43 (branch `t43-ensure-signing`, `b875d83`) sets up a device's berth key and delegation on first signing request.
It refuses with `berth_not_held` unless this teammate holds the berth under this device's own view.
Invitee Bob is refused, because his grant exists only in Alice's Core and nothing brings it into Bob's.

## Findings the reviewers agreed on

1. **The `berth_not_held` check contradicts the architecture.**
   architecture.md:421 says both integration modes "may receive readable updates and author signed changes", and that integration mode "answers what peers normally incorporate, not who is capable of writing bytes."
   A proposal-only teammate is supposed to sign proposals.
   Requiring `HELD` before signing forbids that, independent of Bob's missing grant.
   Options (a) and (c) keep this contradiction; (a)'s reviewer and (c)'s reviewer both flagged it.

2. **The check is a setup gate, not continuing authorization.**
   Once a key and delegation exist, task 43 returns before checking standing.
   So even as written it does not mean "this device only signs what it considers authorized."
   Whatever we choose, the docs must say which one it is.

3. **Signing and acceptance are different decisions, made on different devices.**
   Every verifier judges under its own view (berth_authority.py, architecture.md:31).
   A signer-side refusal protects no other device: Alice's verifier decides for Alice.
   Its only protective value is local: stopping Bob from building history his own device will refuse.

4. **Option (b) changes what Alice signed.**
   A proposal's `mode_plan` is an offer, and finalization turns it into grants.
   Treating the offer as standing lets an abandoned invitation grant authority, and could extend `other_mode` to berths created after the offer.
   No reviewer defended (b) as more than a stopgap.

5. **Evidence has to flow both ways, and today it flows neither way.**
   Bob needs Alice's grant to see his own work as authorized.
   Alice needs Bob's delegation, which lives in Bob's Core, to see Bob's work as authorized.
   The two-device experiment passes today only because Files does not verify yet (task 45).
   No option for task 43 alone makes cross-device verification work.

## The alternative (reviewer e)

Make returning the completed grant part of invitation completion.
Alice's finalization produces an admission package: Bob's signed mode-change grants, his enrollment certificate, and the records chaining them to the anchor, bound to this exact acceptance and Bob's fresh device key.
Bob's Manager verifies and adopts those records into his Core.

This is not a new idea: Documentation/bootstrap-trust.md:375 already describes the invitation as offer, request, and **final response**.
The implementation stops after the request: Alice finalizes, but nothing returns to Bob.
So (e) is closing a gap between the documented protocol and the code, not adding a mechanism.
It is a narrow, bounded import, much smaller than general Core integration (#228), and it does not decide how to merge divergent histories.

## Recommendation

- **Task 43 now: option (d).**
  Setup requires an adopted anchor, an enrolled team-device key for this device, and an approved berth session.
  It does not require berth standing.
  This matches architecture.md:421 and removes a check that protects nothing outside this device.
- **New task: the invitation final response (e).**
  Alice returns the admission package; Bob adopts it.
  After that, Bob's own device sees his work as authorized.
- **Keep #228 (general Core integration) separate.**
  It is still needed so Alice learns Bob's delegation, and for everything after onboarding.
  Task 45 (Files verifies) depends on both.

The strongest argument against (d), from its own reviewer: if Bob's view has conflicting grants, Bob can keep signing work his own device calls ambiguous.
That is acceptable if the docs say signing success is not acceptance, and if the app shows the verdict.
Files will do so under task 45.

## What the docs must state

- Signing setup requires: an adopted anchor, this device's team-device key enrolled in the local view, and an approved Hub session for the berth.
  It does not require berth standing.
- A workhorse delegation says "this device's key for this berth."
  It grants nothing by itself; a verifier accepts it only if the delegator holds the berth in the verifier's view.
- A successful signature proves nothing about acceptance, on this device or any other.
  Every device, including the signer's, judges each commit under its own current view, and the verdict can change as evidence arrives.
- Integration mode governs what peers incorporate, not who may sign (restate architecture.md:421 where signing is described).
- The invitation's final response carries the post-finalization evidence the invitee needs, and the invitee's Manager adopts it explicitly.
  Until then the invitee may sign, and its own device reports its work as missing authority.

## Open for the owner

- Accept (d) for task 43, with the invitation final response as a separate task?
- Should an ambiguous local view pause signing (a separate, explicit signing policy), or only acceptance?
  The reviewers lean toward only acceptance, which keeps signing simple.

## Decisions (owner, 2026-09-27)

- Task 43 follows (d); the invitation final response becomes its own task.
- Ambiguous or missing authority pauses acceptance, not signing.
- Implementing (d) showed that the enrollment check blocks an invitee the same way the standing check did: Bob's enrollment certificate is also written only into Alice's Core.
  The owner chose to drop the local enrollment check too.
  Setup now requires only an adopted anchor, this device's team-device key, and an approved session.
