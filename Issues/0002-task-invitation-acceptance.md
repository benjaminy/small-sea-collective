---
id: 0002
title: Complete invitation acceptance round-trip
type: task
priority: high
---

## Context

The invitation flow is half-built. Alice can send an invitation, but the full round-trip — Bob accepting, his repo being set up, Alice's bucket registration updating — is not yet implemented. This is a core user-facing flow and a blocker for any real multi-user scenario.

## Work to do

- Delivery of acceptance back to Alice (how does she find out Bob accepted?)
- Bob's local repo setup after acceptance (clone? init? what remote?)
- Inviter bucket registration: once Bob accepts, Alice's Hub needs to know about his storage location
- Handle the repo divergence case: if Alice's repo has commits since the invitation was sent, what happens?
- Decide on delivery mechanism: polling Hub endpoint vs push notification

## References

- `packages/small-sea-manager/small_sea_manager/manager.py` — `accept_invitation` and related stubs
- `packages/small-sea-hub/small_sea_hub/backend.py` — session and invitation handling
- `Scratch/WIP.txt` — detailed analysis of acceptance delivery, repo divergence, inviter bucket registration
