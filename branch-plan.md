# Branch Plan

## Goal

Follow up on GitHub issues `#4` and `#14` by getting Small Sea to a first
demo-grade crypto milestone:

- applications can open a normal team session through the Hub
- data in that session is actually encrypted/decrypted with `cuttlefish`
- the Manager persists enough key material that this flow survives past a toy
  in-memory demo

This branch is about getting one real encrypted path working end to end, not
about finishing the full Small Sea identity vision.

## Concrete Milestone

At the end of this branch, a normal team app session should be able to use
real sender-key encryption through the Hub.

Concretely:

- the Manager provisions and stores the key material needed for encrypted team
  sessions
- the Hub knows which sessions are encrypted and applies `cuttlefish.group`
- an app-level integration test proves that one participant can push encrypted
  data and another can pull and decrypt it

## Scope For This Branch

Implement now:

- encrypted **team broadcast** sessions
- persisted sender-key state for "my key" and "peer keys"
- a minimal bridge from Manager-managed state to Hub encryption/decryption

Defer for later:

- full X3DH / Double Ratchet integration with Manager workflows
- automated sender-key distribution over pairwise channels
- full `wrasse-trust` web-of-trust integration
- multi-device key transfer and revocation flows
- post-quantum variants

## Planned Shape

1. Add minimal persistent storage for sender-key state.
   - own sender key state lives with participant-owned data
   - peer sender key state lives with team-shared data
2. Teach Manager provisioning/invitation flows to create enough initial key
   material that a team can start using encrypted sessions immediately.
3. Teach the Hub session layer which sessions are encrypted vs passthrough.
4. Add a small Hub-side crypto adapter around `cuttlefish.group` so apps still
   hand the Hub plaintext and receive plaintext.
5. Prove the path with an end-to-end test that exercises real Hub-mediated
   encryption for a normal app/team session.

## Temporary Simplifications

To keep this branch concrete, it is acceptable if the initial sender-key
provisioning is more direct and less elegant than the final architecture.

That means:

- no compatibility shims
- no attempt to hide all future schema churn
- no need to solve trust-path policy before encrypted sessions exist
- it is fine if the first version bootstraps keys during invitation/setup
  rather than through the eventual pairwise ratchet flow

## Validation

The branch is successful if all of the following are true:

- `cuttlefish` micro tests still pass
- existing signing/bundle tests still pass or are updated to the new storage
  shape
- a new integration test demonstrates encrypted Hub session roundtrip for a
  normal team app workflow
- the encrypted roundtrip test would fail if Hub encryption/decryption were
  bypassed

## Questions To Resolve Early

- what is the smallest schema change that gives us durable sender-key state?
- where should the Hub read the current sender key from?
- what is the cleanest way to mark a session as encrypted vs passthrough?
