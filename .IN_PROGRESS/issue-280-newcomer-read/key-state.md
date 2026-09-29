# Where sender-key state lives and grows (#264, #280)

Task 92.
Every citation was read in the code at commit 35d8763.
Paths are relative to `packages/`.
"Read only" marks a claim taken from the code without a test that exercises it.

## Summary

Each device keeps one sender chain per team, not one per berth.
Every upload to every berth of the team advances that one chain.
The chain's message keys are kept forever in a JSON column, and nothing prunes them.
The only events that drop key material are a sender-key rotation and a teammate's removal, and both drop it on the receiving side too.

## Storage locations

| Location | What it holds | Writers | Does it shrink? |
|---|---|---|---|
| `team_sender_key` table, `small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql:38-48`. One row per team, primary key `team_id`. | This device's own chain: chain key, iteration, signing private key, `skipped_message_keys` as JSON text. | Hub upload, `small-sea-hub/small_sea_hub/crypto.py:227`. Manager at team creation, invitation acceptance and linked-device finalize, via `provisioning.py:3223-3231`. Manager rotation, `provisioning.py:4340-4342`. | Only by rotation, which replaces the whole row with a fresh chain and an empty dict (read only). |
| `peer_sender_key` table, same file `:50-61`. One row per (team, sender device key), so the device's own key also has a row. | A receiver record: the chain position last seen, plus `skipped_message_keys`. | Hub read, `crypto.py:314`. Manager: `provisioning.py:3226` (own receiver row), `:3037` and `:3058` (linked bootstrap), `:4343` (rotation), `:6139` (invitation acceptance), `:4574` (redistribution receipt). | Rows are deleted for a removed teammate's devices, `provisioning.py:4141-4151`, called at `:4740`. A redistribution receipt or a rotation replaces the row with a fresh record whose dict is empty (`INSERT OR REPLACE`, `note_to_self/sender_keys.py:121`; read only). |
| Serialization, `note_to_self/sender_keys.py:80-91` and `:109-149`. Duplicate in `small-sea-manager/small_sea_manager/sender_keys.py`. | Every save rewrites the whole `skipped_message_keys` JSON for the row. | Same as above. | No. Each save costs time proportional to the number of retained keys. |
| Invitation token, `provisioning.py:5901`, built with `serialize_sender_key_record`. | The inviter's full team record, including every retained own message key. | `create_invitation`. | The token is a one-off artifact. |
| Linked-device bootstrap payload, `provisioning.py:2801-2813`. | Skipped keys of every peer row, keyed by sender device. Not the authorizer's own dict: it sends `own_sender_distribution` only (`:2811`). | `create_linked_device_bootstrap`. | One-off. |
| In-memory only: `group_decrypt` and `_message_key_for` work on copies (`cuttlefish/cuttlefish/group.py:312-350`, `crypto.py:179-202`). | Transient. | | Yes, discarded after the call. |

There is no other store of sender-key or message-key material.
The Hub has no cache of its own.
NoteToSelf has none: `hub/spec.md:531` says it has no group sender key, and Manager opens it only in `passthrough` mode (`small_sea_manager/manager.py:335-349`).
So NoteToSelf traffic is not encrypted by this mechanism at all.

## How an upload advances the chain

1. `upload_to_cloud` (`hub/backend.py:1565-1584`) calls `prepare_encrypted_upload` for `encrypted` sessions.
   The encryption context names team, berth and path, but the key does not depend on berth.
2. `prepare_encrypted_upload` (`crypto.py:205-220`) loads the team key, calls `group_encrypt` (`group.py:212-261`, chain key advances by HMAC, iteration + 1), then adds the message key it just used to `skipped_message_keys` (`crypto.py:217-219`).
   The publisher therefore keeps a key for every object it ever published.
3. `commit_encrypted_upload` saves the row (`crypto.py:223-227`), only if the upload succeeded (`backend.py:1582-1583`).

## How a reader retains keys

`decrypt_group_payload` (`crypto.py:262-315`) calls `group_decrypt`, then re-adds the key for the iteration it just read (`:310-313`) and saves the row.
`group_decrypt` on its own removes an out-of-order key once used (`group.py:320-322`), and stores a key for every skipped step when a message arrives ahead of the chain (`group.py:336-340`).
The Hub undoes the first behavior and keeps the second, so a peer row holds a key for every iteration read and every gap.
An iteration older than the row's position and absent from the dict fails with `No skipped key for iteration N` (`crypto.py:187-193`, `group.py:312-319`).

## How a newcomer gets a key

| Path | What the newcomer receives | Evidence |
|---|---|---|
| Founder | Creates its own chain at iteration 0. | `provisioning.py:5516`. |
| Invitation | The token carries the inviter's whole record, including skipped keys (`:5901`). The acceptor's in-memory bootstrap decrypt uses them (`manager.py:897-908`, `provisioning.py:5120-5160`), so the clone can read pre-invitation objects. What the acceptor stores is only a distribution at the current position (`provisioning.py:5955`, `:6139-6143`). The skipped keys are dropped. | Read only. |
| Linked device | Authorizer's chain at its current position, plus every peer row's current position and skipped keys (`:2789-2813`, `:3034-3062`). | Read only. |
| Redistribution (removal, new device, admission) | A distribution message at the sender's current position, sent over an X3DH channel (`:4360-4361`). Skipped keys are not sent. `receive_sender_key_distribution` replaces the receiver's row (`:4557-4574`). | Test `test_delivered_sender_key_cannot_read_a_link_published_before_it` (`manager/tests/test_core_peer_fetch_encrypted.py:253`). |
| Rotation | `rotate_team_sender_key` creates a new chain and replaces both the team row and the device's own peer row (`:4333-4348`). Old-chain keys are gone from the sender. | Read only. |

## Observations for the owner

1. The invitee already receives the keys #280 says are missing, and throws them away.
   Persisting them is a small change (`:5955` builds a distribution; the token holds a full record).
   The Hub-level tests hand the newcomer `skipped_message_keys={}` (`hub/tests/test_publication_routes.py:478`, `test_group_crypto.py:331`), which models redistribution, not the invitation token.
2. Rotation and redistribution drop old-chain keys on both sides (read only).
   After a teammate is removed, every remaining device probably loses the ability to re-read what it read under the old chain.
   Nothing pins this yet.
3. The linked-device bootstrap ships peer rows from `load_all_peer_sender_keys`, which includes the authorizer's own receiver row (`:3226`).
   That row starts at iteration 0 and advances only when the device reads its own objects.
   If it lags, the new device could derive the authorizer's whole history, which would contradict #280's linked-device account.
   Unresolved: needs a probe before anyone relies on either reading.
4. Growth is about 80 bytes of JSON per upload, per device per team, rewritten in full on every save, and copied whole into each invitation token.
5. Message-key logic is duplicated in `provisioning.py:5099-5117` and `crypto.py:179-202`, and the serializer is duplicated in Manager and NoteToSelf packages.
6. #264 cites `crypto.py:78-80` and `:105-107`, which no longer match; the retention is now at `crypto.py:217-219` and `:310-313`.
