# Open Architecture Questions

Decisions that are hard to change once downstream code is written. Work through these roughly in order.

---

## 1. Encryption Layer Shape

The Hub-as-chokepoint architecture exists to enable transparent E2E encryption, but the encryption layer isn't implemented yet. This decision ripples into everything else.

**Questions to answer:**
- What does the Hub encrypt/decrypt, and when? Does it encrypt before writing Cod Sync bundles, or after? Does the Hub hold decrypted data in memory during a session?
- Where do private keys live? Hard disk? OS keychain? Passphrase-protected? This determines the threat model.
- What does cloud storage actually see? If S3 sees only encrypted blobs, the Cod Sync chain format probably needs to be encryption-aware (e.g., metadata vs. payload separation).

**Why it's urgent:** Building out Team Manager, the invitation flow, and Cod Sync consumers before answering these means retrofitting encryption into many call sites.

---

## 2. Hub ↔ Team Manager Database Contract

Explicitly TBD in the Hub spec. Hub needs to read team membership/permissions to make authorization decisions; Team Manager owns writes. This is a hard coupling.

**Questions to answer:**
- One shared SQLite file, or two files with a sync/notification contract?
- What's the read interface — does Hub query the DB directly, or does Team Manager expose a query API?
- Who owns schema migrations, and what happens if Hub and Team Manager are on different versions?

**Why it's urgent:** The Team Manager spec is skeleton-only. This contract unblocks finishing it.

---

## 3. Session Lifecycle & Approval Flow

Sessions are the primary API surface every client app uses. The Hub spec says this is partially TBD.

**Questions to answer:**
- Who approves a session request — the Hub, the Team Manager app, or the user interactively?
- How is a session scoped to a Station? Can one session span multiple stations?
- What triggers expiry — time, user logout, device removal?

**Why it's urgent:** The `small-sea-client` library wraps sessions, so the session shape determines the entire client UX. Getting this wrong breaks all downstream client code.

---

## 4. Cod Sync Chain Format Stability

Any data stored in S3 using the current chain-of-deltas format becomes a migration problem if the format changes later.

### Settled Decisions

These questions were worked through in detail and are now captured in the [Cod Sync format spec](../packages/cod-sync/Documentation/format-spec.md):

- **Concurrency control**: CAS (compare-and-swap) via conditional writes on `latest-link.yaml`. Failed CAS means pull, merge, retry. Implemented in the Hub's storage adapters and threaded through `SmallSeaRemote` and `LocalFolderRemote`.
- **Versioning**: Per-link semver in `supp_data.cod_version`. Major bump = breaking (reader refuses), minor/patch = additive. Version numbers are monotonically non-decreasing forward through the chain.
- **Encryption**: Link blobs and git bundles encrypted as separate files (allows chain traversal without downloading full bundles). Cipher and key exchange TBD.
- **GC / compaction**: Chain compaction (collapse to fresh initial-snapshot) handles both garbage collection and format migration. Any user with write access can trigger it.
- **Hub owns cloud interaction**: S3Remote to be eliminated; all cloud access goes through the Hub.

### Remaining Open Items

- **S3Remote elimination**: Requires reworking the invitation flow. Inviter's cloud data is assumed globally readable (security comes from E2E encryption, not access control). Invitation tokens may include time-limited read paths.
- **Encryption details**: Cipher selection, key exchange protocol, and the bootstrapping flow for new members joining a chain are all TBD.

**Why it's urgent:** Every Cod Sync consumer (Team Manager, shared-file-vault, future apps) inherits this format.


---

## 5. Identity Model: NoteToSelf Station & Multi-Device

The `NoteToSelf-SmallSeaCore` station holds personal keys and device info. The open question "can a single Hub serve multiple users?" is related.

**Questions to answer:**
- Is identity device-local or portable? Two devices = two identities, or one?
- How does an X3DH prekey bundle get published so that people inviting you can discover it? Is it in your public S3, and what signs it?
- What happens to encrypted data if a device is lost — is there a key backup/recovery story?

**Why it's urgent:** The invitation flow and key rotation logic both depend on the identity model. It can be stubbed longer than the others but shouldn't be deferred past the point where invitations are fully wired up.

---

## Suggested Order

1. Hub ↔ Team Manager DB contract — concrete, scoped, unblocks Team Manager spec
2. Session lifecycle — write it out in Hub spec before writing more client code
3. Encryption layer interface — even a rough API sketch (encrypt/decrypt boundary, key storage stub) protects against having to retrofit it everywhere
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, S3Remote elimination
5. Identity model — most complex; can be stubbed a while longer
