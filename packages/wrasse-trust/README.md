<img src="../../Documentation/Images/wrasse-trust.png">

# Wrasse Trust

Wrasse Trust is Small Sea's package for identity-oriented keys and certificates.
It is the layer that helps answer:

- which signing key belongs to which team member
- which device keys are currently vouched for
- which public certificates should be checked before trusting a signature

Wrasse Trust does not handle message transport or session encryption. That work
lives elsewhere, especially in `cuttlefish`.

## What Is Solid So Far

The current implementation supports a narrow, team-local trust model:

- each member has a per-team identity key
- each device has its own per-team device key
- device keys are certified by that member's per-team identity key
- those public device-binding certificates are stored in the team history
- private team-identity material lives in `NoteToSelf`, not in the shared team repo

In practice, this means Small Sea can already model:

- `Alice/Sharks` as a team-local member identity
- `Alice/Sharks/phone` as a concrete device signing key
- a `device_binding` certificate that says the phone key is vouched for by
  Alice's current Sharks identity

## Current Package Responsibilities

Wrasse Trust currently provides:

- key generation helpers with basic protection-level labels
- certificate issuance and verification helpers
- team-scoped `device_binding` certificates
- ceremony serialization helpers used by the manager

## What Is Still In Motion

Important parts of the long-term trust model are still being worked out:

- how much identity should remain strictly per-team versus optionally linked
  across teams
- how key rotation, revocation, and epoch changes should interact
- how second-device provisioning should evolve
- whether offline or threshold-controlled keys should become part of the core model
- how broad the certificate vocabulary should become beyond the current narrow slice

Those open design notes now live in
[README-brain-storming.md](README-brain-storming.md).

## Current Direction

The near-term direction is intentionally narrow:

- keep team trust local to team history
- use per-team device keys for routine signing
- use the per-team identity key rarely, mainly for certifying devices
- keep public proof material in the team repo and private key custody in `NoteToSelf`

That is enough to support the current device-registration work without freezing
the entire future trust architecture too early.
