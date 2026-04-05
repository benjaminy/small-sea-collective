# Open Architecture Questions

Decisions that are hard to change once downstream code is written. Work through these roughly in order.

---

## 1. Encryption Layer Shape

The Hub-as-chokepoint architecture exists to enable transparent E2E encryption, but the encryption layer isn't implemented yet. This decision ripples into everything else.

**Why it's urgent:** Building out Small Sea Manager, the invitation flow, and Cod Sync consumers before answering these means retrofitting encryption into many call sites.

### Settled Decisions

- **Hub is the transparent crypto proxy** — apps interact with the Hub using plaintext (file upload/download, notifications, VPN send/receive). The Hub transparently negotiates session keys and encrypts/decrypts at the boundary. Apps are crypto-naive; they never touch key material or the Cuttlefish library.
- **Hub = user's crypto identity on this device** — the existing session context (team/app/berth) already tells the Hub which team's keys to use for any operation. No additional routing information is needed.
- **Cloud storage sees only ciphertext** — link blobs and git bundles are encrypted before leaving the Hub. Service providers can affect availability but nothing else. Security comes from E2E encryption, not access control (consistent with Section 2).
- **Cuttlefish is a Hub-internal library** — the protocol stack is PQXDH → Double Ratchet (1:1) / Sender Keys (group). See `packages/cuttlefish/README.md` for primitives and PQC choices.
- **Key storage follows protection levels** — DAILY keys may be unlocked at Hub startup (biometric/device PIN). GUARDED keys are loaded on-demand with an explicit user prompt. BURIED keys are never loaded into Hub memory during normal operation; they are used only for offline root-of-trust ceremonies (signing, revocation).
- **App ↔ Hub channel is localhost plaintext** — acceptable given OS process isolation on a single-user device. A process on the same device already has equivalent trust to the Hub. This is a conscious decision, not an oversight.

### Remaining Open Items

- **Key storage format** — how private keys are persisted on disk (OS keychain, encrypted file, secure enclave where available) is TBD.
- **Key backup/recovery** — base keys that encrypt data can themselves be re-encrypted under multiple other keys (local use, sharing, backup). Mechanism TBD; see also Section 5.
- **`member` key/cert material schema** — placeholder exists in `core.db`; contents now unblocked by Cuttlefish key model.
- **Cod Sync encryption wiring** — cipher and key-exchange bootstrapping for new members joining an existing chain are TBD (see Section 4).

---

## 2. Hub ↔ Small Sea Manager Database Contract

Explicitly TBD in the Hub spec. Hub needs to read team membership/permissions to make authorization decisions; Small Sea Manager owns writes. This is a hard coupling.

**Why it's urgent:** The Small Sea Manager spec is skeleton-only. This contract unblocks finishing it.

### Settled Decisions

- **Shared SQLite, direct read** — Hub reads `core.db` directly via file-watch + whole-cache flush on any modification. No query API. Fine-grained cache invalidation is possible but almost certainly overkill given low change frequency.
- **Small Sea Manager is UI-only** — writes `core.db` directly, no API surface. Client apps interact with data only through the Hub API. Hub's `/cloud_locations` endpoint is wrong and should be removed; cloud storage config is the Small Sea Manager's responsibility.
- **Sessions in Hub-only DB** — sessions live in `small_sea_collective_local.db` (separate from `core.db`). Other apps access sessions through the Hub API only.
- **Single-user-per-Hub** — one Hub per device/user; no multi-participant file-watcher complexity needed.
- **Hub and Small Sea Manager stay version-locked** — they are the core infrastructure and update together; no cross-version compatibility needed.
- **Permissions are per-berth, two-table schema** — `member(id)` (per-team identity) + `berth_role(id, member_id, berth_id, role)` where role ∈ `{read-only, read-write}`. "Admin" simply means read-write on the TeamManager berth. The `member` table will eventually carry key/cert material.
- **Local permissions are authoritative** — Hub only incorporates changes from teammates who have read-write permission in its own local copy. Permission-change race conditions (e.g. Alice upgrades Bob mid-sync) are implementation details, not architecture.
- **Teammate cloud locations belong to member** — stored linked to the `member` record (set via invitation flow). Multiple locations per member deferred.
- **Data is globally readable; privacy via encryption** — Hub reads teammates' Cod Sync chains without special credentials (just the URL). Security comes from E2E encryption, not access control.
- **Hub is always-on background monitor** — runs a background loop watching teammates' cloud locations and incorporating updates when permissions allow. Hub does all cloud I/O (consistent with Section 4).

### Remaining Open Items

- **Hub monitoring API** — apps may need a way to register/deregister cloud locations for the Hub to watch, rather than hard-coding assumptions into the Hub. Shape TBD.
- **Hub's `/cloud_locations` endpoint** — needs to be removed; currently writes to `core.db` directly which is Small Sea Manager's domain.
- **Hub `open_session` for non-NoteToSelf teams** — currently reads `App`/`TeamAppBerth` from NoteToSelf/core.db; needs updating to read from the team DB for non-NoteToSelf sessions.
- **`member` key/cert material** — schema placeholder exists; contents TBD (tied to Section 1 encryption decisions).
- **NoteToSelf/[App] berths** — per-app personal state that's more app-specific than team-specific; useful but not yet designed.


---

## 3. Session Lifecycle & Approval Flow

Sessions are the primary API surface every client app uses.

**Why it's urgent:** The `small-sea-client` library wraps sessions, so the session shape determines the entire client UX. Getting this wrong breaks all downstream client code.

### Settled Decisions

- **PIN-based approval, two-step flow** — (1) App calls `POST /sessions/request` with `(participant, team, app, client_name)`; Hub generates a 4-digit PIN, writes a `pending_session` row, fires a native OS notification (via plyer), and returns the pending ID. (2) User reads the PIN from the notification and types it into the requesting app. App calls `POST /sessions/confirm` with `(pending_id, pin)`; Hub validates and returns the session token.
- **Notification format** — PIN leads for truncation safety: `PIN: 1234 — "ClientName" requesting access to TeamName → AppName`. The Small Sea resource name (team/app) is Hub-authoritative; the client name is self-reported and shown in quotes.
- **Session token** — 32-byte random, opaque. Presented as `Authorization: Bearer <token-hex>` on all subsequent requests. Hub looks it up in its local DB on each call.
- **Session scope** — per-berth, identified by `(team_name, app_name)` as human-readable strings. Hub resolves to the berth ID. Multi-berth sessions (all teams for an app) are a later UX enhancement.
- **App identity** — PIN proves user intent. No process-level binding for now. Future elaborations (signed app certs, etc.) deferred.
- **Pending PIN TTL** — 5 minutes. Pending row is deleted on successful confirm or when an expired confirm is attempted.
- **Session record** — stores `(id, token, berth_id, client_name, created_at, duration_sec)`. `client_name` is preserved for a future "manage active sessions" UI.
- **Session expiry** — deferred. Schema has `duration_sec` as a placeholder.
- **Hub-only DB** — Hub is the only process that accesses the session DB. Caching the lookup is a later optimization if needed.

### Remaining Open Items

- **Session expiry policy** — when and how sessions expire (time, logout, device removal) is TBD. Schema has `duration_sec` as a placeholder.
- **Multi-berth sessions** — one session spanning all berths for a given app; deferred as a UX enhancement.
- **Stale pending session cleanup** — no background cleanup job exists yet; expired rows are only removed when a confirm attempt hits the TTL check.
- **Session management UI** — Hub needs an endpoint to list/revoke active sessions; Small Sea Manager needs a UI for it. Neither is implemented yet.


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

**Why it's urgent:** Every Cod Sync consumer (Small Sea Manager, shared-file-vault, future apps) inherits this format.


---

## 5. Identity Model: NoteToSelf Berth & Multi-Device

The `NoteToSelf-SmallSeaCore` berth holds personal keys and device info. The open question "can a single Hub serve multiple users?" is related.

**Questions to answer:**
- Is identity device-local or portable? Two devices = two identities, or one?
- How does an X3DH prekey bundle get published so that people inviting you can discover it? Is it in your public S3, and what signs it?
- What happens to encrypted data if a device is lost — is there a key backup/recovery story?

**Why it's urgent:** The invitation flow and key rotation logic both depend on the identity model. It can be stubbed longer than the others but shouldn't be deferred past the point where invitations are fully wired up.


Answers:
- This is not implemented at all yet, but my plan is that a person can have the same identity with any number of devides, but it's cryptographically a little complicated.
   Unique key-pairs are generated on devices that support secure enclave fanciness (and fudged on devices that don't).
   That key is the one that's used to sign data that goes out.
   And then there's another cert signing kind of layer where a person can say "yes, this is 'my' device"
- The question about prekeys and signing will be addressed (soon) in addressing section 1.
- The backup key question is important.
   As is common with E2E encrypted systems, the base keys that encrypt the data can themselves be copied and encrypted with lots of other keys for different purposes (local use, sharing, backup)



---

## Suggested Order

1. ~~Hub ↔ Small Sea Manager DB contract~~ — mostly resolved; see settled decisions in Section 2. Remaining: monitoring API shape, `/cloud_locations` removal, Hub `open_session` update
2. ~~Session lifecycle~~ — mostly resolved; see settled decisions in Section 3. Remaining: expiry policy, session management UI
3. ~~Encryption layer interface~~ — mostly resolved; see settled decisions in Section 1. Remaining: key storage format, key backup/recovery, Cod Sync encryption wiring
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, S3Remote elimination
5. Identity model — most complex; can be stubbed a while longer
