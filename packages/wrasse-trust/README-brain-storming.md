# Wrasse Trust — Small Sea Identity and Trust

> [!NOTE]
> This file was previously `README.md`. It is now the package brainstorming
> note: useful for design work, but not a stable description of what Wrasse
> Trust currently guarantees or implements.

> [!WARNING]
> UNDER CONSTRUCTION: this README is a working design note, not a settled
> specification. The current code implements an earlier **layered** model
> (per-team identity keys wrapping per-team device keys) that the design
> direction below is in the process of retiring in favor of a simpler
> **device-only** model. Expect doc/code drift until the next refactor
> lands.
>
> Design decisions are being calved off into issues and branches as they
> solidify. This document intentionally holds ideas that are not yet
> coherent with each other — it is a brainstorming surface, not a spec.

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
- participant key lifecycle
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

The ambition is not merely "this account can sign this blob." The ambition
is closer to:

- teammates can vouch for people and their devices
- teams can be reasoned about as derived principals built from member and
  admission history
- trust can survive routine key rotation
- trust and authorization can be expressed as a graph rather than a central
  directory
- physical proximity and regular collaboration should produce cryptographic
  evidence of trust over time

That means Wrasse Trust needs to model more than one kind of subject and
more than one kind of certificate — even though, as the "Key Types" section
below describes, the underlying private key material turns out to be quite
uniform.

## Working Model

This section is the current design direction, not a final spec.

### Core Principle: Per-Team Identities, Device-Rooted Keys

A participant's identity in Small Sea is **per-team**, not global. And
within a team, the only private key material that exists is **per-device**.

"Alice" as a global cryptographic identity does not exist in the protocol.
Instead:

- `Alice/Accounting` is Alice's identity within team Accounting — represented
  by a per-team participant UUID plus one or more team-device keys that
  speak for that UUID
- `Alice/Marketing` is a different per-team identity with a different UUID
  and different device keys
- `Alice/NoteToSelf` is Alice's personal device-management context —
  cryptographically just another per-team identity, socially the one that
  is always present

These identities are distinct and unlinked by default. They can be
**optionally linked** via cross-signing certificates that Alice chooses to
publish into whichever team repos she wants the link visible in — but the
protocol does not require or assume such linkage.

**Within a team, the only keys are team-device keys.** There is no rare-use
"team-membership identity key" sitting above the device keys. There is no
private key material shared across Alice's Accounting devices. Each device
holds exactly one private signing key for each team it is enrolled in, and
that private key never leaves the device. "Alice in Accounting" is an
**equivalence class of device keys** linked by cross-signatures, rooted at
a membership cert that admitted the first device.

This design has important properties:

- **Privacy**: compromising one team's data does not reveal Alice's
  membership in other teams, because her keys in those teams are not
  derived from any shared secret
- **No wrapped-key hairball**: there is no shared private key material
  that has to be stored encrypted-per-device and re-wrapped as devices
  come and go
- **Honest device isolation**: losing a device means losing exactly that
  one device's private key, not exposing any higher-level identity
  material
- **Social honesty**: people actually do present differently in different
  contexts; the protocol respects this rather than fighting it
- **Per-team isolation is structural, not a discipline problem**: the
  keys in one team literally cannot sign anything in another team,
  because they are different keys with no shared parent

The layered alternative — a per-team identity key that certifies per-team
device keys — was previously considered and is the shape currently
reflected in the code. It has been retired from the design because it
introduced a rare-use private key that had to be wrapped and unwrapped
per device, created a "compromised device exposes the identity key" worry,
and added a layer of ceremony around device provisioning without buying
anything the cross-sig graph doesn't already buy.

**The honest cost of the device-only model is recovery.** If Alice's only
device is destroyed, her per-team identity in each team she was in is
cryptographically unrecoverable — there is no wrapped key to pull back
down. She can be re-admitted as a new member by her teammates, but as a
new member, not as a recovery of the old one. Recovery mechanisms (paper
keys, threshold backup, custodian devices, social recovery) are all
layerable on top as additional cross-sig sources and are explicitly
deferred past v1. The v1 UX needs to steer users toward enrolling a
second device early, precisely because that is the only cheap recovery
path the base model offers.

### Display Convention

The default display name for a participant is **`Name/Team`**, e.g.
`Bob/prayer-group` or `Alice/Accounting`, not bare `Alice`. This is a
deliberate UX commitment that makes the per-team scoping visible to users
instead of hiding it as plumbing. It rhymes with the way Mastodon's
`@user@instance` teaches federation: the visual format teaches the trust
model.

Consequences:

- Linking two team-membership identities is a deliberate user act. When a
  link exists, the UI shows it as an explicit claim ("verified same person
  as `Alice/Marketing`") rather than silently merging the two identities.
- Nickname collisions across teams are not a problem — `Bob/prayer-group`
  and `Bob/d&d-night` are simply different entries, with no need for
  global disambiguation suffixes.
- Team renames become a UX event because the team name is part of
  displayed identity.

NoteToSelf plays a special role socially — it is the one "team" that is
always single-participant, and therefore the natural place for
device-management bookkeeping and cross-team link certs to aggregate. But
NoteToSelf is not special **cryptographically**: `Alice/NoteToSelf/phone`
is just another team-device key, and it cannot sign anything in Accounting
or Marketing. Its role at install time is simply that it is the first team
that always exists.

### Teams Are Derived Principals

For version 1, a team does not need a special shared private key.

Instead, the team is a derived principal represented by the history in its
`{Team}/SmallSeaCollectiveCore` berth:

- who was admitted (membership certs)
- who was removed or revoked
- which devices are linked to which per-team participant UUID
- the overall commit DAG that hash-links everything together

In that sense, "Accounting" is its membership and revocation history, not
a separate secret sitting somewhere called "the Accounting private key."

**There is no cryptographic "admin" role in Small Sea.** "Admin" just
means "a person whose clone other teammates happen to pull from, and who
therefore tends to be the one typing the commands when new members are
added." The cryptographic layer does not enforce who may issue membership
certs — any existing member's device may issue one. Whether that admission
actually propagates to the rest of the team depends entirely on whose
clones teammates sync with. This is a social layer riding on top of a
cryptographic layer, not a cryptographic hierarchy.

One consequence worth naming: two teammates who pull from disjoint sets
of clones can have internally consistent but mutually different views of
who is in the team. The sync model already lives with this; the cert
layer inherits it. "Consistency" of team membership means "teammates
agree, via social gossip, on whose clones to watch."

This is what "derived principal" actually means when you unpack it: the
team is what its aggregated, socially-filtered history says it is, not a
cryptographically enforced consensus object.

### Independent Key Properties

A key in Small Sea has several mostly independent dimensions:

- `subject`: who or what the key speaks for (a per-team device)
- `protection`: how hard it is to extract or misuse (hardware-backed,
  passphrase-protected, biometric, offline)
- `time`: when it is valid and how it overlaps with predecessor and
  successor keys
- `scope`: which team context it belongs to

Older versions of this document included a `purpose` dimension. Under the
device-only model, purpose has collapsed to essentially one value
(team-device signing), and the dimension is no longer load-bearing. The
other four remain meaningful.

For example, "offline" is mainly a protection mode, not a purpose. "Team
key" describes scope. A useful trust model keeps those axes separate even
when one of them has gone flat.

### Subjects

Wrasse Trust reasons about two kinds of subject:

- **per-team participant UUID**: a stable opaque identifier that represents
  one person's membership in one team (e.g., `Alice/Accounting`). The UUID
  itself carries no key material and no metadata — it is just a label that
  appears in `membership` and `device_link` claims. What gives the label
  meaning is the cert graph anchored at the team's first membership cert.
  The UUID is per-team; there is deliberately no "global Alice ID" in the
  protocol, not even as an internal identifier.
- **per-team device**: one concrete installation acting as a per-team
  participant UUID. A device holds exactly one private signing key per
  team it is enrolled in. Devices and their cross-sigs do all the actual
  cryptographic work in Wrasse Trust.

A "team" is a derived principal, not a first-class subject with its own
private key material.

A participant's "global" identity, to the extent it exists, is the
optional cross-team link certs they choose to publish.

### Key Types

Under the device-only model, there is essentially one kind of private key:
**the team-device key**.

| Key | Purpose | Typical Protection | Rotation |
|-----|---------|-------------------|----------|
| **Team-device key** | All signing this device is ever asked to do in one team: `membership`, `device_link`, `cross_certification`, `identity_link`, routine content. (e.g. `Alice/Accounting/laptop`.) | Device-local, enclave-backed where available | Per device lifetime / reprovisioning; rotation within a single device is a new key plus a `device_link` from the old key to the new one |

The BURIED / GUARDED / DAILY key hierarchy in the current code is legacy
placeholder structure and does not correspond to anything in the design
direction. It will be removed in a later refactor.

Cuttlefish may introduce additional short-lived key material for pairwise
session crypto (ratchet state, prekeys, sender keys). That is a Cuttlefish
concern rather than a Wrasse Trust subject, and is kept outside this
model on purpose.

The protection level of each team-device key is a separate concern from
its subject:

- device-local storage with OS-level protection
- secure enclave or other device-bound hardware
- biometric or PIN gating on signing operations

Protection is a device-policy axis, not a Wrasse Trust subject.

### Where Private Keys Live

Very simply: **each device holds its own private keys, for each team it
is enrolled in, in that device's local key store.** Private key material
never leaves the device. There is no synced wrapped-key blob anywhere in
Small Sea.

NoteToSelf holds each device's own view of its enrollments (one row per
team saying "I am UUID `U` in team `T` with local private key `K`"), not
any shared secret material. If a device is destroyed, its private keys
are gone — there is no other copy.

Public certs — the part teammates need to see — live in each team's
`{Team}/SmallSeaCollectiveCore` berth, and are synced through the normal
repo sync mechanism.

As a convenience, copies of relevant public certs may also be cached into
NoteToSelf's SmallSeaCollectiveCore for local inventory and cross-team
reasoning. This is a **local cache**, not a canonical source — it never
contains anything that is not also in some team repo. The convenience is
that Alice's device-management UI can answer "show me everything I have
signed up for" by reading a single local DB instead of walking every team
repo.

### Append-Only Trust Log (Sigchains in Git)

Trust accumulation requires tamper-evident history. Small Sea already has
this: **the git commit DAG**.

Certificates live in the `{Team}/SmallSeaCollectiveCore` databases, and
the git history provides the hash-linked chain. This means:

- Each team's trust state is append-only by construction (git commits are
  content-addressed and hash-linked)
- Any party can verify the history of cert issuance by walking the commit
  DAG
- Pruning stale data (which Small Sea already supports) does not destroy
  the trust chain — the commit DAG structure is preserved even when old
  content is pruned
- No separate sigchain format is needed — git IS the sigchain
- Public certs and admission history live in the team repo; nothing
  else needs to be synced anywhere

This is a significant architectural advantage. Systems like Keybase had
to build their own Merkle tree infrastructure; Small Sea gets it from the
substrate.

NoteToSelf is its own git repo and thus has its own append-only history.
Device enrollment bookkeeping (Alice's own record of "this device is
enrolled in that team") can live there when a record-of-the-record is
useful, even though the canonical cert already lives in the team repo.

## Time and Rotation

Time is not just expiration. Time is part of the trust model.

Old keys and new keys each have desirable properties:

- older keys have accumulated more cross-sigs and attestations
- newer keys are less likely to have been silently compromised

The design direction here is to let those benefits overlap instead of
forcing a hard tradeoff. In normal operation:

- key lifetimes should overlap
- trust policy should be able to value both continuity and freshness
- routine rotation should preserve trust chains when possible

Under the device-only model, rotation within a single device is just
"generate a new team-device key, issue a `device_link` from the old key
to the new key, retire the old key when convenient." No ladder, no
overlap ceremony — the new key is visible to teammates as soon as the
`device_link` cert is.

Emergency revocation is different. In that case, overlap may be unsafe
and the system should prefer a sharp break.

**MVP simplification:** Hard rotation (revoke old, activate new) is
acceptable for version 1. Overlapping validity windows can be added later
without changing the cert format.

## Epochs, Removal, and Splits

Any removal is a serious event in a decentralized system.

- removing a teammate should always advance the team membership epoch and
  rotate the content and session material that teammate could read
- removing a device should revoke that device key and rotate anything that
  device could read
- any `device_link` certs issued by the revoked device after the last
  trusted checkpoint may need to be transitively reconsidered (standard
  revocation-with-back-dating)

Note that under the device-only model, there is no "the identity key was
wrapped on the removed device, do we have to rotate the identity key
too?" question. That worry is gone.

The hard truth is that a fully decentralized system cannot reliably
prevent splits under partition. A stale device or removed member may
continue making local progress while disconnected.

The design goal is therefore not "prevent every fork." The design goal is:

- make epoch changes explicit
- detect stale branches quickly
- reject ordinary writes from stale epochs once a newer epoch is known
- make fork resolution an explicit administrative or human action rather
  than a silent merge

One candidate invariant for version 1:

- membership changes and removals advance the epoch before any further
  normal writes are accepted

## Certificates

Wrasse Trust supports several certificate types rather than treating every
signed edge as the same thing.

### Certificate Families

Under the device-only model:

- `self_binding`: legacy transitional type for the current
  BURIED/GUARDED/DAILY placeholder hierarchy. Goes away when that
  hierarchy does.
- `membership`: an existing member's device admits a new per-team
  participant UUID into the team and names their founding device key in
  its claims. The very first membership cert in a team's history is
  self-issued at team creation and serves as the team's trust root.
- `device_link`: an existing device (already speaking for UUID `U` in
  team `T`) vouches that a second device also speaks for `U` in `T`.
  This is the equivalence-class expansion cert — what gets issued when
  Alice adds a new laptop to a team her phone is already in.
- `cross_certification`: one participant's device signs another
  participant's device during a ceremony (the safety-number-verified
  equivalent). Used between participants, not within one participant's
  own equivalence class.
- `identity_link`: optional cross-team link between per-team participant
  UUIDs (`Alice/Accounting` ↔ `Alice/Marketing`). Alice publishes these
  into whichever team repos she wants the link visible in. Opt-in only.
- `succession`: this key supersedes or delegates from another key. Mostly
  used for rotation within a single device; may or may not be needed
  given that `device_link` can express "same device, new key" directly.
  (See Open Questions.)
- `attestation`: this device key was generated or is held under some
  stated protection condition (hardware-backed, enclave-resident, etc.).
- `ambient_proximity`: low-stakes automatic cross-sig from a device
  proximity health check. Individually weak, aggregatable.
- `revocation`: this key, device, or membership should no longer be
  trusted.

**Transitional note on `device_binding`:** the previous layered model used
a `device_binding` cert type to link a per-team device key up to a per-team
identity key. Under the device-only model, that cert has no job — the
founding-device case is absorbed into `membership` (via the founding device
key named in the cert's claims) and the sibling-device case is
`device_link`. The `device_binding` cert type is being retired. The code
still implements it; the next refactor pass deletes it outright, with no
code-level deprecation phase (the whole codebase is still pre-alpha and
sandbox-rebuilds are cheap). This note exists in the doc only so that
readers mid-transition don't confuse themselves.

### Typed Trust Traversal

Trust traversal must be **typed**. A valid trust path is not just "a pile
of signatures" — it is a meaningful chain of statements.

Under the device-only model, the canonical question is: "is device `K`
trusted as UUID `U` in team `T`?" A valid answer walks a path of this
shape:

- team root (the first `membership` cert in the team's history, which is
  self-issued at team creation) →
- zero or more later `membership` certs transitively admitting `U` into
  the team, each issued by an existing member's device →
- `membership(U, founding_device=K₀, team=T)`, the cert that first
  admitted `U` →
- zero or more `device_link(K_i → K_{i+1}, U)` edges, each issued by a
  device already known to speak for `U` →
- arriving at `K`

The anchor is always the team's first membership cert. Everything walks
back to it.

Other cert types compose into this graph rather than replacing it:

- `cross_certification` edges enable paths *between* participants (e.g.,
  "I trust Alice's device because I verified it in a ceremony")
- `identity_link` edges enable paths *across teams* ("this UUID in
  Accounting is the same person as that UUID in Marketing") — opt-in
- `revocation` prunes paths
- `ambient_proximity` reinforces paths at low individual weight

The long-term aspiration is that trust can move across people and teams,
not as a traditional CA tree, but as a graph of typed statements.

### Which device can issue which cert type?

Under the device-only model, the issuer of every cert is a team-device
key. The interesting distinction between cert types is about **what
additional context the issuing device must already have** in order for
the cert to be meaningful to teammates verifying it.

| Cert Type | Issuer Rule |
|-----------|------------|
| `self_binding` | Legacy placeholder. Goes away with the BURIED/GUARDED/DAILY hierarchy. |
| `membership` | Any existing-member device in the team. Self-issued is allowed only at team genesis, when no prior member exists. Propagation is social, not cryptographic. |
| `device_link` | A device that already speaks for the same UUID `U` in the same team `T`. This is what grows the equivalence class. |
| `cross_certification` | Any team-device key, during a ceremony with another participant. |
| `identity_link` | A team-device key in each of the two teams being linked. Published into whichever team repos the linker wants the link visible in. |
| `succession` | The device key being superseded, or a co-device key via `device_link`. |
| `attestation` | The device key whose protection condition is being attested. |
| `ambient_proximity` | Any team-device key, low stakes. |
| `revocation` | A device that has a valid trust path to the thing being revoked — either a co-device of the subject (self-revoke) or an existing-member device of the team (membership revoke). |

None of these rules mention "admin." Admin is a social role — it refers
to the teammates whose clones other teammates happen to pull from — and
the cert layer does not need to encode that relationship. The cert layer
provides the signed history; the sync layer decides which histories
teammates actually see.

This table is preliminary and will need refinement as the protocol
solidifies.

The **concept** of typed certs is standard — X.509 has key usage
extensions, SPKI/SDSI has typed authorization, and Keybase sigchain links
have explicit types. The **specific vocabulary** above is a Small Sea
invention tailored to the per-team, device-only identity model and to
ambient proximity trust, with Keybase's sigchain link types as the
closest ancestor. The lesson from PGP's untyped web of trust is that
signatures whose meaning isn't explicit are impossible to reason about;
typed certs are the fix.

## Team Membership and Authority

### "Is Alice on team Accounting?"

Answered by a graph walk inside the team's own cert store:

1. Find Alice's per-team participant UUID `U` in this team's cert store
   (Alice's device tells the UI which UUID to ask about, since the UUID
   is opaque and per-team).
2. Find the `membership(U, founding_device=K₀, team=Accounting)` cert
   that admitted `U`.
3. Walk back from that cert's issuer through earlier `membership` certs
   until you reach the team's first membership cert (the self-issued
   genesis cert). If the walk reaches the root and no `revocation` cert
   prunes the path, `U` is a member.
4. To prove a *specific device* of Alice's is acting as `U`, walk forward
   from `K₀` through `device_link` edges. If the device in question is
   reachable, it speaks for `U`.

A teammate whose sync clones don't carry some of Alice's membership or
`device_link` certs will correctly conclude that Alice is not visible in
the team — not because Alice isn't a member, but because the social sync
layer hasn't delivered the relevant certs. That is the honest answer and
matches the derived-principal story.

### "Who speaks for the team?"

In Small Sea v1, **the team itself does not speak.** Individual members
speak; the team's "voice" is the aggregated history in its repo.

Any existing-member device can issue a `membership` cert admitting a new
participant. Whether that admission is seen by other teammates depends
on the social sync layer, not on cryptographic admin authority. A member
whose clone is not widely watched can still unilaterally admit someone,
but only teammates who actually pull from that clone will see the new
admission.

Future directions for contested or high-stakes team governance — quorum
signing, tiered roles, time-locked operations with cancellation windows —
can be added as additional cert types without changing the base model.
They are deferred past v1.

## Ambient Cross-Signing (Proximity Trust)

This is a distinctive Small Sea idea: **low-stakes, automatic cross-signing
ceremonies** that happen when teammates' devices are physically near each
other.

### The Concept

When Alice's phone and Bob's phone notice each other over Bluetooth (or
similar), they can perform a lightweight cryptographic health check and
each emit an `ambient_proximity` cert:

- "Yup, still looks like this device belongs to Bob/Accounting"
- Individually low weight, but accumulates over time

Under the device-only model, `ambient_proximity` certs fit naturally as
another kind of edge in the trust graph — they are just more edges, with
a low per-edge weight. No new subject types are needed.

### Why This Matters

- **Trust is social.** The fact that Alice and Bob physically co-locate
  regularly is meaningful evidence that traditional PKI ignores.
- **Team identity gets cryptographic weight.** A team where members
  regularly see each other in person has a qualitatively different trust
  profile than a team of strangers.
- **Continuous verification.** Instead of a one-time ceremony, trust is
  continuously reinforced by ongoing physical proximity.

### Design Considerations

- Ambient certs should be **cheap to produce and store** (they will be
  numerous)
- They should be **individually low-weight** in trust evaluation — no
  single proximity ping should be sufficient to establish trust
- They should be **aggregatable** — "50 proximity pings over 6 months"
  is a meaningful trust signal
- They need **anti-replay protection** — a cert should prove "these
  devices were near each other at time T," not just "these devices have
  met at some point"
- **Battery and bandwidth** concerns must be respected — this cannot be
  power-hungry

### Open Questions for Ambient Trust

- What Bluetooth protocol? BLE advertisements? Some standard proximity
  protocol?
- How to prevent relay attacks (Mallory relays BLE between distant
  devices)?
- What's the minimum meaningful aggregation window?
- Should ambient certs be stored in the team git repo or only locally?

## Devices

Devices are first-class citizens in the trust model — in fact they are
the *only* subjects that hold private key material.

See [device_provisioning_todo.md](device_provisioning_todo.md) for the
concrete device provisioning design and implementation plan. **Important:
that document still reflects the earlier layered model** and will be
rewritten in a later pass to match the device-only direction.

Summary of provisioning under the device-only model:

- **First install.** Small Sea creates NoteToSelf as "the team of one"
  and generates the first team-device key, `Alice/NoteToSelf/thisDevice`,
  along with a self-issued `membership` cert that anchors NoteToSelf's
  trust root. No other keys are generated.
- **Creating a new team.** Alice's device mints a fresh per-team
  participant UUID `U`, generates a new team-device key
  `Alice/{Team}/thisDevice`, and self-issues a `membership` cert binding
  `U` into the new team and naming the new team-device key as the
  founding device. No prior key (not even the NoteToSelf key) needs to
  participate — team creation is inherently a self-anchoring act.
- **Joining an existing team.** A current member's device issues a
  `membership` cert admitting the joiner's UUID and founding device key.
  The joiner generates their per-team participant UUID and team-device
  key locally before the ceremony; the existing member's `membership`
  cert carries both.
- **Enrolling a second device in a team Alice is already in.** The new
  device generates its own team-device key for that team. An
  already-enrolled device of Alice's, *for that same team*, issues a
  `device_link` cert linking the new device key to the existing UUID `U`.
  There is no cross-team enrollment shortcut, because there is no
  cross-team key material to propagate. This means adding a new laptop
  to an existing Alice who is in `N` teams is `N` cross-signs. **The UI
  can batch this into a single user action ("enroll laptop in all my
  teams"); the protocol stays per-team.**

The per-team-enrollment cost is the honest price of per-team isolation.
It is only felt at device-enrollment time, which is rare.

## Relationship to Cuttlefish

The trust model is intentionally separate from the session-crypto layer:

- Wrasse Trust decides which identities and keys should be believed
- Cuttlefish decides how messages and bundles are encrypted in transit

That split keeps each package smaller and cleaner. A future integration
layer will bind "this encrypted action" to "this trusted identity," but
that binding is outside the scope of Wrasse Trust itself.

Under the device-only model, Cuttlefish's multi-device story gets
cleaner rather than harder: X3DH (or PQXDH) initiates against *each*
target device, producing `N` pairwise ratchets for a multi-device
recipient. That is how Signal actually works for multi-device — the
layered model was hiding it and re-inventing a single-identity-key
abstraction that Cuttlefish didn't want anyway.

## Trust Policy

Trust policy is how the system decides "should I believe this key?"

### Planned Policies

**TOFU (Trust On First Use):** Accept a single valid typed chain from a
known team root. This is what most messaging apps do. Good enough for
the MVP.

**Verified:** Require a ceremony-based `cross_certification`. This is
the "safety number verified" equivalent.

**Ambient-reinforced:** TOFU bootstrapping, with trust confidence
increasing based on accumulated `ambient_proximity` certs. This is the
distinctive Small Sea mode, to be developed after TOFU and Verified are
working.

Trust policy is expected to iterate significantly as Small Sea matures.
The cert format and graph traversal primitives should be policy-agnostic;
policy is a layer on top.

## Related Systems Worth Learning From

Wrasse Trust should invent as little as possible.

Systems that seem especially relevant:

- **Matrix cross-signing** for device-rooted trust and the user-signing /
  self-signing split. Matrix has the closest ancestor to our
  `device_link` cert.
- **Keybase sigchains** and per-user keys for append-only signed state
  and rotating operational keys. Keybase's admin-chain-not-shared-key
  model for teams is directly relevant.
- **OpenPGP** for person-to-person certifications and introducer-style
  trust. Note: PGP's untyped web-of-trust is a cautionary tale — typed
  certs are the fix.
- **TUF** for offline roots, delegated online roles, thresholds, and
  expiration. TUF's threshold model is relevant for future team
  governance.
- **MLS** for epoch and update language in the messaging layer
  (Cuttlefish concern, not Wrasse Trust).
- **SPKI/SDSI** for typed authorization and delegation edges rather than
  a strict X.509-style hierarchy. This is the closest conceptual
  ancestor to Wrasse Trust's cert model.

The likely Small Sea synthesis is:

- per-team identities with optional cross-team linking (novel)
- device-rooted keys with no persistent per-participant key (inspired by
  Matrix/Signal, simpler than the layered alternative we previously
  considered)
- admission as the only team-level authority concept, with "admin" left
  as a social role (inspired by Keybase's per-team chain, pushed further)
- append-only trust state via git commit DAG (novel use of existing
  substrate)
- typed certificates and delegation inspired by SPKI
- ambient proximity trust as continuous verification (novel)
- transport/session crypto kept separate in Cuttlefish, informed by MLS

## Module Map

These modules exist today, even though the conceptual model is still in
flux:

- `wrasse_trust.keys` — participant key hierarchies and protection levels
- `wrasse_trust.identity` — certificate and revocation issuance/verification
- `wrasse_trust.ceremony` — payloads and helpers for in-person signing
- `wrasse_trust.trust` — certificate graphs and trust-path search

## Current Reality vs Direction

Current code reality:

- The implementation still reflects the older **layered** model (per-team
  identity key + per-team device key + `device_binding` certs between
  them).
- `KeyCertificate` is typed (`CertType` enum) as of the
  `typed-cert-format` branch.
- `device_binding` is still the cert type used for the founding device.
- Cert issuance still goes through `issue_cert`,
  `issue_device_binding_cert`, and `build_hierarchy_certs`.
- The BURIED / GUARDED / DAILY key hierarchy is still present as
  placeholder.
- All current crypto primitives are placeholder — they should not be
  preserved for backward compatibility or built upon.

Design direction:

- One kind of private key: the team-device key.
- Two core cert types for participants and devices: `membership` (with
  founding device key in claims) and `device_link` (for sibling devices).
- `device_binding` cert type retired; deleted outright in the next
  refactor with no code-level deprecation phase.
- BURIED / GUARDED / DAILY hierarchy deleted; `KeyPurpose` as a concept
  may not even need to exist under device-only (see Open Questions).
- Cross-team identity linking remains opt-in and published by the user
  into whichever team repos they choose.
- Admin authority remains a purely social concept — no cryptographic
  admin role is introduced.
- Trust anchoring is the team's first (self-issued, genesis) `membership`
  cert, reached by walking back through the cert graph and git commit
  DAG.

## What Can Be Deferred Past Version 1

- Post-quantum crypto (API should be agnostic; ship Ed25519/X25519 only)
- Device recovery: paper keys, threshold backup, custodian devices,
  social recovery — all layerable on top of the base model as additional
  cross-sig sources
- Quorum/threshold team governance (single-admin-as-social-role is fine
  for v1)
- Hardware attestation certs
- Complex trust policies beyond TOFU
- Key rotation with overlapping validity windows (hard rotation is fine)
- Ambient proximity signing (requires Bluetooth protocol work)
- `succession` as a distinct cert type, if `device_link` subsumes it

## What Cannot Be Deferred

- Typed certificates (already landed)
- Per-team participant UUIDs as the only identifier for a
  participant-in-team (already how the code works)
- `membership` and `device_link` cert types with clear issuer rules
- Device-only private key storage (no wrapped keys anywhere)
- Trust log via git (already in place)
- Epoch transitions and stale-epoch rejection rules for removals
- A v1 UX that steers new users toward enrolling a second device early,
  because that is the only cheap recovery path the base model offers

## Open Questions

- How exactly does the UI surface the "sole device destroyed = lost
  membership" consequence so users are nudged toward a second device
  early, without being alarming?
- Under the device-only model, is the `succession` cert type actually
  needed, or is "rotate a key within a device" always expressible as a
  `device_link` from the old key to the new key?
- How should revocation traverse `device_link` edges when a device is
  retroactively determined to have been compromised before a given
  checkpoint? This is the standard revocation-with-back-dating problem,
  but the graph structure makes it slightly more involved than a linear
  chain.
- Is a `KeyPurpose` field on `ParticipantKey` actually needed, given
  that under device-only there is essentially only one purpose left?
- What is the minimum useful `membership` cert claim shape? It needs to
  carry at least `participant_uuid`, `team_id`, and `founding_device_key`.
  What else?
- How do cross-team `identity_link` certs interact with the sync story?
  Publishing one into Accounting reveals that Alice also exists
  somewhere else — acceptable because the publication is opt-in, but
  worth naming explicitly in the UX.
- How does the invitation flow (see invitation architecture in Manager)
  bind to the trust model? The invitation token likely needs to carry
  the joiner's founding device key so the admitting member can include
  it in the `membership` cert.
- What exact epoch data must be committed so stale writes are
  unambiguously detectable?
- For ambient proximity: what Bluetooth protocol? How to handle relay
  attacks? What aggregation window is meaningful? Should ambient certs
  be stored in team repos or only locally?
