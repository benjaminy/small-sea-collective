# Open Architecture Questions

Decisions that are hard to change once downstream code is written. Work through these roughly in order.
The narrow Constitution core and its extension boundary are defined in [`Documentation/team-constitution.md`](team-constitution.md).
Governance and integration policies tracked here must not silently become core protocol rules.

---

## 1. Encryption Layer Shape

The Hub-as-chokepoint architecture exists to enable transparent E2E encryption, but the encryption layer isn't implemented yet. This decision ripples into everything else.

**Why it's urgent:** Building out Small Sea Manager, the invitation flow, and Cod Sync consumers before answering these means retrofitting encryption into many call sites.

### Settled Decisions

- **Hub is the transparent crypto proxy** — apps interact with the Hub using plaintext (file upload/download, notifications, VPN send/receive). The Hub transparently negotiates session keys and encrypts/decrypts at the boundary. Apps are crypto-naive; they never touch key material or the Cuttlefish library.
- **Hub = user's crypto identity on this device** — the existing session context (team/app/berth) already tells the Hub which team's keys to use for any operation. No additional routing information is needed.
- **Cloud storage sees only ciphertext** — link blobs and git bundles are encrypted before leaving the Hub. Service providers can affect availability but nothing else. Security comes from E2E encryption, not access control (consistent with Section 2).
- **Cuttlefish is a Hub-internal library** — the protocol stack is PQXDH → Double Ratchet (1:1) / Sender Keys (group). See `packages/cuttlefish/README.md` for primitives and PQC choices.
- **Operational keys are per-team and per-device** — one device never copies or impersonates another device's private key.
  OS-backed storage, user-presence requirements for high-amplification signing, and separately prepared recovery capability are protection mechanisms rather than a permanent BURIED/GUARDED/DAILY key hierarchy.
- **App ↔ Hub channel is localhost plaintext** — acceptable given OS process isolation on a single-user device. A process on the same device already has equivalent trust to the Hub. This is a conscious decision, not an oversight.

### Remaining Open Items

- **Key storage format** — how private keys are persisted on disk (OS keychain, encrypted file, secure enclave where available) is TBD.
- **Prepared recovery format and ceremony** — ordinary device keys never leave their devices. A separate per-team recovery capability may authorize a fresh device key for the same teammate UUID through a loud, signed, replay-resistant recovery event. Backup storage, rotation, rollback protection, and UX remain TBD; see also Section 5.
- **`teammate` key/cert material schema** — placeholder exists in `core.db`; contents now unblocked by Cuttlefish key model.
- **Cod Sync encryption wiring** — cipher and key-exchange bootstrapping for new teammates joining an existing chain are TBD (see Section 4).

---

## 2. Hub ↔ Small Sea Manager Database Contract

Explicitly TBD in the Hub spec.
Hub needs team and berth state to make local synchronization decisions, while Small Sea Manager exclusively owns direct Core database access.
This is a hard coupling.

**Why it's urgent:** Session authorization, synchronization policy, and the application basis service all need framework state without giving Hub direct Core database access.

### Settled Decisions

- **Manager database exclusivity** — only the `small-sea-manager` package reads or writes team `core.db` databases directly.
  Hub and client apps obtain required framework state through Manager-owned operations rather than opening Core databases themselves.
- **Hub is the client boundary** — client apps interact with framework state only through the Hub API.
  Hub's `/cloud_locations` endpoint is wrong and should be removed; cloud storage configuration and Core persistence are Manager responsibilities.
- **Sessions in Hub-only DB** — sessions live in `small_sea_collective_local.db` (separate from `core.db`). Other apps access sessions through the Hub API only.
- **Single-user-per-Hub** — one Hub per device/user; no multi-participant file-watcher complexity needed.
- **Hub and Small Sea Manager stay version-locked** — they are the core infrastructure and update together; no cross-version compatibility needed.
- **Integration mode is product policy** — the current `berth_role` schema stores `read-write` and `read-only`, and Manager exposes `steward` and `contributor` presets.
  Future `automatic` and `proposal-only` behavior belongs to the Manager/Hub policy extension, not to the Constitution core.
- **Constitution events live in Core** — Core stores signed event objects and local indexes or projections.
  The event envelope verifies cryptographic structure without deciding membership, authority, local effect, or retention.
- **Concurrent heads remain representable** — neither Manager nor Hub may treat identifier, timestamp, row, Git, or arrival order as constitutional authority.
  A policy may make a local choice, but the core does not choose a winner.
- **Application basis bookmarks are self-contained** — an app may ask the Hub for a canonical object naming the Manager's current structurally verified Constitution view and carry those opaque bytes with its own data.
  The basis contains a format version, technical origin, and canonical minimal tip set, but no roster or policy result.
  Its content digest is a stable basis ID for comparison and optional deduplication, not a lookup key that requires a replicated registry.
- **Basis disclosure follows application data** — a basis has no issuer, device, application, or timestamp field, but its origin and tips are visible to every holder of the application record carrying it.
  The record may identify its author, and holders may correlate the tips with the event DAG to infer roster, recovery, or other personal events selected by the view.
- **Wide views are bounded by parking, not by the basis operation** — when active Constitution tips would exceed the integration policy's bound, Manager parks the excess branches rather than refusing or truncating a basis.
  Manager assigns handling states parent before child rather than activating a received batch atomically from its final tips; ordering among concurrently ready events remains a local choice.
  A batch containing more concurrent branches than the bound therefore parks at least one branch before a descendant merge is considered.
  An event with any parked ancestry remains parked; no received event unparks a branch, because a flooding device can publish its own merge to bring the tip count back under the bound.
  Unparking is a local acceptance decision: a merge naming parked tips integrates as an ordinary collapse on devices that never parked those branches and is surfaced through the Hub as a proposed reconciliation on devices that did.
  Compromise recovery usually needs no unparking — remove the device on a surviving branch and continue, leaving the flood parked.
  Parking stays visible, reversible, and distinct from verification failure; Manager exposes it to the Hub so the Hub can notify the user without changing the basis or application contract.
  The basis operation stays total and always names the full active view.
- **Teammate cloud locations belong to teammate** — stored linked to the `teammate` record (set via invitation flow). Multiple locations per teammate deferred.
- **Data is globally readable; privacy via encryption** — Hub reads teammates' Cod Sync chains without special credentials (just the URL). Security comes from E2E encryption, not access control.
- **Hub is always-on background monitor** — runs a background loop watching teammates' cloud locations and incorporating updates when its local integration mode calls for it. Hub does all cloud I/O (consistent with Section 4).

### Remaining Open Items

- **Hub monitoring API** — apps may need a way to register/deregister cloud locations for the Hub to watch, rather than hard-coding assumptions into the Hub. Shape TBD.
- **Hub's `/cloud_locations` endpoint** — needs to be removed; currently writes to `core.db` directly which is Small Sea Manager's domain.
- **Hub `open_session` for non-NoteToSelf teams** — currently reads `App`/`TeamAppBerth` from NoteToSelf/core.db; it must instead obtain the corresponding team-berth resolution through the Manager-owned boundary.
- **`teammate` key/cert material** — schema placeholder exists; contents TBD (tied to Section 1 encryption decisions).
- **NoteToSelf/[App] berths** — per-app personal state that's more app-specific than team-specific; useful but not yet designed.
- **Default Manager/Hub policy interface** — current product behavior still needs one inspectable rule for deciding whom to watch, integrate, and give keys.
  Its versioning and diagnostics are implementation or extension design, not fields in every core event.
- **Partial and hostile input handling** — the core verifier must distinguish invalid envelopes from valid events with missing parents.
  SQLite staging, quarantine, retry, and local resource budgets remain storage-implementation work.
- **Basis service plumbing** — the public operation belongs on the Hub's team-scoped session surface, while basis computation remains Manager-owned.
  The exact local Manager/Hub call, canonical encoding, format tip-count limit (which over fixed-size tip IDs also determines the maximum encoded size), integration-policy active-tip bound, and malformed-object response remain implementation work.
  The policy bound may be lower than the format limit but must not exceed it.
  A recipient rejects excessive byte length before canonical decoding and rejects an over-limit tip count during decoding before allocating or consuming the tip entries; either violation makes the whole object malformed.
  Micro tests should cover canonical tip reduction, stable IDs, malformed or oversized objects, parking and sticky descendants at the active-tip bound, a flood-plus-self-merge batch remaining parked, reconciliation acceptance, and missing named events.
- **Basis freshness checking** — the Hub basis operation could report whether a presented basis still names the device's active view, and possibly enforce freshness for sessions that opt in.
  Whether checking or enforcement belongs in the generic contract is TBD; the current contract carries the basis object and leaves freshness as the application's responsibility.
- **Reconciliation acceptance policy** — when a device that parked branches may accept a proposed reconciliation automatically rather than prompting the user is Manager policy work, not core protocol.
  Candidate signals include the number of branches being reactivated and whether their signers remain trusted in the resulting view.
- **Authentic-input resource safety** — a valid signature does not entitle an event to unlimited local storage, bandwidth, computation, or policy effect.
  Define simple local limits and visible recovery paths in the storage implementation before adding a replicated quota or governance protocol.


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

- **Concurrency control**: CAS (compare-and-swap) via conditional writes on `latest-link.yaml`. Failed CAS means pull, merge, retry. Implemented in the Hub's storage adapters and threaded through `SmallSeaStore` and `LocalFolderStore`. A failed CAS is reported, not resolved: Cod Sync does not fetch, merge, or retry on the caller's behalf.
- **Versioning**: Per-link semver in the link's `version` key. Major bump = breaking (reader refuses), minor/patch = additive. Version numbers are monotonically non-decreasing forward through the chain.
- **Encryption**: Link blobs and git bundles encrypted as separate files (allows chain traversal without downloading full bundles). Cipher and key exchange TBD.
- **GC / compaction**: Chain compaction (collapse to a fresh full snapshot that still contains the published head) handles both garbage collection and format migration. Any user with write access can trigger it.
- **History retention**: Compaction does not rebase or replace the Git commit DAG.
  Objects may eventually dehydrate beyond a live-data window according to application and storage policy.
  The Constitution event envelope does not itself impose a global or clone-local retention promise.
  The window is not an erasure guarantee; teammates may keep independent copies of snapshots they already fetched.
- **Forward restoration**: Shared repair never resets a branch or moves a ref backward.
  A new descendant commit may contain an old tree, after which desired intervening changes are replayed as new commits with provenance.
- **Hub owns cloud interaction**: the direct-provider stores are test-only; all production cloud access goes through the Hub.

### Remaining Open Items

- **Direct-provider store elimination**: Requires reworking the invitation flow. Inviter's cloud data is assumed globally readable (security comes from E2E encryption, not access control). Invitation tokens may include time-limited read paths.
- **Encryption details**: Cipher selection, key exchange protocol, and the bootstrapping flow for new teammates joining a chain are all TBD.
- **Staleness and checkpoints**: Storage and application policies may need warnings or checkpoints before historical blobs dehydrate.
  Do not add them to the Constitution core unless interoperability requires a new core object rule.
- **Repair capability and replay interface**: Cod Sync provides forward restoration and stable commit reachability, not trustworthy attribution.
  Applications declare whether repair is user-directed, author-asserted, or backed by application-defined cryptographic provenance.
  A generic manifest may name the clean base, pre-repair head, omissions, replay sources, conflicts, and irreversible external effects without claiming more authorship certainty than the application can support.
- **Identity payload privacy**: An admission extension must decide what personal data it signs, encrypts, retains, or separates from a durable event.
  This is important privacy design, but it is not a reason for the core envelope to define names, interaction attestations, commitment openings, or encryption windows.

**Why it's urgent:** Every Cod Sync consumer (Small Sea Manager, ssc-files, future apps) inherits this format.


---

## 5. Identity Model: NoteToSelf Berth & Multi-Device

The `NoteToSelf-SmallSeaCollectiveCore` berth (the Core berth) holds personal keys and device info. The open question "can a single Hub serve multiple users?" is related.

**Questions to answer:**
- What is the exact prepared-recovery key and data format, and where does the user keep it?
- How does a loud recovery ceremony prevent replay and rollback while authorizing a fresh device key for an existing teammate UUID?
- How should Manager explain that recovery without prepared material requires a new teammate UUID and rebuilding connections?
- How does an X3DH prekey bundle get published so that people inviting you can discover it? Is it in your public S3, and what signs it?
- How should Manager verify and display real human intent when a device signature alone may reflect malware, token theft, or an ambiguous user interaction?
- Does a concrete cross-participant UI need teammate-visible device labels, rather than stable device-key identifiers and a device count?
  If so, the signed record should carry a label commitment beside a separately encrypted payload, following the existing admission-label pattern.
- What observer-local device-liveness product is needed, and which device or Hub owns its evidence, retention, aggregation, and UI?
- What local policy should the first admission extension use before integrating a new teammate or distributing future key material?
- Which extension, if any, should represent scoped authority outside Small Sea?

**Why it's urgent:** The invitation flow and key rotation logic both depend on the identity model. It can be stubbed longer than the others but shouldn't be deferred past the point where invitations are fully wired up.


Answers:
- Each device has a distinct per-team operational key and one device never copies or impersonates another's key.
- An already-enrolled sibling can link a fresh device key for the same teammate UUID.
- A separately prepared per-team recovery capability may also authorize a fresh device key, but only through a conspicuous signed recovery event.
- Without a sibling or prepared recovery, tier-two recovery creates a new teammate UUID and rebuilds connections through fresh admission.
- The question about prekeys and signing will be addressed in Section 1.

### Settled Decisions (from issue-97 trust-domain reframe)

The following questions from this section are settled. See `architecture.md` §1
and `packages/small-sea-manager/spec.md` for the full descriptions.

- **Read access is endpoint-trust-scoped.** Any admitted party can in principle
  proxy plaintext or receiver state to others. The protocol does not and cannot
  enforce a cryptographic read-access boundary between admitted and non-admitted
  parties. The real boundary is the social commitment of admitted parties.

- **Linked-device admission is a unilateral identity-owner act (sibling
  handoff).** The existing sibling bootstraps the new device by handing off
  current team state and the sibling's peer sender keys, and publishes a
  `device_link` cert over the new device's concrete public keys. The new
  device's access is join-time-forward from the bootstrap snapshot. No
  per-sender redistribution ceremony is required for admission.

- **The current teammate-admission extension is inviter-orchestrated and transcript-bound.** Its current safety properties are:
  - The inviter allocates the invitee's `teammate_id` at proposal creation; the invitee does not choose it.
  - The transcript binds the exact team, proposal, nonce, invitee identifier, and invitee device keys.
    Transport metadata is explicitly excluded.
  - The inviter publishes the completed transcript.
  - The current endorsement threshold and `finalization` record are policy in this extension, not Constitution-core membership semantics.
  - Admission does not automatically prove authority to represent the team externally.

- **Rotation means containment or hygiene, never retroactive erasure.**
  The identity and encryption extensions decide who receives future key material.

- **Post-admission transport setup is a separate flow (B7).** A prospective
  teammate may configure their incoming cloud endpoint after the completed
  admission transcript is published.
  Peers decide whether to use that announcement under their storage-routing policy.
  This capability is independent of admission and is also how existing
  teammates change cloud providers.



---

## Suggested Order

1. ~~Hub ↔ Small Sea Manager DB contract~~ — mostly resolved; see settled decisions in Section 2. Remaining: monitoring API shape, `/cloud_locations` removal, Hub `open_session` update
2. ~~Session lifecycle~~ — mostly resolved; see settled decisions in Section 3. Remaining: expiry policy, session management UI
3. ~~Encryption layer interface~~ — mostly resolved; see settled decisions in Section 1. Remaining: key storage format, key backup/recovery, Cod Sync encryption wiring
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, direct-provider store elimination
5. Identity model — most complex; can be stubbed a while longer
