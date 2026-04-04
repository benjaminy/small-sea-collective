> Migrated to GitHub issue #14.

---
id: 0008
title: Implement Cuttlefish cryptography (ratchet, sender keys, signing)
type: task
priority: low
---

## Context

Cuttlefish's Signal Protocol implementation is intentionally stubbed out. Per WIP.txt this is explicitly deprioritized until the system demo is working. When the time comes, there are several concrete gaps to fill.

## Work to do

- `RatchetState`: define full state (root key, chain keys, ratchet key pair, etc.) — currently an opaque dict placeholder (`ratchet.py:29`)
- `EncryptedMessage`: implement authenticated header encryption (Signal spec section 3.5) (`ratchet.py:39`)
- `SenderKeyRecord`: define (chain key, signing key, iteration) — currently an opaque dict (`group.py:26`)
- `sign_cert()`: define the canonical byte representation that is signed (`identity.py:53`)
- Wire up actual PQXDH / Double Ratchet / Sender Keys per the README spec

## References

- `packages/cuttlefish/cuttlefish/ratchet.py:29,39`
- `packages/cuttlefish/cuttlefish/group.py:26`
- `packages/cuttlefish/cuttlefish/identity.py:53`
- `packages/cuttlefish/README.md` — full cryptographic design spec
