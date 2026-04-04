> Migrated to GitHub issue #4.

---
id: 0017
title: Cuttlefish integration with Hub and Manager
type: design
priority: high
---

## Context

All eight Cuttlefish modules are now implemented with working crypto:
X3DH → Double Ratchet → Sender Keys → group encryption, plus keys,
identity, ceremony, and trust. The next step is integrating this with the
Hub (cloud storage gateway) and Manager (key lifecycle).

The high-level split is decided (see Cuttlefish README § "Hub, Manager,
and Cuttlefish Responsibilities"):
- **Hub**: sender-key encrypt/decrypt for team broadcast sessions;
  passthrough for pairwise/key-management sessions. Determined by a flag
  set at session creation time.
- **Manager**: owns all key lifecycle — generation, rotation, distribution,
  certification. Handles Double Ratchet operations for pairwise channels.
- **Apps**: crypto-unaware; talk to Hub, which handles encryption
  transparently.

## Open Architecture Questions

### 1. Where does Cuttlefish state live?

The current `team_signing_key` (NoteToSelf) and `member.public_key`
(team DB) are placeholders. Full Cuttlefish integration needs storage for:

| State | Scope | Syncs? | Current location |
|-------|-------|--------|-----------------|
| Identity key hierarchy (BURIED/GUARDED/DAILY) | Per-participant, cross-device | Yes (NoteToSelf) | `team_signing_key` (placeholder) |
| Own sender key (chain state) | Per-participant, per-team | Yes (NoteToSelf) | Not yet |
| Peers' sender keys | Per-team | Yes (team DB) | Not yet |
| Pairwise ratchet state | **Per-device** | **No** | Not yet |
| Prekey bundles (own private keys) | Per-device | No | Not yet |
| Peers' prekey bundles (public) | Per-team | Yes (team DB or cloud) | Not yet |
| Cert graph (certificates, revocations) | Per-team, public | Yes (team SmallSeaCollectiveCore) | Not yet |

**Key question: pairwise ratchet state is device-local.** This is the
first piece of data in Small Sea that is device-specific and does not
sync. Everything else either syncs via NoteToSelf or via team stations.
Where does this live? Options:
- A new device-local SQLite DB outside the NoteToSelf station
- A table in NoteToSelf that is explicitly excluded from sync
- A separate "device state" directory alongside NoteToSelf

### 2. Hub's crypto surface

The Hub should not touch raw key material. Proposed: a `CryptoSession`
adapter that wraps sender key state and exposes only:
- `encrypt(plaintext) -> ciphertext`
- `decrypt(ciphertext) -> plaintext`

The Hub creates this adapter when it needs to encrypt/decrypt, reads
the current sender key from the team DB (or NoteToSelf), and delegates.
This keeps the Hub's crypto dependency minimal (`cuttlefish.group` only).

**Lazy vs eager key loading**: sender keys can rotate mid-session, so
the Hub should look up the current key when needed, not cache it from
session creation time.

### 3. Ratchet operation ownership

The Manager owns all ratchet operations (sender key distribution,
rotation, X3DH session initiation). These are infrequent and triggered
by SmallSeaCollectiveCore changes. After a rotation, the Manager
updates the relevant key tables; the Hub picks up the new sender key
on its next encrypt/decrypt.

The Hub never needs to know about Double Ratchet or X3DH — it just
needs the current sender key for each team.

### 4. Session creation changes

`SmallSeaSession` needs a field to distinguish encrypted (broadcast)
from passthrough (pairwise) sessions. Options:
- `encrypted: bool` on the session row
- Implicit from session type / channel type

The Hub reads this flag and decides whether to apply sender-key crypto
or pass bytes through.

### 5. Schema evolution

New tables needed (approximate):

**NoteToSelf (syncs across devices):**
```sql
-- Replace team_signing_key with richer identity key storage
CREATE TABLE identity_key (
    id          BLOB PRIMARY KEY,
    team_id     BLOB NOT NULL,
    key_id      BLOB NOT NULL,      -- SHA-256(public_key)[:16]
    public_key  BLOB NOT NULL,
    private_key BLOB NOT NULL,
    protection_level TEXT NOT NULL,  -- DAILY/GUARDED/BURIED
    parent_key_id BLOB,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (team_id) REFERENCES team(id)
);

-- Own sender key per team (chain state)
CREATE TABLE own_sender_key (
    id          BLOB PRIMARY KEY,
    team_id     BLOB NOT NULL,
    chain_id    BLOB NOT NULL,
    chain_key   BLOB NOT NULL,
    iteration   INTEGER NOT NULL,
    signing_public_key  BLOB NOT NULL,
    signing_private_key BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (team_id) REFERENCES team(id)
);
```

**Team DB (syncs via team station):**
```sql
-- Peers' sender keys (received via pairwise channels)
CREATE TABLE peer_sender_key (
    id          BLOB PRIMARY KEY,
    member_id   BLOB NOT NULL,
    chain_id    BLOB NOT NULL,
    chain_key   BLOB NOT NULL,
    iteration   INTEGER NOT NULL,
    signing_public_key BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (member_id) REFERENCES member(id)
);
```

**Device-local (does NOT sync):**
```sql
-- Pairwise ratchet state (per peer, per team or shared)
CREATE TABLE pairwise_ratchet (
    id              BLOB PRIMARY KEY,
    peer_member_id  BLOB NOT NULL,
    team_id         BLOB,           -- NULL if shared across teams
    dh_public_key   BLOB NOT NULL,
    dh_private_key  BLOB NOT NULL,
    dh_remote_public_key BLOB,
    root_key        BLOB NOT NULL,
    sending_chain_key BLOB,
    receiving_chain_key BLOB,
    sending_message_index INTEGER NOT NULL,
    receiving_message_index INTEGER NOT NULL,
    previous_sending_chain_length INTEGER NOT NULL,
    skipped_keys    BLOB            -- JSON or msgpack serialized
);

-- Own prekey private keys (consumed after use)
CREATE TABLE prekey_private (
    prekey_id   BLOB PRIMARY KEY,
    private_key BLOB NOT NULL,
    created_at  TEXT NOT NULL
);
```

### 6. PQ crypto (deferred)

Post-quantum extensions (ML-KEM for key agreement, ML-DSA/SLH-DSA for
signatures) are a separate effort. The current Ed25519/X25519
implementation is structurally complete; PQ will be layered on without
changing the module APIs. Track separately.

## References

- `packages/cuttlefish/README.md` — design spec, §"Hub, Manager, and
  Cuttlefish Responsibilities"
- `packages/small-sea-hub/small_sea_hub/backend.py` — Hub session and
  cloud storage code
- `packages/small-sea-manager/small_sea_manager/provisioning.py` —
  Manager key generation and invitation flow
- Issue 0007 — identity model design progress
- Issue 0008 — Cuttlefish implementation tasks
