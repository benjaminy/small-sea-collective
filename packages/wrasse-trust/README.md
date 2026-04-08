<img src="../../Documentation/Images/wrasse-trust.png">

# Wrasse Trust

Wrasse Trust is Small Sea's cryptographic identity and trust layer.
It answers questions like:

- which team-device key is speaking right now
- which per-team participant UUID that device speaks for
- which public certificates and revocations should be believed
- how trust should flow through team history, device enrollment, and time

Wrasse Trust does not handle message transport or session encryption.
That work lives elsewhere, especially in `cuttlefish`.

## Current Reality

The code currently implements an earlier **layered** model:

- each member has a per-team identity key
- each device has a separate per-team device key
- `device_binding` certs link device keys to the per-team identity key
- wrapped private identity-key material is stored via `NoteToSelf`

That shape was a useful first slice, but it is no longer the intended
long-term model.

## Current Direction

The design direction has shifted to a **device-only, per-team** model:

- there is no global participant identity in the protocol
- each team membership gets its own fresh per-team participant UUID
- the only private signing keys are **team-device keys**
- "Alice/Accounting" means "this per-team UUID plus the device keys that
  validly speak for it"
- `membership` certs admit per-team participant UUIDs and name their
  founding device keys
- `device_link` certs expand an existing member's device set within one team
- NoteToSelf is socially useful for bookkeeping, but it is not
  cryptographically privileged
- "admin" remains a social sync concept, not a special key role

This direction is simpler, preserves per-team isolation more honestly,
and avoids syncing wrapped higher-level private keys around the system.

## What Is Implemented Today

Wrasse Trust already provides useful building blocks that survive the rethink:

- typed certificate infrastructure
- certificate issuance and verification helpers
- ceremony serialization helpers used by Manager
- trust-graph traversal primitives

Some of the currently implemented cert families and key structures will change
as the device-only model is pushed into code. Pre-alpha rules apply here:
clarity beats compatibility.

## Where To Read Next

- [README-brain-storming.md](README-brain-storming.md) is the live design
  note for the identity/trust rethink
- [device_provisioning_todo.md](device_provisioning_todo.md) captures the
  older provisioning plan and is currently a transitional reference, not the
  active intended design
