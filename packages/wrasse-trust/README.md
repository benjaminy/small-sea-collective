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

## Keys Today

Each device holds one signing key per team, its **team-device key**.
There is no per-team identity key above it.
A `membership` certificate admits a teammate and names their first device key.
A `device_link` certificate adds another device key for the same teammate.
A device trusts these certificates only when they chain back to the team's authority anchor, a public key the device adopted out of band when it created or joined the team.

Three kinds of history are signed, each by a different key:

- **Core commits** are signed by the committing device's team-device key.
  The Manager signs them locally.
  A receiving Manager checks every commit against the device keys that the certificates establish, and requires the first commit to be signed by the anchor key (`small_sea_manager/git_verification.py`).
- **App commits**, such as Files, are signed by a **workhorse key**, one per device and berth.
  A team-device key authorizes a workhorse key for one berth with a signed workhorse delegation.
  The app never holds the private key: it asks the Hub to sign, and the Hub asks the same way to verify (`/session/verify`).
  The Hub accepts a commit only if its workhorse key is delegated for that berth and the delegating teammate holds that berth.
- **NoteToSelf commits** are signed by the device's NoteToSelf signing key.
  Nothing verifies them yet (#294).

Authority is judged by each device from the records it holds, in a *transitional view* (`small_sea_manager/berth_authority.py`, decision D3 of #266).
The view reports each commit as authorized, or refused with a reason: bad signature, wrong scope (a real key used on a berth it was not delegated for), missing authority, or ambiguous authority.
Removing a teammate stops their future authority, but commits they signed earlier still verify (#295 tracks commits made after a removal).

The earlier layered model, with a per-team identity key and `device_binding` certificates, survives only in `wrasse_trust/identity.py` and its tests; no other package uses it.

## Current Direction

The design direction has shifted to a **device-only, per-team** model:

- there is no global participant identity in the protocol
- each team membership gets its own fresh per-team participant UUID
- the only private signing keys are **team-device keys**
- "Alice/Accounting" means "this per-team UUID plus the device keys that
  validly speak for it"
- `membership` certs admit per-team participant UUIDs and name their
  founding device keys
- `device_link` certs expand an existing teammate's device set within one team
- NoteToSelf is socially useful for bookkeeping, but it is not
  cryptographically privileged
- "steward" remains Manager shorthand for automatic Core integration in the current Manager/Wrasse policy, not a Constitution-core key role
- significant teammate facts are signed append-only domain records carried by the team repository rather than inferred from Git authorship alone
- ordinary device keys never leave their devices; separately prepared recovery capability can authorize a fresh device key through a signed, conspicuous recovery ceremony

This direction is simpler, preserves per-team isolation more honestly,
and avoids syncing wrapped higher-level private keys around the system during ordinary operation.

The Constitution envelope and extension boundary in [`Documentation/team-constitution.md`](../../Documentation/team-constitution.md) are the canonical core direction.
Current identity, admission, integration-mode, and recovery behavior is Manager/Wrasse extension policy documented in the relevant package specs; Wrasse does not promote those choices into core verification rules.

## What Is Implemented Today

Wrasse Trust already provides useful building blocks that survive the rethink:

- typed certificate infrastructure
- certificate issuance and verification helpers
- ceremony serialization helpers used by Manager
- trust-graph traversal primitives

Pre-alpha rules apply here: clarity beats compatibility.

## Where To Read Next

- [README-brain-storming.md](README-brain-storming.md) explores cryptographic
  mechanisms for the identity/trust rethink and is subordinate to the
  canonical architecture model
- [device_provisioning_todo.md](device_provisioning_todo.md) captures the
  older provisioning plan and is currently a transitional reference, not the
  active intended design
