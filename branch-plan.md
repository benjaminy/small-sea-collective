# Branch Plan

## Goal

Follow up on GitHub issues `#4` and `#14` by getting Small Sea to a first
demo-grade crypto milestone:

- applications can open a normal team session through the Hub
- data in that session is actually encrypted/decrypted with `cuttlefish`
- the Manager persists enough key material that this flow survives past a toy in-memory demo

This branch is about getting one real encrypted path working end to end, not about finishing the full Small Sea identity vision.

## Concrete Milestone

At the end of this branch, a normal team app session should be able to use real sender-key encryption through the Hub.

Concretely:

- the Manager provisions and stores the key material needed for encrypted team sessions
- the Hub knows which sessions are encrypted and applies `cuttlefish.group`
- an app-level integration test proves that one participant can push encrypted data and another can pull and decrypt it

## Scope For This Branch

Implement now:

- encrypted **team broadcast** sessions
- persisted sender-key state for "my key" and "peer keys"
- a minimal bridge from Manager-managed state to Hub encryption/decryption

Defer for later:

- full X3DH / Double Ratchet integration with Manager workflows
- automated sender-key distribution over pairwise channels
- full `wrasse-trust` web-of-trust integration
- multi-device key transfer and revocation flows
- post-quantum variants

## Planned Shape

1. Add minimal persistent storage for sender-key state.
   - own sender key state lives with participant-owned data
   - peer sender key state lives with team-shared data
2. Teach Manager provisioning/invitation flows to create enough initial key material that a team can start using encrypted sessions immediately.
3. Teach the Hub session layer which sessions are encrypted vs passthrough.
4. Add a small Hub-side crypto adapter around `cuttlefish.group` so apps still hand the Hub plaintext and receive plaintext.
5. Prove the path with an end-to-end test that exercises real Hub-mediated encryption for a normal app/team session.

## Temporary Simplifications

To keep this branch concrete, it is acceptable if the initial sender-key provisioning is more direct and less elegant than the final architecture.

That means:

- no compatibility shims
- no attempt to hide all future schema churn
- no need to solve trust-path policy before encrypted sessions exist
- it is fine if the first version bootstraps keys during invitation/setup rather than through the eventual pairwise ratchet flow

## Validation

The branch is successful if all of the following are true:

- `cuttlefish` micro tests still pass
- existing signing/bundle tests still pass or are updated to the new storage shape
- a new integration test demonstrates encrypted Hub session roundtrip for a normal team app workflow
- the encrypted roundtrip test would fail if Hub encryption/decryption were bypassed

## Resolved Design Decisions

- **What gets encrypted**: everything the Hub sends to the internet (cloud
  storage uploads, signal files). The only exception is NoteToSelf sessions,
  which have no team peers. ntfy push notifications are left plaintext for now
  (third-party service, different trust model).
- **Group granularity**: one sender key group per team. The team ID (UUIDv7
  from NoteToSelf) is used as `group_id`. Apps sharing a team share the same
  crypto infrastructure; berth machinery handles app isolation.
- **Key storage**: all key material lives in the participant's own NoteToSelf
  `core.db` — both own sender keys and peer sender keys. Each member keeps
  their own copy of peer key state. This means slight redundancy but keeps
  secrets in the least shared place.
- **Hub key access**: the Hub reads sender key state directly from the
  NoteToSelf `core.db` via SQLite. The DB files are already the contract
  between Manager and Hub.
- **Encrypted by default**: all team sessions are encrypted. No per-session
  or per-berth opt-in flag needed for this branch.
- **Sender key distribution security**: each participant has a static X25519
  encryption keypair. For the initial bootstrap (invitation), sender key
  distribution messages travel plaintext in the out-of-band invitation/
  acceptance tokens. The static X25519 keys are infrastructure for future
  sender key rotation over cloud channels.

---

## Implementation Steps

### Step 1 — Schema Changes

**NoteToSelf `core.db`** (in `core_note_to_self_schema.sql`):

Add three tables:

```sql
-- Static X25519 keypair for encrypting sender key distribution messages.
-- One row per participant (like user_device).
CREATE TABLE IF NOT EXISTS encryption_key (
    id          BLOB PRIMARY KEY,
    public_key  BLOB NOT NULL,   -- 32-byte X25519
    private_key BLOB NOT NULL    -- 32-byte X25519
);

-- Own sender key chain, one per team.
CREATE TABLE IF NOT EXISTS team_sender_key (
    id                  BLOB PRIMARY KEY,
    team_id             BLOB NOT NULL,
    chain_id            BLOB NOT NULL,   -- 32-byte random chain generation ID
    chain_key           BLOB NOT NULL,   -- 32-byte current chain key
    iteration           INTEGER NOT NULL DEFAULT 0,
    signing_public_key  BLOB NOT NULL,   -- 32-byte Ed25519
    signing_private_key BLOB NOT NULL,   -- 32-byte Ed25519
    FOREIGN KEY (team_id) REFERENCES team(id)
);

-- Peer sender keys, one row per peer per team.
CREATE TABLE IF NOT EXISTS peer_sender_key (
    id                 BLOB PRIMARY KEY,
    team_id            BLOB NOT NULL,
    member_id          BLOB NOT NULL,    -- peer's member ID in the team
    chain_id           BLOB NOT NULL,
    chain_key          BLOB NOT NULL,
    iteration          INTEGER NOT NULL DEFAULT 0,
    signing_public_key BLOB NOT NULL,
    skipped_keys       TEXT,             -- JSON dict: {iteration: hex(message_key)}
    FOREIGN KEY (team_id) REFERENCES team(id)
);
```

**Team DB** (in `core_other_team.sql`):

Add `encryption_public_key` column to the `member` table:

```sql
CREATE TABLE IF NOT EXISTS member (
    id                    BLOB PRIMARY KEY,
    public_key            BLOB,          -- Ed25519 signing (existing)
    encryption_public_key BLOB           -- X25519 static encryption
);
```

Bump schema versions accordingly.

### Step 2 — Key Generation in Provisioning

In `provisioning.py`:

- **`create_participant`**: generate an X25519 static encryption keypair and
  store it in the `encryption_key` table in NoteToSelf.
- **`create_team`**: call `cuttlefish.group.create_sender_key(team_id,
  member_id)` and store the resulting `SenderKeyRecord` in `team_sender_key`.
  Also store the participant's X25519 public key in the team DB
  `member.encryption_public_key`.
- **`accept_invitation`**: same as `create_team` — generate a sender key for
  the new team and store the encryption public key in the member row.

### Step 3 — Sender Key Exchange During Invitation

The invitation and acceptance tokens are already exchanged out-of-band
(manual copy-paste). Sender key distribution piggybacks on this exchange.

**Invitation token** adds a `sender_key_distribution` field containing the
`SenderKeyDistributionMessage` fields serialized as hex:

```json
{
  "...existing fields...",
  "sender_key_distribution": {
    "chain_id": "<hex>",
    "iteration": 0,
    "chain_key": "<hex>",
    "signing_public_key": "<hex>"
  }
}
```

**`accept_invitation`**:
1. Reads inviter's sender key distribution from the token.
2. Stores it as a `peer_sender_key` row in NoteToSelf (keyed by team_id +
   inviter_member_id).
3. Generates own sender key for this team.
4. Includes own `sender_key_distribution` in the acceptance token.

**Acceptance token** adds the same `sender_key_distribution` field.

**`complete_invitation_acceptance`**:
1. Reads acceptor's sender key distribution from the acceptance token.
2. Stores it as a `peer_sender_key` row in NoteToSelf.

After this exchange both participants have each other's sender keys and can
immediately encrypt/decrypt team data through the Hub.

### Step 4 — Hub Crypto Adapter

New module: **`small_sea_hub/crypto.py`**

Core functions:
- `encrypt_for_upload(nts_db_path, team_id, member_id, plaintext) → bytes`
  — reads own `team_sender_key`, calls `cuttlefish.group.group_encrypt`,
  persists the updated chain state, returns serialized `GroupMessage`.
- `decrypt_from_download(nts_db_path, team_id, ciphertext) → bytes`
  — deserializes the `GroupMessage`, reads the appropriate `peer_sender_key`
  (keyed by `message.sender_participant_id`), calls
  `cuttlefish.group.group_decrypt`, persists updated chain state, returns
  plaintext.

**`GroupMessage` wire format**: binary with a version byte followed by
length-prefixed fields (sender_participant_id, sender_chain_id, iteration,
iv, ciphertext, signature). Machine-to-machine on cloud storage, no need for
human readability.

**Integration points in `backend.py`**:
- `upload_to_cloud`: if the session is not NoteToSelf, encrypt before calling
  the storage adapter.
- `download_from_cloud`: if the session is not NoteToSelf, decrypt after the
  adapter returns.
- `download_from_peer`: decrypt using the peer's sender key (the
  `GroupMessage` envelope identifies the sender).
- NoteToSelf sessions: bypass encryption (no team peers, single participant).

### Step 5 — Validation (Vault as Guinea Pig)

Integration test exercising the full encrypted path:

1. **Setup**: create two participants (Alice, Bob) each with cloud storage
   configured. Alice creates a team.
2. **Invitation**: Alice invites Bob. The invitation token carries Alice's
   sender key distribution. Bob accepts; the acceptance token carries Bob's
   sender key distribution. Alice completes the acceptance. Both now have
   each other's sender keys in NoteToSelf.
3. **Vault operation**: Alice creates a Vault niche in the team, publishes a
   file through the Hub. The Hub encrypts the upload with Alice's sender key.
4. **Peer pull**: Bob pulls the niche through the Hub. The Hub downloads from
   Alice's cloud bucket and decrypts using Alice's sender key (stored as
   Bob's `peer_sender_key`).
5. **Assertions**:
   - Bob's decrypted content matches what Alice published.
   - The raw bytes on cloud storage are ciphertext (not plaintext).
   - The test would fail if Hub encryption/decryption were bypassed.

### Step 6 — Existing Test Updates

- Cuttlefish micro tests should continue to pass unchanged.
- Existing signing/bundle tests may need updates to account for the new
  `encryption_public_key` column in the member table and the new NoteToSelf
  tables.
- Existing invitation tests need updates to include sender key distribution
  in the tokens.

---

## Deferred for Later

- Full X3DH / Double Ratchet pairwise channels
- Encrypted sender key rotation over cloud (using static X25519 keys)
- ntfy notification payload encryption
- Multi-device key transfer and revocation
- Post-quantum key variants
- `wrasse-trust` web-of-trust integration
- Per-berth encryption configuration (everything encrypted is the default)
