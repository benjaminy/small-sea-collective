# Options for newcomer reads (#280)

Task 93.
A draft for the owner to decide.
Facts come from `key-state.md` in this directory.

## The problem is wider than newcomers

A reader can decrypt an object only if it holds that object's message key.
A reader given a sender key at step N can derive steps N and later, never earlier.
Three tests pin this:

- `test_newcomer_cannot_read_a_message_published_before_it_joined` (Hub crypto).
- `test_a_newcomers_cod_sync_fetch_fails_on_a_link_published_before_it_joined` (Hub routes).
- `test_delivered_sender_key_cannot_read_a_link_published_before_it` (Manager).

The third shows the same failure inside one team: after Bob's key is redistributed to Alice, Alice still cannot read Bob's earlier Core links.
So option (a) does not just inconvenience newcomers.
It blocks a two-way read between existing teammates whenever the key is handed over late.

The sender chain is per team, not per berth.
Any mechanism that hands over keys hands over every berth's history at once.

## What each berth type needs

- **Core.** Git link chain, small, read by every member. Needs history.
- **NoteToSelf.** Not encrypted today: `passthrough` sessions, no sender key. Options (a) to (d) do not apply, and (e) describes the status quo.
- **App berths such as Files.** Objects of any size. Entitlement may differ from Core.

## Options

**(a) Post-join reads only.**
Code: none. Forward secrecy: unchanged.
Core: fails for the inviter reading the invitee (pinned) and probably for linked devices.
Files: newcomers see nothing old.
Fine as a stated rule for Files, if that is the decision. Not viable for Core.

**(b) Snapshot inside the join artifact.**
Code: exists for linked devices (#235). Extending it to invitations means putting a Core bundle into the acceptance record.
Cost: size grows with history, once per join.
Forward secrecy: unchanged, since no new key material moves.
Core: works for the joiner. It does not help a teammate who reads the joiner's earlier links, because no artifact travels that way.
Files: no. A bundle of arbitrary app data does not fit in a signed token.

**(c) Hand over history keys.**
The publisher already keeps every own message key: `prepare_encrypted_upload` adds each one to `skipped_message_keys` (`crypto.py:217-219`).
The invitation token already carries them (`provisioning.py:5901`), and the acceptor drops them (`:5955`).
The linked-device bootstrap ships peers' dicts but not the authorizer's own.
Redistribution sends only the current position (`:4361`).
Code: persist the dict at acceptance, include it in the linked bootstrap's own record, send it in redistribution, and stop rotation from discarding old-chain keys.
The last part needs a schema change: rows are keyed by device, so a new chain replaces the old one.
Cost: about 80 bytes per upload, plus one full-JSON rewrite per save.
Forward secrecy: no loss beyond today, since keys are already kept forever. The handed-over dict lets the recipient read all past objects if it can also fetch them.
Core and Files: both work, with one entitlement for all berths.
This also settles #264 in the direction of stating the retention as the design.

**(d) Re-publish after the newcomer joins.**
Code: the publisher re-encrypts history and rewrites each object. The context binds the path, and the Cod Sync link chain would need rebuilding.
Cost: the whole history, once per join, and the publisher must be online.
Forward secrecy: unchanged. Old ciphertext stays in the cloud, readable by anyone who kept the old keys.
Core: awkward. Files: expensive. Weakest option on cost.

**(e) Publish unencrypted.**
Simplest code. The cloud provider reads everything, which removes the reason for the sender-key design.
Only NoteToSelf lives there today.

## Recommendation

(c) looks strongest for Core and for small app berths.
It reuses state that already exists, covers the two-way case that (b) cannot, and needs no re-encryption.
Keep (b) for linked-device Core, where it already works.
Use (a) for large app berths only if the owner decides they need no history.
Drop (d) and (e).

Not on the list: replace the chain with a random key per object, wrapped to each member's key.
That fits repeated reads, but it is a new construction and belongs with #264.

## Caveats

- Rotation after a teammate's removal appears to drop old-chain keys on both sides (read only). Under (c) that must change, or history is lost at every removal.
- Whether linked devices already receive the authorizer's full history through the peer-row bootstrap is unresolved (`key-state.md`, observation 3).

## First question for the owner

Is joining a team meant to grant the full history of every berth, or only what is published after joining?
If the answer differs by berth, (c) cannot express it, because one chain covers the whole team.
Then Core gets (c) and each other berth type needs its own choice.
