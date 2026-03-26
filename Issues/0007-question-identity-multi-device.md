---
id: 0007
title: Settle identity model for NoteToSelf and multi-device
type: question
priority: medium
---

## Context

Of the five open architecture questions tracked in `Documentation/open-architecture-questions.md`, the identity/multi-device story is the least resolved. The others (encryption layer shape, Hub↔Manager DB contract, session lifecycle, Cod Sync chain format) are mostly settled.

## Design Progress (2026-03)

The crypto architecture is converging on an adaptation of Signal's group
messaging protocol to Small Sea's serverless store-and-forward model. Key
decisions so far:

### Signal Group Messaging Adaptation

- **Sender keys** for team broadcast: each member encrypts once with a
  symmetric sender key; all teammates can decrypt. Same as Signal's Sender
  Keys protocol.
- **Pairwise Double Ratchet channels** for distributing sender keys, identity
  key certifications, and membership-change notifications. These are
  implemented as lightweight bucket pairs (same Cod Sync mechanics as team
  buckets, just used infrequently).
- **Asymmetric signatures** on every bundle (Ed25519, currently implemented)
  prevent impersonation even though all group members hold each other's
  sender keys.

### Two-Tier Key Architecture

- **Workhorse keys**: per-participant, per-device, per-team. Stored in secure
  enclaves where available. Used for actual encrypt/sign operations.
  Transient — not the primary locus of identity.
- **Identity keys**: per-participant, per-team, cross-device. Stored in
  NoteToSelf/SmallSeaCollectiveCore (syncs across devices). Certify workhorse
  keys. The BURIED/GUARDED/DAILY protection-level spectrum applies here.

### Cross-Team Deniability (Flexible)

Pairwise channels can be per-team (no cryptographic correlation between teams)
or shared across teams (simpler, fewer buckets). The choice is per-pair, not
global. Per-team is the default for deniability; shared is opt-in for
convenience.

### Key Lifecycle Coordination

Key events (rotations, membership changes, certifications) are announced via
`{Team}/SmallSeaCollectiveCore`. Actual secret material flows only over
pairwise ratcheted channels.

### Trust Maintenance

The common-case ceremony should be effortless. Proximity-based automatic
certification (Bluetooth LE, NFC, local WiFi) when devices are physically
near each other. Higher-ceremony operations (GUARDED/BURIED keys) are
infrequent and manual.

Identity trust evolves over time — having both older and newer keys is
stronger than either alone (analogous to Double Ratchet post-compromise
recovery).

## Implemented So Far

- Per-team Ed25519 signing key pairs (placeholder level)
- Private keys in NoteToSelf `team_signing_key` table
- Public keys in team DB `member.public_key`
- Signed Cod Sync links with `canonical_link_bytes` + `verify_link_signature`
- End-to-end test: `test_signed_bundle_roundtrip`

## Remaining Open Questions

- How does a user establish identity on a new device? (Key import? QR code? PIN-based device link?)
- What is the exact relationship between the NoteToSelf station and per-device keys?
- When a new device joins, what does it get access to retroactively?
- How are device revocations handled?
- Pairwise channel bucket naming scheme (must not leak cross-team identity for per-team-scoped channels)
- Sender key rotation frequency in the absence of membership changes
- Sequence numbers in Cod Sync links for out-of-order delivery
- Hub queuing model for multi-app, multi-team bundle delivery

## UI consequence: member display names

The team DB `member` table only carries `id` (BLOB) and `public_key`. There
is no `display_name` column and no way to resolve a member ID to a human name
at the team-DB level. The Manager web UI currently shows truncated hex IDs
(e.g. `01234567abcd…`) in the member list, which is unusable in practice.

The fix requires a design decision: where do display names live?

- **Option A — self-reported in team DB:** Add a `display_name` column to
  `member`; each participant writes their own name when they join. Simple but
  unauthenticated (anyone can claim any name).

- **Option B — local contacts in NoteToSelf:** The viewing participant stores
  a contacts table in NoteToSelf mapping known `member_id` → nickname. Only
  affects their own view; no coordination needed. Requires importing/learning
  names out-of-band.

- **Option C — identity key certification carries a name:** The certified
  identity key includes a human-readable claim; members verify the name via
  the same trust path as the key. Correct but depends on Cuttlefish
  integration (0008).

Option A is quickest to ship and covers the common case. Option C is the
right long-term answer. Option B is a useful interim if A is considered too
easy to spoof.

## References

- `packages/cuttlefish/README.md` — full design spec (sections: Signal Group Messaging Adaptation, Two Tiers of Keys, Key Dimensions, Web of Trust)
- `Documentation/open-architecture-questions.md` — section 5: Identity Model
- `packages/cuttlefish/cuttlefish/identity.py` — current identity implementation (stub)
- `packages/small-sea-manager/tests/test_signed_bundles.py` — signing end-to-end test
