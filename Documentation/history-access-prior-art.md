# Reading History Encrypted Before You Held the Key

How other projects let a new group member read what the group encrypted before that member held the key, and what that suggests for Small Sea.
Researched 2026-11-24 from the sources listed at the end.
Where a source does not say something outright, this note marks the claim as inference.

## The problem in Small Sea

Cuttlefish encrypts each berth publication with the publishing device's Sender Key, a ratchet whose chain key moves forward with every message and cannot move back.
A teammate who receives that Sender Key at iteration *n* can decrypt iteration *n* and later, but nothing earlier.

Cod Sync stores history as a chain of links that anyone with access can read again later.
A fetch walks back from the newest link until it reaches a link whose head the reader already has (`cod_sync/protocol.py`, the walk in `fetch`).
A reader who has none of a teammate's commits walks all the way back to that teammate's first link.

The gap shows up in both directions when Alice invites Bob:

- Bob cannot read Alice's history published before he joined.
  This is #280.
- Alice cannot make a first fetch of Bob's chain.
  Bob publishes as soon as he joins, and Alice receives his Sender Key a step later, through the Hub's runtime watch.
  A probe on 2026-11-24 delivered Bob's key through the watcher, had Bob publish again, and then had Alice fetch.
  Alice decrypted Bob's newest link, then failed on his first link with "No skipped key for iteration 1".
  Bob's later publications do not help, because the walk still needs his first link.

#264 names the underlying mismatch: a ratchet built for messages read once, used for storage read many times.

## Two families of designs

**Message streams** (Signal, Matrix, MLS) deliver each message once and treat "new members cannot read earlier messages" as a security property.
Groups that later wanted to share history had to add it on top.

**Shared stores** (Keybase, Secure Scuttlebutt private groups, p2panda's data scheme, Keyhive) encrypt stored data with keys that do not ratchet per message.
They rotate keys when membership shrinks, and they hand new members the old keys, or a key that unlocks the old keys.
Each of them gave up forward secrecy for stored data, and says so.

Cod Sync's chain is a shared store, so the second family is the closer fit.

## Message streams

### Signal (Sender Keys)

Each member generates a sender chain key and signing key and sends them to every other member over their pairwise sessions (Balbás et al.'s description of Sender Keys).
Group messages are then encrypted under that chain.
The academic treatments list "new members cannot read past messages" as a requirement, not a limitation.

Signal avoids our failure because a message is delivered once and nobody walks back through earlier ones.
Inference: the sender also sends its distribution message before the first group message that uses it, so a recipient never holds ciphertext from before its key.
In Small Sea, Bob's earliest links exist in storage before his key reaches Alice.

### Matrix (Megolm)

Megolm, like Sender Keys, is a per-sender ratchet, so a device that receives a session at index *n* cannot decrypt earlier messages.
MSC3061 states the result: rooms whose history visibility is `shared` or `world_readable` "effectively" behave like `invited`, because members generally do not send new members the keys for earlier messages.

MSC3061 proposed sharing those keys from the session's first index, marked with a `shared_history` flag.
The sender sets the flag from its own view of the room's visibility when it sends, because a malicious server could misreport the visibility later.
MSC4268, "Sharing room keys for past messages", superseded MSC3061 and was merged on 2026-04-08.
The Element team describes the changes: keys go only to verified devices, packed into one blob and uploaded as an encrypted attachment.
The Matrix v1.19 release notes (2026-07-08) list room history sharing from MSC4268.

Lesson: retrofitting history onto a ratchet took Matrix several years, and the result is "send the newcomer the old keys", not a change to the ratchet.

### MLS (RFC 9420)

A member joins at the current epoch and receives that epoch's secrets.
Earlier epochs stay closed to them; sharing history is outside the core protocol.
MLS also assumes a Delivery Service that orders handshake messages, which Small Sea does not have.
We did not research MLS history-sharing drafts or decentralized MLS variants for this note.

## Shared stores

### Keybase teams

Each team has generations of per-team keys, each derived from a random 32-byte seed.
"Whenever the key rolls over, the previous seed s_i is encrypted with c_i+1", where c is a symmetric key derived from the newer seed.
When a member is added, the current seed is encrypted for that member's per-user key.
Keys rotate when a member revokes a device, is reset, leaves, or is removed, and the rotation "can happen lazily".
Adding a member does not rotate the key.
Old chats are not re-encrypted after a rotation.

Inference: since each old seed is boxed under the next generation's key, a member who holds the current seed can unbox every earlier seed, so a new member can read all earlier generations.
Keybase's team chat documentation says new members can read the whole chat history, except exploding messages, which is consistent with this.
Keybase's encrypted git (2017) is end-to-end encrypted for the team, but the blog post does not say which keys encrypt repository data or whether new members read old repository history.

Fit: Keybase is the closest analog to Small Sea's encrypted Git chains.
The "previous seed boxed under the next key" chain gives a newcomer all of history from one current key.

### Secure Scuttlebutt private groups

Each group epoch has one symmetric key.
The add-member message carries `secret`, "the symmetric key for this epoch", and `oldSecrets`, "an array of all secrets of all predecessor epochs to this epoch, all the way back to the root epoch".
The exclusion spec says that when adding someone you "MUST add them to all the epochs in the history of the group, starting with epoch 0", including epochs on forks that lost, because "content may have been published in these forks".

Removing a member creates a new epoch with a new key, given to everyone except the removed member.
The removed member keeps every message from before the removal.
The spec says forward secrecy was "intentionally left out".

Two members can exclude others concurrently and fork the epoch.
The exclusion spec resolves forks with fixed rules: prefer an epoch that includes you, prefer the smaller membership when one is a subset of the other, break ties by the lexicographically smallest epoch key, and create a new epoch when memberships overlap.
The spec admits the tie-break "can be gamed" by brute-forcing epoch IDs, and the ssb-tribes2 README calls its recovery "a bit unreliable".

Fit: SSB also has no central server and an append-only store that members replicate.
Its epoch fork rules are worth reading next to the Constitution spine's handling of concurrent membership changes.

### p2panda-encryption

The crate offers two schemes.
The data scheme encrypts with "a secret, symmetric key for a group".
The message scheme is "a forward secure (FS) messaging ratchet, similar to Signal's Double Ratchet algorithm".

In the data scheme, "users who enter a group late will still have access to previously-created content".
A new member receives "the whole secret bundle" in a welcome message and "will be able to decrypt all previously-created content".
A new group secret is generated when a group is created or updated, or a member is removed.
Forward secrecy is optional: applications may drop old secrets.

The crate leaves group membership and message ordering to the application.
The message scheme needs stricter ordering than the data scheme because of its forward secrecy.

Fit: this is the split #264 points toward, already built: long-lived group secrets for stored data, a ratchet only for messages.

### Keyhive (Ink & Switch)

Keyhive encrypts ranges of Automerge operations as chunks, and each chunk carries "the keys to all of their causal predecessor chunks".
"If you can decrypt a chunk, you will gain access to the key for decrypting the previous chunk."
They call this causal encryption.
It "sacrifices forward secrecy" and "retains secrecy of concurrent and future chunks".
They accept the cost because Automerge needs the whole causal history to render a document.

Group key agreement uses BeeKEM, a TreeKEM variant that needs only a causal order of operations, not a server.
Removing a member blanks that member's path in the key tree, and removed members cannot read future chunks.
They state the limit plainly: "if someone has the data and the symmetric key, then they have the ability to read that data."
A 2026 entry says Keyhive as a whole does not provide forward secrecy.
The lab notebook is a research log; the detailed causal-key design is deferred to a later post we did not find.

Fit: this matches Cod Sync's walk-back fetch most closely.
If each link carried the key for its predecessor, a reader who could decrypt Bob's newest link could decrypt the whole chain.

## What this suggests for Small Sea

These are options for the #264 and #280 discussions, not decisions.

- **Every shared-store project gave up forward secrecy for stored history.**
  None of them ratchets per message over data that members read again.
  Small Sea would face the same choice for berth publications.
- **New members get old keys in one of two ways.**
  Either they receive the list of all past keys (SSB's `oldSecrets`, p2panda's secret bundle), or they receive one current key that unlocks the rest (Keybase's chain of boxed seeds, Keyhive's causal keys).
  The second fits a chain that is read by walking back from its newest link.
- **Rotation happens on removal, not on addition.**
  Keybase, SSB and p2panda all hand the existing key to new members.
- **Per-sender keys are the exception.**
  Most of these projects use one key per group epoch, not one per sender device.
  A per-sender key adds the ordering problem the probe found: a sender's publications can exist before its key reaches anyone.
- **The fresh-snapshot plan still works,** but it needs a snapshot on both sides of an invitation, and Cod Sync would need a way to start a reader at a snapshot with no predecessor.

Open questions:

- Does a removed teammate keeping old history matter for Small Sea's threat model? Every project above accepts it.
- Should berth publications use per-epoch group keys while Sender Keys stay for runtime messages, as p2panda splits them?
- Should a Constitution epoch, rather than a per-device sender chain, define a key generation?

## Sources

Read 2026-11-24.

- Balbás, Collins, Gajland, "WhatsUpp with Sender Keys? Analysis, Improvements and Security Proofs", https://eprint.iacr.org/2023/1385.pdf, and the RECSI 2022 handout, https://davidbalbas.github.io/files/Sender_Keys_RECSI2022_handout.pdf
- MSC3061, "Sharing room keys for past messages", https://github.com/matrix-org/matrix-spec-proposals/pull/3061
- MSC4268, https://github.com/matrix-org/matrix-spec-proposals/pull/4268
- Matrix v1.19 release, https://matrix.org/blog/2026/07/08/matrix-v1.19-release/
- Matrix Conference 2025 talk slides, "Joining the conversation", https://matrix-2025.pages.dev/slides/slides_VLF9QQ.pdf
- RFC 9420, The Messaging Layer Security (MLS) Protocol, https://www.rfc-editor.org/rfc/rfc9420
- Keybase Book, Teams Crypto, https://book.keybase.io/docs/teams/crypto
- Keybase, "Keybase for Teams", https://book.keybase.io/teams
- Keybase blog, "Encrypted git for everyone" (2017), https://keybase.io/blog/encrypted-git-for-everyone
- SSB private-group-spec, https://github.com/ssbc/private-group-spec, and its add-member section, https://github.com/ssbc/private-group-spec/blob/master/group/add-member/README.md
- SSB group exclusion spec, https://github.com/ssbc/ssb-group-exclusion-spec
- ssb-tribes2, https://github.com/ssbc/ssb-tribes2
- p2panda-encryption crate docs (v0.8.1), https://docs.rs/p2panda-encryption/latest/p2panda_encryption/
- Ink & Switch Keyhive lab notebook, https://www.inkandswitch.com/keyhive/notebook/ (entries 01, 02, 05 and 06)
