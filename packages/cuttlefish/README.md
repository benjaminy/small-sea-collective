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
- **Sender Keys** — efficient group messaging. Each group member publishes a
  sender chain; messages are encrypted once and are decryptable by all current
  members.

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

This is related to (and should build on, if usable) prior work on:
- TOFU + key continuity (as in SSH)
- CONIKS / Key Transparency
- The Signal Safety Number / fingerprint comparison UX
- Keybase's social proof model (though Cuttlefish has no central keybase-style
  server)

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

## Open Questions

These are tracked in `Documentation/open-architecture-questions.md`; summarized here for discoverability.

- **Key storage format** — how private keys are persisted on-device (OS keychain, secure enclave where available, encrypted file). Determines the concrete threat model for device compromise.
- **Key backup/recovery** — base keys can be re-encrypted under multiple other keys for backup purposes. Mechanism TBD.
- **Cod Sync new-member bootstrapping** — when someone joins a team, can they decrypt historical chain data? This is a forward-secrecy policy question as much as a technical one.
- **`member` key/cert schema** — the `member` table in `core.db` has a placeholder for key/cert material; contents TBD pending Cuttlefish key model stabilizing.
- **SLH-DSA availability** — verify that `cryptography` >= 46 actually ships SLH-DSA; fall back to `liboqs-python` if not.
- **Trust policy primitives** — `trust.py` defers policy to callers. Define common policy building blocks (threshold, weighted, time-decay) once real use cases emerge.

---

## Status

All modules are currently **stubs**. The intended build order is:

1. `keys.py` — data model foundation
2. `identity.py` + `ceremony.py` — get signing ceremonies working end-to-end
3. `prekeys.py` + `x3dh.py` — async session initiation
4. `ratchet.py` — per-message forward secrecy
5. `group.py` — group sender keys
6. `trust.py` — policy evaluation over the cert graph
