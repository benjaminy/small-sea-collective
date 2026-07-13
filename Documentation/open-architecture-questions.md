# Open Architecture Questions

Decisions that are hard to change once downstream code is written. Work through these roughly in order.
The identity, governance, integration-mode, recovery, and retention invariants in [`architecture.md`](../architecture.md#no-team-server) are canonical; this file tracks unresolved mechanisms rather than competing policy.

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

Explicitly TBD in the Hub spec. Hub needs to read team membership and berth integration modes to make local synchronization decisions; Small Sea Manager owns writes. This is a hard coupling.

**Why it's urgent:** The Small Sea Manager spec is skeleton-only. This contract unblocks finishing it.

### Settled Decisions

- **Shared SQLite, direct read** — Hub reads `core.db` directly via file-watch + whole-cache flush on any modification. No query API. Fine-grained cache invalidation is possible but almost certainly overkill given low change frequency.
- **Small Sea Manager is UI-only** — writes `core.db` directly, no API surface. Client apps interact with data only through the Hub API. Hub's `/cloud_locations` endpoint is wrong and should be removed; cloud storage config is the Small Sea Manager's responsibility.
- **Sessions in Hub-only DB** — sessions live in `small_sea_collective_local.db` (separate from `core.db`). Other apps access sessions through the Hub API only.
- **Single-user-per-Hub** — one Hub per device/user; no multi-participant file-watcher complexity needed.
- **Hub and Small Sea Manager stay version-locked** — they are the core infrastructure and update together; no cross-version compatibility needed.
- **Integration mode is per-berth and analysis-relative** — the conceptual values are `automatic` and `proposal-only`.
  The current `berth_role` schema still stores `read-write` and `read-only` as approximations.
  `Steward` remains current Manager shorthand for automatic Core integration, but an authority claim must name its causal context and local analysis.
- **Local integration mode determines what should count** — the target Hub policy only fetches and incorporates ordinary changes from teammates considered automatic by its named local Core projection.
  The current watcher still discovers signals from every teammate, so strict mode-aware replication remains implementation work.
  Mode-change races preserve competing signed evidence; they do not require one globally chosen winner.
- **Significant teammate evidence is append-only** — admission, device, acknowledgment, interaction, delegation, prepared-recovery, recovery-use, integration-mode, display-name, unification, exclusion, repudiation, ratification, storage-announcement, staleness-observation, proposal, and endorsement records remain inspectable.
  Mutable teammate and role tables become rebuildable outputs of a named local analysis rather than one accepted signed lineage.
- **The locally adopted Constitution evidence DAG lives in Core** — every current Core database snapshot produced by a clone contains the signed evidence that clone has adopted, including each record's declared causal closure.
  Adoption into the live Core database is the record-level retention decision; fetching or attempting a Git merge is not enough, and authentic input does not gain durable replication merely by arriving.
  Adoption is not acceptance and grants no local effect by itself.
  The promise is clone-relative non-pruning, not physical immortality, universal availability, or a globally complete evidence set.
  It binds a well-behaved implementation and is not peer-verifiable, since never adopting a record and dropping an adopted one produce the same observable absence; an analysis treats a missing record as unavailable, never as proof it never existed.
  The complete Git commit DAG remains bookkeeping, provenance, and repair ancestry, but constitutional analysis does not depend on old checkout blobs.
- **Eventual visibility, not eventual one-state convergence** — under eventual communication, authentic branches observed by honest participants should become visible to other reachable participants.
  Different analyses may remain in disagreement forever, producing a team split rather than a protocol-selected winner.
- **Teammate cloud locations belong to teammate** — stored linked to the `teammate` record (set via invitation flow). Multiple locations per teammate deferred.
- **Data is globally readable; privacy via encryption** — Hub reads teammates' Cod Sync chains without special credentials (just the URL). Security comes from E2E encryption, not access control.
- **Hub is always-on background monitor** — runs a background loop watching teammates' cloud locations and incorporating updates when its local integration mode calls for it. Hub does all cloud I/O (consistent with Section 4).

### Remaining Open Items

- **Hub monitoring API** — apps may need a way to register/deregister cloud locations for the Hub to watch, rather than hard-coding assumptions into the Hub. Shape TBD.
- **Hub's `/cloud_locations` endpoint** — needs to be removed; currently writes to `core.db` directly which is Small Sea Manager's domain.
- **Hub `open_session` for non-NoteToSelf teams** — currently reads `App`/`TeamAppBerth` from NoteToSelf/core.db; needs updating to read from the team DB for non-NoteToSelf sessions.
- **`teammate` key/cert material** — schema placeholder exists; contents TBD (tied to Section 1 encryption decisions).
- **NoteToSelf/[App] berths** — per-app personal state that's more app-specific than team-specific; useful but not yet designed.
- **Default local analysis contract** — Manager and Hub need one inspectable default analysis for ordinary operation, plus a way to surface alternate post-hoc analyses without presenting any one projection as universal truth.
  A reproducible decision basis includes the analyzer name and semantic version, evidence-frontier and causal-closure digest, local-input and policy digest, and canonical projection digest.
  An implementation may keep an ephemeral local revision for cache invalidation or display, but it may reset after restart or rebuild and is not part of the durable basis.
  Analyzer upgrades must not silently reinterpret an unchanged basis under the same identifier.
- **Branch-local malformed evidence handling** — missing or malformed dependencies must fail closed for conclusions that depend on them without allowing one hostile independent leaf to invalidate all previously useful evidence.
  The quarantine representation and SQLite candidate-adoption boundary are TBD.
- **Adoption mechanics inside a Git-carried database** — Core rows arrive inside a merged tree, so declining to adopt is an active operation: the merge must emit a tree that deliberately omits rows a parent commit contained, and the result then depends on local intake state at merge time rather than on the merged inputs alone.
  What a clone fetched, parked, or declined must survive restart outside the live Core database, or the next merge silently re-decides it.
  The policy-aware merge, its staging and atomicity, and the durable parked-state representation are TBD.
- **App-to-Core basis anchor (candidate)** — an app-berth publication could carry the digest of a signed Core basis-anchor record in which the publishing device names the constitutional basis it claims to be using.
  The Hub should bind that digest to the update in device-signed publication metadata; arbitrary application content need not become Constitution evidence or acquire constitutional signatures.
  An unfamiliar digest signals a basis mismatch — the receiver may be behind, ahead, on a different branch, or missing parked evidence — and lets the receiver fetch or compare Core before deciding integration.
  Recognition gives the receiver's local analysis a concrete signed claim to inspect instead of letting berth scope or arrival order stand in for authorization.
  The anchor is not proof that the device possessed or fully verified the named closure, that the basis was complete, or that it was current.
  Its useful signal is post-hoc: analyses can surface repeated use of an old basis, selective anchoring after witnessed receipt of relevant evidence, incompatible basis claims, or an anchor that cannot be retrieved and verified.
  The app publication should carry one coarse record digest rather than an exposed frontier; the basis-anchor record may commit to the analyzer, evidence, local-input, policy, and projection digests while the privacy and retrieval shape remain open.
- **Authentic-input resource safety** — a lost or compromised recognized device can produce unlimited well-formed signed input.
  Define local intake budgets, suspension, parking, and diagnostic summaries; the non-prunable boundary is adoption into a clone's live Core database, and resource policy must never hide ancestry that clone already adopted.
  Small Sea teams never need high constitutional throughput, so simple per-device rate limits at the intake boundary are the intended shape.
  The protocol imposes no replicated quota on local authorship; each device chooses what it publishes, and each receiving clone chooses what it fetches and adopts.
  Core berths can default to low thresholds, and crossing one surfaces the situation for human review and override rather than acting as silent enforcement.
  Limits may pause claimed repair input too, but they must leave an operator-visible path to inspect, resume, or explicitly override the limit; a remote author cannot obtain an exemption merely by labeling input as repair.
  A generic rate-limiting framework for other apps may be useful eventually; do not over-design it on day one.
  Survey prior art before inventing one: per-author append-only feeds (Secure Scuttlebutt, Hypercore) make per-device budgeting and equivocation detection cheap, and gossip flow control and transparency-log admission control are also relevant.


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
- **History retention**: Compaction does not rebase or replace the Git commit DAG.
  Old non-constitutional blobs may eventually dehydrate beyond a live-data window, while each Core clone keeps its locally adopted Constitution objects and their declared causal closures in later states.
  Separable PII payloads do not inherit that retention merely because their commitments do.
  The window is not an erasure guarantee; teammates may keep independent copies of snapshots they already fetched.
- **Forward restoration**: Shared repair never resets a branch or moves a ref backward.
  A new descendant commit may contain an old tree, after which desired intervening changes are replayed as new commits with provenance.
  Core repair additionally preserves every Constitution object that clone adopted.
- **Hub owns cloud interaction**: S3Remote to be eliminated; all cloud access goes through the Hub.

### Remaining Open Items

- **S3Remote elimination**: Requires reworking the invitation flow. Inviter's cloud data is assumed globally readable (security comes from E2E encryption, not access control). Invitation tokens may include time-limited read paths.
- **Encryption details**: Cipher selection, key exchange protocol, and the bootstrapping flow for new teammates joining a chain are all TBD.
- **Staleness and checkpoints**: Signed observations that a teammate clone has not advanced may warn about an approaching retention horizon and aid later reconvergence. They are evidence only; the explicit protocol rule that could establish a checkpoint or permit pruning past a quiet teammate remains TBD.
- **Repair capability and replay interface**: Cod Sync provides forward restoration and stable commit reachability, not trustworthy attribution.
  Applications declare whether repair is user-directed, author-asserted, or backed by application-defined cryptographic provenance.
  A generic manifest may name the clean base, pre-repair head, omissions, replay sources, conflicts, and irreversible external effects without claiming more authorship certainty than the application can support.
- **Direct identity payload and retained metadata** (principle settled; mechanism open): The principle is now canonical — see [`architecture.md`](../architecture.md#direct-identity-payload-is-separable-metadata-remains).
  Direct identity content rides outside the retained skeleton as inert, separable payload referenced by a commitment.
  The skeleton still exposes stable pseudonyms, causal timing, devices, relationships, delegations, and disputes, so its indirect personal information needs a separate threat model and minimization pass.
  The payload mechanism still needs to be settled and security-analyzed:
  - **Commitment scheme**: A bare `hash(name)` is *not* hiding for low-entropy payloads (a name is brute-forceable from the retained commitment), so excision would not actually conceal it. The commitment must be hiding — e.g. a salted/randomized commitment whose *opening* (payload + randomness) is the droppable unit. Choose and justify the scheme; analyze what the commitment leaks and residual metadata after excision.
  - **Signed-over-the-commitment invariant**: Signatures must cover the commitment, never the raw payload, or dropping the payload would break signature verification (the Git "sign the tree hash, not the blob" shape).
  - **Analysis-inert invariant**: No structural or authority analysis may branch on payload content; the same evidence skeleton and analysis inputs must yield the identical result whether the payload is present, encrypted-to-a-subset, or excised.
    This generalizes to *anything not universally and permanently readable*, including the optional encryption window below.
  - **Optional encryption window**: For ordinary roster hygiene a team may keep identity payloads but encrypt them to a current-membership key window so later joiners cannot read old ones. This is convenience, not erasure: the ciphertext is permanent and readable by everyone who held the epoch key, so it does *not* protect against a contemporary insider, and a leaked old key re-exposes it forever. Its key schedule should be a lineage *separate* from content/sender-key rotation (different cadence and purpose). Where genuine erasure is the goal, prefer commit-and-drop.
  - **Interaction-based identity confidence**: The model's preferred identity story is that confidence in a UUID↔person link accretes through interaction over time. The accretion mechanism (cross-signatures, met-in-person attestations, ambient proximity, address-book bindings) is the most appealing and least-specified piece, and should not be left as a seed-only admission payload by default.
  - **Metadata minimization**: For every retained record family, identify what relationship and timing facts its existence reveals, which audiences learn them, and whether the fact can remain local, pairwise, coarser, or unpublished.

**Why it's urgent:** Every Cod Sync consumer (Small Sea Manager, ssc-files, future apps) inherits this format.


---

## 5. Identity Model: NoteToSelf Berth & Multi-Device

The `NoteToSelf-SmallSeaCollectiveCore` berth (the Core berth) holds personal keys and device info. The open question "can a single Hub serve multiple users?" is related.

**Questions to answer:**
- What is the exact prepared-recovery key and data format, and where does the user keep it?
- How does a loud recovery ceremony prevent replay and rollback while authorizing a fresh device key for an existing teammate UUID?
- How should Manager explain that recovery without prepared material requires a new teammate UUID and rebuilding connections?
- How does an X3DH prekey bundle get published so that people inviting you can discover it? Is it in your public S3, and what signs it?
- What are the minimal signed fields and distinct meanings for witnessed-receipt, acceptance, ratification, interaction, objection, and repudiation evidence?
- How should a client measure and surface "active but not reviewing" — app publications continuing while Core nodes sit without witnessed receipts — without punishing absence or automation?
- How should Manager verify and display real human intent when a device signature alone may reflect malware, token theft, or an ambiguous user interaction?
- Which named risk/availability profiles should guide automatic local activation, and how does a team record those non-binding expectations?
- Which default analyses should Manager expose for current standing, historical standing, interaction confidence, repudiation, and reliance?
- What scoped delegation evidence is needed before an application or counterparty treats a teammate as acting on the team's behalf?
- What is the stable technical origin used for replay separation, and how is it distinguished in APIs and UI from living team identity?
- When shared ancestry splits into durable continuations, when do crypto, routing, and application storage move to distinct operational namespaces?
- What does a full visibility acknowledgment sign, what possession does it attest, and how are missing dependencies represented?
- How should dormant teammates and continuations be surfaced — without a protocol-defined expiration threshold — so people can judge whether historical authenticity should still confer current agency?

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

- **Teammate admission is an inviter-orchestrated, transcript-bound evidence flow.** Key properties that are non-negotiable:
  - The inviter allocates the invitee's `teammate_id` at proposal creation; the invitee does not choose it.
  - The proposal names a retained multi-head causal context rather than depending on one Git checkout or one current roster query.
  - The transcript binds the exact team, proposal, nonce, invitee identifier, and invitee device keys.
    Transport metadata is explicitly excluded.
  - The inviter publishes the completed transcript and their typed acknowledgment.
    Other participants may acknowledge Alice's choice, object, repudiate, or later add bounded interaction evidence about Bob in their own clones.
  - No finalization record turns Bob on globally.
    A recoverable local analysis may act after Alice's acknowledgment; a guarded analysis may wait for more evidence.
  - Admission, automatic Core integration, and authority to represent the team externally are separate claims.

- **Concurrent evidence does not erase proposal eligibility globally.** Proposal records remain authentic claims relative to their causal context even after other governance evidence arrives.
  A participant's analysis may treat concurrent evidence as a reason to defer or reject local effect, but it does not rewrite the proposal as malformed.

- **Identity and authority accumulate through duration.** Carol may initially accept Alice's judgment without knowing Bob, later compare device-bound material with Bob in person, and later acknowledge a scoped delegation.
  Those facts remain distinct retained evidence with separable personal payload where needed.

- **Repudiation is local evidence, not global retroactive finality.** Dave may publish a claim repudiating Bob's admission; Alice and Carol may acknowledge it; another participant may reject it.
  An accepting analysis may unwind Bob-derived standing and repair application state while preserving every signature and reliance record.
  Unresolved disagreement is a team split.

- **Rotation means containment or hygiene, never admission.** A participant
  accepting an exclusion or repudiation rotates with the relevant teammate or
  devices excluded from future redistribution.
  Hygiene is routine and semantically neutral, and rotation does not erase
  earlier disclosure.

- **Post-admission transport setup is a separate flow (B7).** A prospective
  teammate may configure their incoming cloud endpoint after the completed
  admission transcript is published.
  Peers decide whether to use that announcement under their local analysis.
  This capability is independent of admission and is also how existing
  teammates change cloud providers.



---

## Suggested Order

1. ~~Hub ↔ Small Sea Manager DB contract~~ — mostly resolved; see settled decisions in Section 2. Remaining: monitoring API shape, `/cloud_locations` removal, Hub `open_session` update
2. ~~Session lifecycle~~ — mostly resolved; see settled decisions in Section 3. Remaining: expiry policy, session management UI
3. ~~Encryption layer interface~~ — mostly resolved; see settled decisions in Section 1. Remaining: key storage format, key backup/recovery, Cod Sync encryption wiring
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, S3Remote elimination
5. Identity model — most complex; can be stubbed a while longer
