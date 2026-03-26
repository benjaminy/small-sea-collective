# Cuttlefish — Small Sea Cryptographic Layer

Cuttlefish provides the two foundational cryptographic services for Small Sea:
team-level end-to-end encryption, and key-based identity with a multi-key web
of trust. It is designed to give strong security guarantees in the absence of
any trusted central service — service providers can affect availability but
nothing else.

---

## 1. Team-Level Encryption

### Design Basis

Cuttlefish uses the [Signal Protocol](https://signal.org/docs/) as its
foundation. Signal is arguably the most carefully designed and widely audited
E2E messaging protocol in existence. Any deviation from it is a deliberate
research decision that needs a documented justification.

The relevant Signal components:

- **PQXDH** (Post-Quantum Extended Triple Diffie-Hellman) — asynchronous key
  agreement. Signal shipped this in 2023 as a replacement for X3DH. It combines
  X25519 with ML-KEM so that security holds if either primitive is unbroken.
  Cuttlefish follows this spec rather than the older X3DH spec.
- **Double Ratchet** — per-session forward secrecy and post-compromise security.
  Each message advances a ratchet; a leaked key compromises at most one message.
- **Sender Keys** — efficient group messaging. Each group member holds a
  symmetric sender key distributed via pairwise channels; messages are
  encrypted once and are decryptable by all current members. Each message is
  also signed with the sender's asymmetric signing key to prevent
  impersonation. See "Signal Group Messaging Adaptation" in section 2.

### Post-Quantum Cryptography

NIST finalized three PQC standards in August 2024. Cuttlefish uses a hybrid
classical + post-quantum approach throughout. Security holds as long as either
the classical or the post-quantum primitive is unbroken.

| Use | Classical | Post-Quantum | Notes |
|-----|-----------|--------------|-------|
| Key agreement (DH) | X25519 | ML-KEM-768 (FIPS 203) | DAILY/GUARDED keys |
| Key agreement (DH) | X25519 | ML-KEM-1024 (FIPS 203) | BURIED keys |
| Signatures | Ed25519 | ML-DSA-65 (FIPS 204) | DAILY/GUARDED keys |
| Signatures | Ed25519 | SLH-DSA-128s (FIPS 205) | BURIED keys only |

SLH-DSA (SPHINCS+) is chosen for BURIED keys because its security reduces to
collision resistance of a hash function alone — the most conservative possible
assumption. Its large signatures (~8 KB) are acceptable for rare offline use.
The "harvest now, decrypt later" attack is most dangerous for long-lived root
keys, making PQC most important exactly where it is easiest to accommodate.

### The Decentralization Problem

Signal assumes a central server that stores prekey bundles and relays
ciphertexts. Small Sea has no such server. The adaptations:

- **Prekey bundles are published to cloud storage** (via the Hub) rather than
  to a Signal server. Any team member can pick them up asynchronously. This is
  the primary mitigation for the "offline party" problem.
- **Where pre-computation can't help**, some flows will require additional
  round-trips — possibly through an out-of-band channel. This is the
  acknowledged price of decentralization.
- **Prekey exhaustion** — if a recipient's one-time prekeys run out and they
  are offline, the default policy is **STRICT**: key agreement fails and the
  sender must wait or prompt the recipient to replenish their bundle. Callers
  may opt in to **DEGRADE** mode, which falls back to the signed prekey only
  (matching Signal's original behavior), sacrificing the incremental forward
  secrecy of one-time prekeys. The default is deliberately the secure choice.
- **Group membership changes** (member joins, leaves, device revocation) may
  require a full sender key rotation. The protocol should make the happy path
  cheap and the rotation path correct, even if slow.

### Forward Secrecy and Post-Compromise Security

The goal is to eventually achieve both, matching Signal. This is believed to
be novel in a fully decentralized async setting and is explicitly research
territory. The initial implementation uses stubs; the ratchet and sender key
machinery will be layered in incrementally with clearly marked TODO points.

---

## 2. Key-Based Identity

### A Fresh Start

Identity in the Small Sea Collective is hard.
There is no central/global actor to provide any kind of trust/identity anchor.
Rather we need *some kind* of web of trust model.
Participants add evidence to support identity trust through other channels (ideally meeting in the real world and scanning QR codes or similar).
And that trust propagates around networks of teams somehow.
That sounds good, but getting it right is **hard**.
Webs of trust is not a new idea, but it has yet to have much impact on regular people/systems.

### Signal Group Messaging Adaptation

Cuttlefish adapts Signal's Sender Keys group messaging protocol to Small Sea's
serverless, store-and-forward architecture. The core model:

**Sender keys for team broadcast.** Each team member holds a symmetric sender
key. When Alice pushes a bundle to her team bucket, she encrypts it once with
her sender key. Every teammate who holds that key can decrypt it. This is
Signal's Sender Keys protocol — one encryption operation per message regardless
of group size.

**Pairwise Double Ratchet channels for key distribution.** Sender keys,
identity key certifications, and membership-change notifications are
distributed over pairwise channels between team members. These channels use
the Double Ratchet protocol for forward secrecy and post-compromise security.
Pairwise channels are used infrequently — only for key lifecycle events, not
regular data flow.

**Asymmetric signatures for authenticity.** Every bundle is signed with the
sender's per-team Ed25519 signing key. Teammates verify signatures using the
sender's public key (stored in the team DB `member` table). This eliminates
the impersonation risk inherent in symmetric sender keys — even though Bob
holds Alice's sender key (for decryption), he cannot forge her signature.
Signal's protocol also includes asymmetric signatures on sender key messages
for the same reason.

#### Cloud Storage Layout

Pairwise channels are implemented as lightweight bucket pairs, structurally
identical to team broadcast buckets. For Alice in team Friends with Bob and
Carol:

```
ss-<friends-station>/              # team broadcast (sender-key encrypted)
ss-<alice→bob(friends)>/           # pairwise: Alice → Bob
ss-<alice→carol(friends)>/         # pairwise: Alice → Carol
```

The pairwise buckets use the same Cod Sync push/pull mechanics as team
buckets. They carry little traffic — just key distributions and certifications
— so storage and bandwidth costs are minimal.

**Transport opportunism.** The S3 bucket is the reliable baseline for pairwise
channels, but faster transports can be used opportunistically. If Alice and
Bob already have a Hub-mediated VPN tunnel or are on the same LAN, pairwise
ratchet operations can happen over that channel instead. The protocol is
transport-agnostic; only the key material matters.

#### Hub, Manager, and Cuttlefish Responsibilities

All cloud storage access goes through the Hub — the Manager never talks to
S3/GDrive/Dropbox/etc directly. But the Hub applies crypto selectively based
on the session type, which is set at session creation time:

- **Encrypted sessions** (team broadcast): The Hub uses `cuttlefish.group`
  to apply sender-key encryption on upload and decryption on download. This
  is the default for normal app data. Apps hand the Hub plaintext; the Hub
  handles crypto transparently.

- **Passthrough sessions** (pairwise channels, key management): The Hub
  uploads and downloads bytes as-is. The Manager has already applied the
  appropriate crypto (Double Ratchet encryption for pairwise key distribution
  messages) before handing the data to the Hub.

This means:
- The **Hub** depends on `cuttlefish.group` only — it does sender-key
  encrypt/decrypt for broadcast sessions and acts as a dumb pipe otherwise.
- The **Manager** depends on `cuttlefish.group` (sender key creation and
  distribution) and `cuttlefish.ratchet` (pairwise channel encryption). It
  owns all key lifecycle operations: generation, rotation, certification,
  and distribution.
- **Apps** are crypto-unaware — they talk to the Hub, which handles
  encryption transparently for team broadcast data.

#### Cross-Team Identity: Flexible Pairwise Scope

Pairwise channels can be scoped per-team or shared across teams, at the
participants' discretion:

- **Per-team pairwise channels** (default): `Alice(Friends)→Bob(Friends)` is
  completely independent from `Alice(WorkProject)→Bob(WorkProject)`. No
  cryptographic correlation between teams. This is the right choice when
  cross-team deniability matters — e.g., Carol and Dave work together and are
  in a political action group, and prefer not to cross-contaminate those
  relationships.

- **Shared pairwise channels**: `Alice→Bob` serves all teams they share. Fewer
  buckets, shared ratchet state. This is simpler and is likely the common case
  — most people are fine being identified as the same person across their teams.

The protocol must support both modes. The choice is made per-pair, not
globally: Alice and Bob might share a pairwise channel, while Alice and Carol
keep theirs per-team. The bucket naming scheme encodes this choice (team-
scoped names include the team station ID; shared names do not).

#### Key Lifecycle Coordination

Key rotations and membership changes are coordinated through the team's
`{Team}/SmallSeaCollectiveCore` station:

1. **Trigger**: A member pushes a key-event record to their team station's
   SmallSeaCollectiveCore (e.g., "I rotated my sender key," "new member
   joined," "I'm certifying Bob's identity key").

2. **Notice**: Other members pull from SmallSeaCollectiveCore, see the event,
   and know they need new key material.

3. **Exchange**: The actual secret key material (new sender keys, ratchet
   messages) flows over the pairwise channels — never through the team
   broadcast station.

This separation is important: the team broadcast side carries only
announcements and public certificates. Secret material only travels over
pairwise ratcheted channels.

**Events that trigger sender key rotation:**
- A member joins the team (new member needs sender keys from all existing
  members; existing members rotate their sender keys so the new member
  cannot read pre-join history)
- A member leaves or is revoked (all remaining members rotate their sender
  keys so the departed member cannot read post-departure messages)
- Periodic rotation (limits blast radius of a compromised sender key)
- Device compromise/revocation for any member

#### Sequencing and the Hub

Bundles for many apps flow through the Hub simultaneously. Out-of-order
delivery is expected:

- **Sequence numbers in Cod Sync links** let a puller detect gaps ("I have
  link 4 but not link 3") and defer decryption until the gap is filled.
- **Per-app-per-team queues in the Hub** allow the Hub to deliver Chat bundles
  immediately while queueing Calendar bundles for an app that isn't running.
- **Key-dependency queuing**: if a bundle arrives but the recipient hasn't yet
  received the sender key (via pairwise channel), the Hub queues it until the
  key distribution completes.

### Two Tiers of Keys

Every byte/blob/bundle that goes out to the internet through Small Sea is
encrypted and signed.  The key architecture has two tiers:

**Workhorse keys** — per-participant, per-device, per-team. Stored in secure
enclaves where available (or password-encrypted keys on legacy devices). Used
for the actual encrypt/sign operations on bundles. These are somewhat
transient; they should not be the primary locus of identity.

The rotation schedule for workhorse keys is an open question. We need
machinery to rotate them without major disruption — probably an overlap period
where the new key is propagated but not yet used, then a switchover.

**Identity keys** — per-participant, per-team, cross-device. Stored in a table
in the NoteToSelf/SmallSeaCollectiveCore station (which syncs across the
participant's devices). These keys certify workhorse keys: a certificate from
an identity key says "this workhorse key belongs to me and is authorized to
act on my behalf in this team."

`{Team}/SmallSeaCollectiveCore` stations hold a cert table where participants
attest to their own workhorse keys and each others' identity keys.

### The Problem with Single Key-Pairs

Traditional public-key identity systems rely on a single long-lived key-pair
per identity. This fails in practice: a single key that is old enough to have
accumulated trust is also old enough to have been quietly compromised; a fresh
key is safe but has no accumulated trust. Small Sea's approach is to work with
a *collection* of keys per participant that vary along two dimensions.

### Key Dimensions

**Protection level** — how hard the key is to unlock on a given device:

| Level | Unlock mechanism | Typical use |
|-------|-----------------|-------------|
| `DAILY` | Biometric / device PIN | Routine message signing |
| `GUARDED` | Explicit passphrase | Ceremony signing, capability grants |
| `BURIED` | Long passphrase stored offline | Root-of-trust operations only |

**Age** — keys are issued at a point in time and accumulate certs from
teammates over their lifetime. An older key has more social proof; a newer key
is less likely to have been quietly stolen. Both matter.

### CA-Style Hierarchy Within a Participant

A participant's key collection is structured as a small CA hierarchy:

```
BURIED key  (root, rarely used, signs intermediates)
    └── GUARDED key  (intermediate, signs daily keys, rotated periodically)
            └── DAILY key  (leaf, used for routine operations)
```

When a participant rotates a DAILY key, the GUARDED key signs the new one,
preserving the chain of trust without requiring a new signing ceremony with
teammates. The BURIED key is only invoked to issue or revoke GUARDED keys.

### Web of Trust

Team members sign each other's keys to establish identity. All certificates
are published publicly (via cloud storage), so any party can attempt to trace
a trust chain from a signing key to a key they already trust.

No single trust metric is mandated. A relying party can specify their own
policy — e.g., "I require certs from at least two keys of level GUARDED or
above, from at least two different teams, with at least one key older than
six months." This is research territory; the initial implementation just makes
the certs available and defers trust policy to callers.

### Signing Ceremonies

The bootstrapping problem — how do you trust a key in the first place? — is
addressed through lightweight physical ceremonies:

- **Bump / proximity exchange**: two people in the same physical space can sign
  each other's keys by bumping phones or scanning a QR code. The signing target
  is the GUARDED key (or a delegation from it), so the ceremony result
  propagates down to DAILY keys automatically through the hierarchy.
- **Smart chaining**: the bump ceremony does not need to directly sign every
  key. Signing one key in the hierarchy, combined with the locally-signed
  chain, transitively extends the trust. The goal is that a user should never
  need to think about which key is being signed.

See the Prior Art section below for related systems (Matrix, Keybase, Briar).

**Open question**: whether the bump ceremony should sign a single key or a
*binding* (a signed statement that "these keys all belong to the same
participant"). The latter is more powerful but has tricky revocation semantics.

### Device Compromise and Revocation

Devices are cryptographically identifiable in Small Sea. If a device is
known or suspected to be compromised:

1. Any teammate can issue a **revocation certificate** for the keys associated
   with that device.
2. Revocation certs are published to cloud storage alongside the original certs.
3. Because every device a tainted key ever touched is potentially traceable
   through the cert graph, revocation can propagate — but the scope of that
   propagation is an open design question.

The BURIED key, being offline and rarely used, is the most revocation-resistant
key. It is the natural choice for signing revocations of lower-level keys.

---

## Threat Model

| Threat | Posture |
|--------|---------|
| Passive eavesdropping on cloud storage / transport | Defeated by E2E encryption |
| Active tampering by cloud storage provider | Defeated by authenticated encryption + cert transparency |
| Compromised Hub server | **Trusted** — Hub is in the trusted computing base |
| Compromised service provider (non-Hub) | Adversarial; can affect availability only |
| Stolen / hacked device | Mitigated by key hierarchy and revocation; long-term open problem |
| Quiet key compromise (no physical theft) | Mitigated by key rotation and the age/protection-level dimensions |

---

## Module Map

| Module | Responsibility |
|--------|---------------|
| `keys.py` | Key types, protection levels, serialization, the participant key collection |
| `prekeys.py` | X3DH prekey bundle generation, publication, and consumption |
| `x3dh.py` | Extended Triple DH key agreement (async session initiation) |
| `ratchet.py` | Double Ratchet (per-session forward secrecy) |
| `group.py` | Sender Keys group messaging |
| `identity.py` | Certificates, the CA hierarchy, signing and verification |
| `ceremony.py` | Key signing ceremony helpers (QR / bump exchange format) |
| `trust.py` | Trust chain traversal and policy evaluation |

---

## Prior Art

The individual pieces of Cuttlefish's design have strong prior art; their
specific combination — particularly the two-dimensional key model (protection
level x age) with relying-party-configurable trust policy — appears to be
novel. No existing system combines all three of: physical ceremonies,
hierarchical per-participant keys, and a fully decentralized web of trust.

### Systems to study and build on

**Matrix cross-signing** — the closest production system. Three-key hierarchy
(Master → Self-Signing → User-Signing) maps roughly to BURIED/GUARDED/DAILY.
QR-based verification ceremonies. Trust is binary (verified or not); no
concept of key age as a positive signal. Federated, not fully decentralized.
- [Cross-signing overview](https://jcg.re/blog/quick-overview-matrix-cross-signing/)
- [E2EE implementation guide](https://matrix.org/docs/matrix-concepts/end-to-end-encryption/)

**Keybase sigchains** — multi-device key management with a public audit trail.
Each device has its own key-pair; a Per-User Key (PUK) is encrypted to all
active device keys and rotated on revocation. Paper keys serve as offline
recovery. The sigchain concept is the best reference for how Cuttlefish
should structure its public certificate log. Effectively dead (Zoom
acquisition 2020), but the client is open source and an NCC Group security
audit is public.
- [New key model](https://keybase.io/blog/keybase-new-key-model)
- [Per-User Keys](https://book.keybase.io/docs/teams/puk)
- [NCC Group audit](https://keybase.io/docs-assets/blog/NCC_Group_Keybase_KB2018_Public_Report_2019-02-27_v1.3.pdf)

**OpenPGP offline master key + subkeys** — the BURIED/GUARDED/DAILY hierarchy
maps almost directly to the standard GPG best practice of an offline master
that certifies online subkeys. Battle-tested. Sequoia PGP's web-of-trust
implementation models trust evaluation as a max-flow network problem — the
closest existing implementation of configurable trust policy evaluation.
- [Sequoia web of trust](https://sequoia-pgp.gitlab.io/sequoia-wot/)
- [Debian subkeys guide](https://wiki.debian.org/Subkeys)

**KERI** (Key Event Receipt Infrastructure) — fully decentralized identity
with pre-rotation: commit to the hash of your next key before you need it.
If the current key is compromised, rotate to the pre-committed key. Key
events form a hash-chained log verifiable by anyone. Specified as an IETF
Internet-Draft, used by GLEIF.
- [KERI paper (arXiv:1907.02143)](https://arxiv.org/abs/1907.02143)
- [KERI Made Easy (DIF)](https://identity.foundation/keri/docs/KERI-made-easy.html)

**SPKI/SDSI** (Rivest & Lampson, 1990s) — decentralized certificate system
with authorization-centric design and "threshold subjects" (K-of-N policy).
The threshold construct is the closest existing mechanism to Cuttlefish's
relying-party-configurable trust policy. Academically influential but saw
essentially zero real-world deployment.
- [SPKI/SDSI certificate chain discovery](https://people.csail.mit.edu/rivest/pubs/CEEFx01.pdf)

**CONIKS / Key Transparency** — Merkle-tree-based key directory that prevents
a provider from lying about key bindings. Complementary to (not overlapping
with) Cuttlefish's web of trust. Apple has shipped it for iMessage. IETF
standardization in progress.
- [CONIKS paper (USENIX)](https://www.usenix.org/conference/usenixsecurity15/technical-sessions/presentation/melara)
- [IETF keytrans working group](https://datatracker.ietf.org/wg/keytrans/about/)

### What's novel in Cuttlefish

1. **Key age as a positive trust signal.** All existing systems treat old keys
   as purely a liability (longer exposure). The insight that old keys have
   accumulated social proof while new keys have less exposure — and that both
   dimensions matter simultaneously — is not present in any prior system found.

2. **Relying-party-configurable multi-dimensional trust policy.** Policy
   expressions like "certs from old AND new keys from different teams" go
   beyond OpenPGP trust signatures (single scalar) and SPKI/SDSI thresholds
   (K-of-N on a single dimension).

3. **The full combination.** Signal-based E2E encryption + per-user CA
   hierarchy + decentralized web of trust + physical ceremonies + graduated
   multi-dimensional trust. Each pair exists somewhere; nobody has all of them.

---

## Open Questions

These are tracked in `Documentation/open-architecture-questions.md`; summarized here for discoverability.

- **Pairwise channel bucket naming** — how are pairwise bucket names derived? Needs to encode: the two participants, the team (if per-team scoped), and the direction. Must not leak identity correlation for per-team-scoped channels.
- **Sender key rotation frequency** — how often should sender keys rotate in the absence of membership changes? More frequent = smaller blast radius but more pairwise traffic.
- **Key storage format** — how private keys are persisted on-device (OS keychain, secure enclave where available, encrypted file). Determines the concrete threat model for device compromise.
- **Key backup/recovery** — base keys can be re-encrypted under multiple other keys for backup purposes. Mechanism TBD.
- **Cod Sync new-member bootstrapping** — when someone joins a team, can they decrypt historical chain data? This is a forward-secrecy policy question as much as a technical one.
- **Identity key rotation and the BURIED/GUARDED/DAILY spectrum** — identity keys need periodic rotation. A new workhorse key might initially be certified only by a DAILY identity key, with higher-ceremony certification (GUARDED/BURIED) happening when convenient. The recovery property is that having both newer and older keys is stronger than either alone (analogous to the Double Ratchet's post-compromise recovery).
- **Proximity-based trust maintenance** — if hardware supports it, devices could detect each other (Bluetooth LE, NFC, local WiFi) and silently exchange fresh certifications without user interaction. The goal is zero-ceremony trust maintenance for the common case of people who are physically together.
- **Sequence numbers in Cod Sync links** — needed for out-of-order delivery handling. Design must account for multiple apps producing bundles concurrently for the same team.
- **Hub queuing model** — the Hub multiplexes bundles for many apps across many teams. Needs per-app-per-team queues, plus key-dependency queuing for bundles that arrive before their sender key.
- **`member` key/cert schema** — the `member` table in `core.db` currently holds a single Ed25519 public key (placeholder). Will need to accommodate workhorse keys, identity keys, and certificates.
- **SLH-DSA availability** — verify that `cryptography` >= 46 actually ships SLH-DSA; fall back to `liboqs-python` if not.
- **Trust policy primitives** — `trust.py` defers policy to callers. Define common policy building blocks (threshold, weighted, time-decay) once real use cases emerge.

---

## Status

**Implemented (placeholder level):**
- Per-team Ed25519 signing keys generated on team creation and invitation acceptance
- Private keys stored in NoteToSelf `team_signing_key` table (syncs across devices)
- Public keys stored in team DB `member.public_key`
- Cod Sync `push_to_remote` optionally signs links; `canonical_link_bytes` + `verify_link_signature` for verification
- End-to-end test: `test_signed_bundle_roundtrip` demonstrates sign-on-push, verify-on-pull

**All Cuttlefish modules are currently stubs.** The intended build order is:

1. `keys.py` — data model foundation (workhorse + identity key types)
2. `group.py` — sender key distribution and symmetric encryption
3. `ratchet.py` — pairwise Double Ratchet for key distribution channels
4. `identity.py` + `ceremony.py` — cert hierarchy, signing ceremonies, proximity exchange
5. `prekeys.py` + `x3dh.py` — async session initiation for pairwise channels
6. `trust.py` — policy evaluation over the cert graph
