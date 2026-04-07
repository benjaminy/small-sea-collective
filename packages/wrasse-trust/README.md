# Wrasse Trust — Small Sea Identity and Trust

> [!WARNING]
> UNDER CONSTRUCTION: this README is a working design note, not a settled
> specification. The current code still uses the older BURIED/GUARDED/DAILY
> terminology and only implements part of the model described here. The goal of
> this document is to sharpen the concepts before more implementation lands.
>
> Design decisions are being calved off into issues and branches as they
> solidify. This document intentionally holds ideas that are not yet coherent
> with each other — it is a brainstorming surface, not a spec.

Wrasse Trust is the Small Sea package for identity, certification, and trust
evaluation. It is the layer that tries to answer questions like:

- Which keys belong to which participant/device/team-membership?
- Which certificates and attestations should be believed?
- How should trust flow across time, rotation, and team membership?
- How can teammates vouch for each other without a central identity provider?
- Can a participant prove they belong to a given team?

Wrasse Trust does not own transport or session encryption. That lives in
`cuttlefish`.

## Scope

Wrasse Trust currently owns:

- participant, device, and team trust modeling
- participant key hierarchies
- certificate and revocation formats
- key-signing ceremony helpers
- trust graph traversal

Wrasse Trust does **not** own:

- pairwise or group message encryption
- message transport
- cloud sync
- berth policy enforcement

Those concerns belong elsewhere in Small Sea.

## Why This Exists

Small Sea wants team-oriented trust to be a first-class feature.

The ambition is not merely "this account can sign this blob." The ambition is
closer to:

- teammates can vouch for people and their devices
- teams can be reasoned about as derived principals built from member and admin
  history
- trust can survive routine key rotation
- trust and authorization can be expressed as a graph rather than a central
  directory
- physical proximity and regular collaboration should produce cryptographic
  evidence of trust over time

That means Wrasse Trust needs to model more than one kind of key and more than
one kind of certificate.

## Working Model

This section is the current design direction, not a final spec.

### Core Principle: Per-Team Identities

A participant's identity in Small Sea is **per-team**, not global.

"Alice" as a global identity does not exist in the protocol. Instead:

- "Alice/Sharks" is Alice's identity within team Sharks
- "Alice/Jets" is Alice's identity within team Jets
- "Alice/NoteToSelf" is Alice's personal/device-management identity

These are distinct cryptographic identities with separate key material. They
can be **optionally linked** via cross-signing certificates — Alice can prove
that Alice/Sharks and Alice/Jets are the same person if she chooses to — but
the protocol does not require or assume this linkage.

This design has important properties:

- **Privacy**: compromising one team's data does not reveal Alice's membership
  in other teams
- **Social honesty**: people actually do present differently in different
  contexts; the protocol respects this rather than fighting it
- **Isolation**: a compromised team identity does not automatically compromise
  all of Alice's other team memberships
- **Flexibility**: pseudonymous participation is naturally supported

Per-team identity is a **privacy and isolation feature, not a technical
convenience**. The failure mode to avoid is building per-team identities at the
protocol layer and then having the UI silently collapse them into a single
"Alice" by default — that gives all the costs of the design with none of the
benefits. The UX layer must keep the scoping legible (see "Display Convention"
below), and cross-team linking must always be a deliberate, visible act rather
than an automatic default.

### Display Convention

The default display name for a participant is **`Name/Team`**, e.g.
`Bob/prayer-group` or `Alice/Sharks`, not bare `Alice`. This is a deliberate
UX commitment that makes the per-team scoping visible to users instead of
hiding it as plumbing. It rhymes with the way Mastodon's `@user@instance`
teaches federation: the visual format teaches the trust model.

Consequences:

- Linking two team-membership identities is a deliberate user act. When a link
  exists, the UI shows it as an explicit claim ("verified same person as
  Alice/Jets") rather than silently merging the two identities.
- Nickname collisions across teams are not a problem — `Bob/prayer-group` and
  `Bob/d&d-night` are simply different entries, with no need for global
  disambiguation suffixes.
- Team renames become a UX event because the team name is part of displayed
  identity.

NoteToSelf plays a special role: it is the one "team" that is always
single-participant, so it serves as the **device management context**. Device
provisioning, device key rotation, and cross-team identity linking are
operations that happen within NoteToSelf. NoteToSelf identity material never
appears on another team's chain, preventing accidental cross-team linkage.

### Teams Are Derived Principals

For version 1, a team does **not** need a special shared private key.

Instead, the team is a derived principal represented by the history in its
`{Team}/SmallSeaCollectiveCore` berth:

- who was admitted
- who currently has admin authority
- who was removed or revoked
- who certified which devices and keys

In that sense, "Sharks" is the admin chain plus the membership and revocation
history, not a separate secret sitting somewhere called "the Sharks private
key."

This is important because it keeps the team model practical:

- no shared team secret needs to be copied around
- team authority can be understood from history
- future quorum or threshold governance can be layered onto the history model
  without replacing it

### Independent Key Properties

A key in Small Sea has several mostly independent dimensions:

- `subject`: who or what the key speaks for (a team-membership identity, a
  device, etc.)
- `purpose`: what the key is for (identity root, signing, encryption, device
  binding)
- `protection`: how hard it is to extract or misuse (hardware-backed,
  passphrase-protected, biometric, offline)
- `time`: when it is valid and how it overlaps with predecessor and successor
- `scope`: which team context it belongs to

Those dimensions should not be collapsed into a single ladder.

For example, "offline" is mainly a protection mode, not a purpose. "Team key"
describes scope, not necessarily secrecy level. A useful trust model needs to
keep those axes separate.

### Subjects

Wrasse Trust reasons about these subjects:

- `team-membership identity`: a participant's identity within a specific team
  (e.g., "Alice/Sharks"). This is the primary subject.
- `device`: one concrete installation or hardware endpoint. Devices are managed
  through NoteToSelf but provisioned into team contexts.
- `team`: a **derived** collective principal represented by membership,
  authority, and revocation history rather than, in version 1, by a dedicated
  team private key

A participant's "global" identity, to the extent it exists, is their
NoteToSelf identity plus whatever cross-team links they choose to publish.

### Key Types (Purpose-Based)

The older BURIED/GUARDED/DAILY names bundled together purpose and protection in
a way that does not scale. The design direction is to name keys by purpose.

| Key | Purpose | Typical Protection | Rotation |
|-----|---------|-------------------|----------|
| **Team-membership identity key** | Rare-use certifying key for `Alice/Sharks`; signs device bindings, succession, and revocation | Encrypted in NoteToSelf with per-device wrappers; preferably hardware-unlocked only briefly | Rarely |
| **Team-device key** | Routine signing key for one concrete device in one team, e.g. `Alice/Sharks/phone` | Device-local, enclave-backed where available | Per device lifetime / reprovisioning |
| **Encryption key** | Receiving encrypted content for a team context or epoch | Device-local or synced per policy | Periodic, with overlap for decryption continuity |

Each team-membership identity (Alice/Sharks, Alice/Jets, Alice/NoteToSelf) has
its own certifying identity key. Each physical device gets its own distinct
per-team team-device key. Device keys are generated locally and provisioned
into team contexts via NoteToSelf (see
[device_provisioning_todo.md](device_provisioning_todo.md)).

The protection level of each key is a separate concern:

- synced encrypted blob
- password-protected local storage
- secure enclave or other device-bound hardware
- offline custody
- threshold or quorum control

### Where Private Keys Live

Near-term recommendation:

- the private key for a team-membership identity like `Alice/Sharks` lives in
  `NoteToSelf`, not in the Sharks team repo
- it should never be stored there in plaintext
- it should be stored as encrypted key material with one additional wrapper per
  authorized device
- routine signing should be done by per-device per-team keys, not by the
  team-membership identity key itself

That makes `NoteToSelf` the local control plane and inventory for private key
material, while the team repo remains the proof surface for public certs and
trust history.

This is not perfect security. If an authorized device is fully compromised, the
wrapped team-membership key may become exposed through that device. That is why
the team-membership identity key should be rare-use and why device removal may
need substantial rotation.

### Append-Only Trust Log (Sigchains in Git)

Trust accumulation requires tamper-evident history. Small Sea already has this:
**the git commit DAG**.

Certificates live in the `{Team}/SmallSeaCollectiveCore` databases, and the
git history provides the hash-linked chain. This means:

- Each team's trust state is append-only by construction (git commits are
  content-addressed and hash-linked)
- Any party can verify the history of cert issuance by walking the commit DAG
- Pruning stale data (which Small Sea already supports) does not destroy the
  trust chain — the commit DAG structure is preserved even when old content is
  pruned
- No separate sigchain format is needed — git IS the sigchain
- Public certs and authority history live in the team repo; private key blobs
  and device-specific wrappers live only in NoteToSelf

This is a significant architectural advantage. Systems like Keybase had to
build their own Merkle tree infrastructure; Small Sea gets it from the
substrate.

**Open question:** Does NoteToSelf need its own sigchain-equivalent for device
management operations that shouldn't be visible to any team? Probably yes — and
it already has its own git repo, so this is natural.

## Time and Rotation

Time is not just expiration. Time is part of the trust model.

Old keys and new keys each have desirable properties:

- older keys have accumulated more certifications and attestations
- newer keys are less likely to have been silently compromised

The design direction here is to let those benefits overlap instead of forcing a
hard tradeoff. In normal operation:

- key lifetimes should overlap
- trust policy should be able to value both continuity and freshness
- routine rotation should preserve trust chains when possible

Emergency revocation is different. In that case, overlap may be unsafe and the
system should prefer a sharp break.

**MVP simplification:** Hard rotation (revoke old, activate new) is acceptable
for version 1. Overlapping validity windows can be added later without changing
the cert format.

## Epochs, Removal, and Splits

Any removal is a serious event in a decentralized system.

- removing a teammate should always advance the team membership epoch and rotate
  the content and session material that teammate could read
- removing a device should revoke that device key and rotate anything that
  device could read or certify
- if a removed device could unwrap a team-membership identity key, that key may
  need to be re-wrapped or rotated as well

The hard truth is that a fully decentralized system cannot reliably prevent
splits under partition. A stale device or removed member may continue making
local progress while disconnected.

The design goal is therefore not "prevent every fork." The design goal is:

- make epoch changes explicit
- detect stale branches quickly
- reject ordinary writes from stale epochs once a newer epoch is known
- make fork resolution an explicit administrative or human action rather than a
  silent merge

One candidate invariant for version 1:

- membership changes and removals advance the epoch before any further normal
  writes are accepted

## Certificates

Wrasse Trust supports several certificate types rather than treating every
signed edge as the same thing.

### Certificate Families

- `self_binding`: this identity root signs its own sub-keys (signing key,
  encryption key)
- `device_binding`: a team-membership identity signs a per-team device key into
  that team context; NoteToSelf may orchestrate this, but the proof visible to
  teammates is team-local
- `cross_certification`: I sign your identity root (the ceremony output)
- `membership`: an admin (or quorum) certifies that a participant identity
  belongs to this team
- `succession`: this key supersedes or delegates from another key (rotation)
- `identity_link`: optional cross-team identity linking (Alice/Sharks vouches
  that Alice/Jets is the same person)
- `attestation`: this key was generated, held, or exercised under some stated
  protection condition
- `ambient_proximity`: low-stakes automatic cert from a proximity health check
  (see below)
- `revocation`: this key, device, or membership should no longer be trusted

### Typed Trust Traversal

Trust traversal must be **typed**. A valid trust path is not just "a pile of
signatures" — it is a meaningful chain of statements.

Examples of valid paths:

- trusted identity root → self_binding → signing key
- trusted identity root → device_binding → device key
- trusted identity root (Alice/Sharks) → membership cert → team Sharks
- trusted identity root (mine) → cross_certification → your identity root →
  self_binding → your signing key

The long-term aspiration is that trust can move across people and teams:

- person/team → cross_cert → person/team → cross_cert → person/team

That is not the same as a traditional CA tree, but it does rhyme with one.

### Which key types can issue which cert types?

This is a critical constraint that prevents nonsensical trust chains:

| Cert Type | Valid Issuers |
|-----------|--------------|
| `self_binding` | Own identity root only |
| `device_binding` | Team-membership identity key only |
| `cross_certification` | Identity roots or team-membership identity keys, not routine device keys |
| `membership` | Current admin chain (or future quorum) |
| `succession` | The key being superseded, or its certifying parent |
| `identity_link` | Either of the two identity roots being linked |
| `ambient_proximity` | Device keys (low-stakes, automatic) |
| `revocation` | Identity root, team-membership identity key, current admin chain, or the key's own parent |

This table is preliminary and will need refinement as the protocol solidifies.

The **concept** of typed certs is standard — X.509 has key usage extensions,
SPKI/SDSI has typed authorization, and Keybase sigchain links have explicit
types that constrain what can sign what. The **specific vocabulary** above is a
Small Sea invention tailored to the per-team identity model and ambient
proximity trust, with Keybase's sigchain link types as the closest ancestor.
The lesson from PGP's untyped web of trust is that signatures whose meaning
isn't explicit are impossible to reason about; typed certs are the fix.

## Team Membership and Authority

### "Is Alice on team Sharks?"

This is the most immediately important team question. A participant should be
able to **prove** team membership cryptographically. The mechanism:

1. When Alice joins Sharks, an existing admin signs a `membership` certificate
   binding Alice's Sharks identity root to the team
2. This cert is stored in the team's SmallSeaCollectiveCore DB (and thus in the
   git history)
3. Anyone with access to the team's git repo can verify Alice's membership by
   checking the cert chain: admin identity root → membership cert → Alice's
   identity root
4. Revocation of membership is an explicit `revocation` cert, also stored in
   the git history

### "Who speaks for the team?"

This is a harder social problem. For version 1, **teams are fully
cooperative** — all members trust each other, and any admin can perform admin
operations (add/remove members, etc.).

Future directions for contested or high-stakes team governance:

- Quorum/threshold signatures for specific operations (e.g., removing an admin
  requires k-of-n admin signatures)
- Tiered admin roles (owner vs. admin vs. member)
- Time-locked operations with cancellation windows

These are real problems but they do not need to be solved before version 1 is
useful. The cert format should be flexible enough to accommodate them later.

### Team Authority via Admin Chains (Not Shared Keys)

For version 1, the team does not have a single shared private key. Instead:

- The team has an **admin chain**: a sequence of membership + role certs in
  the git history that records who has admin authority
- Admins sign membership certs that grant roles
- Day-to-day team operations require a single admin signature
- The team's "identity" is the collection of its admin chain + membership certs,
  anchored in the git history

This avoids the operational nightmare of shared private keys and maps naturally
onto the git-based storage.

## Ambient Cross-Signing (Proximity Trust)

This is a distinctive Small Sea idea: **low-stakes, automatic cross-signing
ceremonies** that happen when teammates' devices are physically near each
other.

### The Concept

When Alice's phone and Bob's phone notice each other over Bluetooth (or
similar), they can perform a lightweight cryptographic health check:

- "Yup, still looks like this device belongs to Bob/Jets"
- This produces an `ambient_proximity` certificate
- These certs are low individual weight but accumulate over time

### Why This Matters

- **Trust is social.** The fact that Alice and Bob physically co-locate
  regularly is meaningful evidence that traditional PKI ignores.
- **Team identity gets cryptographic weight.** A team where members regularly
  see each other in person has a qualitatively different trust profile than a
  team of strangers.
- **Continuous verification.** Instead of a one-time ceremony, trust is
  continuously reinforced by ongoing physical proximity.

### Design Considerations

- Ambient certs should be **cheap to produce and store** (they will be
  numerous)
- They should be **individually low-weight** in trust evaluation — no single
  proximity ping should be sufficient to establish trust
- They should be **aggregatable** — "50 proximity pings over 6 months" is a
  meaningful trust signal
- They need **anti-replay protection** — a cert should prove "these devices
  were near each other at time T," not just "these devices have met at some
  point"
- **Battery and bandwidth** concerns must be respected — this cannot be
  power-hungry

### Open Questions for Ambient Trust

- What Bluetooth protocol? BLE advertisements? Some standard proximity protocol?
- How to prevent relay attacks (Mallory relays BLE between distant devices)?
- What's the minimum meaningful aggregation window?
- Should ambient certs be stored in the team git repo or only locally?

## Devices

Devices are first-class citizens in the trust model.

See [device_provisioning_todo.md](device_provisioning_todo.md) for the
concrete device provisioning design and implementation plan.

Summary:

- Devices are managed through NoteToSelf, which tracks encrypted team identity
  material and device-specific wrappers
- Each device generates its own per-team device key locally
- An existing trusted context uses the team-membership identity key to sign a
  `device_binding` cert for the new per-team device key
- Hardware-backed or enclave-backed attestations are expressible as certs

This lets Small Sea describe not just "who is Alice/Sharks?" but also "which
concrete installation is acting as Alice/Sharks right now?"

## Relationship to Cuttlefish

The trust model is intentionally separate from the session-crypto layer:

- Wrasse Trust decides which identities and keys should be believed
- Cuttlefish decides how messages and bundles are encrypted in transit

That split keeps each package smaller and cleaner. A future integration layer
will bind "this encrypted action" to "this trusted identity," but that binding
is outside the scope of Wrasse Trust itself.

## Trust Policy

Trust policy is how the system decides "should I believe this key?"

### Planned Policies

**TOFU (Trust On First Use):** Accept a single valid typed chain from a known
identity root. This is what most messaging apps do. Good enough for the MVP.

**Verified:** Require a ceremony-based cross-certification. This is the "safety
number verified" equivalent.

**Ambient-reinforced:** TOFU bootstrapping, with trust confidence increasing
based on accumulated ambient proximity certs. This is the distinctive Small Sea
mode, to be developed after TOFU and Verified are working.

Trust policy is expected to iterate significantly as Small Sea matures. The
cert format and graph traversal primitives should be policy-agnostic; policy
is a layer on top.

## Related Systems Worth Learning From

Wrasse Trust should invent as little as possible.

Systems that seem especially relevant:

- **Matrix cross-signing** for the split between user identity keys and device
  keys, and for the self-signing / user-signing key distinction
- **Keybase sigchains** and per-user keys for append-only signed state and
  rotating operational keys. Keybase's per-team key model (admin chain, not
  shared team key) is directly relevant.
- **OpenPGP** for person-to-person certifications and introducer-style trust.
  Note: PGP's untyped web-of-trust is a cautionary tale — typed certs are the
  fix.
- **TUF** for offline roots, delegated online roles, thresholds, and
  expiration. TUF's threshold model is relevant for future team governance.
- **MLS** for epoch and update language in the messaging layer (Cuttlefish
  concern, not Wrasse Trust)
- **SPKI/SDSI** for typed authorization and delegation edges rather than a
  strict X.509-style hierarchy. This is the closest conceptual ancestor to
  Wrasse Trust's cert model.

The likely Small Sea synthesis is something like:

- per-team identities with optional cross-team linking (novel)
- device management through NoteToSelf (novel, inspired by Matrix)
- append-only trust state via git commit DAG (novel use of existing substrate)
- typed certificates and delegation inspired by SPKI
- admin chains for team authority inspired by Keybase
- ambient proximity trust as continuous verification (novel)
- transport/session crypto kept separate in Cuttlefish, informed by MLS

## Module Map

These modules exist today, even though the conceptual model is still in flux:

- `wrasse_trust.keys` — participant key hierarchies and protection levels
- `wrasse_trust.identity` — certificate and revocation issuance/verification
- `wrasse_trust.ceremony` — payloads and helpers for in-person signing
- `wrasse_trust.trust` — certificate graphs and trust-path search

## Current Reality vs Direction

Current code reality:

- the implementation currently centers on a participant key hierarchy with the
  names `BURIED`, `GUARDED`, and `DAILY`
- certificate and revocation formats exist
- ceremony payloads and trust-path traversal exist
- **all current crypto code is placeholder** — it should not be preserved for
  backward compatibility or built upon

Design direction:

- per-team identities with NoteToSelf as device management context
- team as a derived principal from admin and membership history, not a shared
  team private key
- purpose-based key types (team-membership identity, team-device, encryption)
- wrapped storage for team-membership private keys in NoteToSelf
- typed certificates with issuer constraints
- trust log via git commit DAG (no separate sigchain infrastructure needed)
- ambient proximity cross-signing for continuous trust reinforcement
- team membership provable via admin-signed membership certs
- cooperative team governance for v1, with quorum governance deferred

## What Can Be Deferred Past Version 1

- Post-quantum crypto (API should be agnostic; ship Ed25519/X25519 only)
- Threshold/quorum team governance (single-admin is fine)
- Hardware attestation certificates
- Complex trust policies beyond TOFU
- Key overlap/validity windows (hard rotation is fine)
- Ambient proximity signing (requires Bluetooth protocol work)
- Cross-team identity linking (per-team isolation is the default)
- Offline roots and paper-key style recovery flows

## What Cannot Be Deferred

- Typed certificates (the format must support types from the start)
- Device provisioning (multi-device is fundamental to Small Sea)
- Team-membership identity / team-device / signing split
- Membership certificates (proving team membership is core)
- Trust log via git (this is already in place)
- Epoch transitions and stale-epoch rejection rules for removals
- Encrypted, wrapped storage for team-membership private keys in NoteToSelf

## Open Questions

- What exactly goes into a NoteToSelf sigchain entry for device provisioning?
  What fields, what format?
- What exact wrapper format should NoteToSelf use for encrypted
  team-membership private keys, and what metadata must be stored with each
  device-specific wrapper?
- For device removal, when is re-wrapping enough and when must the
  team-membership identity key itself rotate?
- What exact epoch data must be committed so stale writes are unambiguously
  detectable?
- For ambient proximity: what Bluetooth protocol? How to handle relay attacks?
  What aggregation window is meaningful?
- Should ambient certs be stored in team git repos (visible to all members) or
  only locally?
- What is the minimum set of cert types needed for a working MVP?
- How does the invitation flow (see invitation architecture in Manager) bind to
  the trust model? The invitation token likely needs to carry identity root
  material.
- How should cross-team identity linking work in detail? Does Alice/Sharks sign
  Alice/Jets' identity root, or do both sign a shared "identity link"
  statement?
