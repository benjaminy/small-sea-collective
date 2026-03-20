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

**Why it's urgent:** The Team Manager spec is skeleton-only. This contract unblocks finishing it.

### Settled Decisions

- **Shared SQLite, direct read** — Hub reads `core.db` directly via file-watch + whole-cache flush on any modification. No query API. Fine-grained cache invalidation is possible but almost certainly overkill given low change frequency.
- **Team Manager is UI-only** — writes `core.db` directly, no API surface. Client apps interact with data only through the Hub API. Hub's `/cloud_locations` endpoint is wrong and should be removed; cloud storage config is the Team Manager's responsibility.
- **Sessions in Hub-only DB** — sessions live in `small_sea_collective_local.db` (separate from `core.db`). Other apps access sessions through the Hub API only.
- **Single-user-per-Hub** — one Hub per device/user; no multi-participant file-watcher complexity needed.
- **Hub and Team Manager stay version-locked** — they are the core infrastructure and update together; no cross-version compatibility needed.
- **Permissions are per-station, two-table schema** — `member(id)` (per-team identity) + `station_role(id, member_id, station_id, role)` where role ∈ `{read-only, read-write}`. "Admin" simply means read-write on the TeamManager station. The `member` table will eventually carry key/cert material.
- **Local permissions are authoritative** — Hub only incorporates changes from teammates who have read-write permission in its own local copy. Permission-change race conditions (e.g. Alice upgrades Bob mid-sync) are implementation details, not architecture.
- **Teammate cloud locations belong to member** — stored linked to the `member` record (set via invitation flow). Multiple locations per member deferred.
- **Data is globally readable; privacy via encryption** — Hub reads teammates' Cod Sync chains without special credentials (just the URL). Security comes from E2E encryption, not access control.
- **Hub is always-on background monitor** — runs a background loop watching teammates' cloud locations and incorporating updates when permissions allow. Hub does all cloud I/O (consistent with Section 4).

### Remaining Open Items

- **Hub monitoring API** — apps may need a way to register/deregister cloud locations for the Hub to watch, rather than hard-coding assumptions into the Hub. Shape TBD.
- **Hub's `/cloud_locations` endpoint** — needs to be removed; currently writes to `core.db` directly which is Team Manager's domain.
- **Hub `open_session` for non-NoteToSelf teams** — currently reads `App`/`TeamAppStation` from NoteToSelf/core.db; needs updating to read from the team DB for non-NoteToSelf sessions.
- **`member` key/cert material** — schema placeholder exists; contents TBD (tied to Section 1 encryption decisions).
- **NoteToSelf/[App] stations** — per-app personal state that's more app-specific than team-specific; useful but not yet designed.


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

1. ~~Hub ↔ Team Manager DB contract~~ — mostly resolved; see settled decisions in Section 2. Remaining: monitoring API shape, `/cloud_locations` removal, Hub `open_session` update
2. Session lifecycle — write it out in Hub spec before writing more client code
3. Encryption layer interface — even a rough API sketch (encrypt/decrypt boundary, key storage stub) protects against having to retrofit it everywhere
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, S3Remote elimination
5. Identity model — most complex; can be stubbed a while longer
