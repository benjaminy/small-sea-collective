# Small Sea Collective Architecture

Small Sea Collective is a framework for building collaborative team applications on top of general-purpose cloud services (like Dropbox for storage or ntfy for notifications). It brings the local-first paradigm to team collaboration, ensuring that users own their data and do not depend on application-specific backend services.

## Core Concepts

- **Team**: The primary unit of collaboration. In Small Sea, teams are decentralized; there is no central registry.
- **Application (App)**: A way to organize resources like storage, notifications, and identity. Apps are not specific client software but logical groupings of resources.
- **Berth**: The intersection of a specific **Team** and a specific **App**. It is the fundamental unit of resource allocation, local client authorization, cryptographic readability, and teammate integration mode.
- **Client**: Any software (GUI, CLI, agent) that accesses resources through the Small Sea Hub.
- **Hub**: By default, a local service that mediates all access to general-purpose cloud services.
  It acts as a security gateway and protocol translator.
  Experimental deployment shapes are discussed under "Hub Deployment Shapes."

A berth is globally `Team x App`; a participant is not a third berth
coordinate. A participant is the local holder of access to berths through
identity and team membership. From inside a specific app, the app coordinate is
already fixed, so local materialization usually projects to a participant
context containing team scopes. Apps own those local materialized trees and may
use OS-standard app homes and app-chosen directory names. Small Sea provides
registration, authorization, and stable IDs; it does not create arbitrary app
data folders under Manager/Core's NoteToSelf tree. Apps consuming Hub session
info should use exposed hex-string IDs, such as `participant_hex` and
`berth_id`, for Small Sea-derived path components rather than friendly names.

### No Team Server

Small Sea has no central team service that can grant or deny a teammate's writes.
Each teammate publishes their own history, and each participant independently decides which histories to watch, fetch, and integrate into their local clones.
The same teammate update may therefore be integrated by Alice, rejected by Bob, and not yet observed by Carol.

This section is the canonical source for Small Sea's teammate identity, governance, and integration model.
Package-level specifications may describe their mechanisms and current implementation gaps, but they should defer to these semantics rather than inventing parallel policy.

Four questions are easy to blur together:

- **Recognition** asks whether a signature can be traced through the accepted teammate and device history at the state the record references.
- **Readability** concerns which teammates receive the key material needed to interpret future berth updates.
- **Integration mode** determines whether a teammate's ordinary berth publications are accepted automatically or only through explicit proposals.
- **Replication and discovery** concern how publications and proposals are noticed and fetched.

Only integration mode is a per-berth teammate category in this model:

- **Automatic** means peers following the policy are expected to monitor the teammate's ordinary berth publications and integrate every valid change by default.
- **Proposal-only** means peers are not expected to monitor ordinary berth publications from that teammate.
  The teammate may instead sign a change proposal, which becomes eligible for integration after endorsement by the berth's required number of automatic integrators.

The endorsement threshold is always at least one automatic integrator and may be higher for a berth or event type such as Core admission.

Both modes describe recognized teammates who may read, author, and sign data.
The mode changes what peers are expected to integrate, not whether the teammate can produce a change.
The current `read-write` and `read-only` schema values approximate `automatic` and `proposal-only` respectively; renaming those stored values is deferred until the proposal mechanism exists.
When a protocol rule needs a replayable authority at a Core anchor, it should say **automatic Core integrator** rather than treating a Manager role name as protocol state.
The Manager-facing `steward` preset is shorthand for automatic integration on Core, but it is not a central authority or a special key class.

These terms live on two deliberately separate layers, and the distinction is a standing convention rather than loose synonymy:

- **automatic Core integrator** — the *protocol* term. An anchor-relative, replayable standing a verifier evaluates by replaying accepted Core history. Protocol rules, endorsement thresholds, and validity checks use this term. "Core integrator" is an accepted short form; it always implies automatic mode, since proposal-only teammates do not integrate.
- **`steward` / `contributor`** — *Manager-facing presets*. Convenience bundles a human picks at invitation time (`steward` = automatic everywhere; `contributor` = proposal-only on Core, automatic elsewhere). They are not protocol state and must not be evaluated as such.
- **`automatic` / `proposal-only`** — the two *integration modes*, per teammate per berth, that the presets expand into and that the protocol actually reasons about.
- **`read-write` / `read-only`** — the current `berth_role` projection's stand-ins for `automatic` / `proposal-only`; renaming these stored values is deferred (see #167).

This two-mode model is an intentionally modest accommodation for medium-sized teams.
As a group grows, it is natural for a smaller inner group to handle routine integration while a larger outer group mostly observes and occasionally proposes changes.
A medium-sized team genuinely has different levels of engagement, responsibility, and accountability, and Small Sea should support that without trying to model every possible governance arrangement.
Automatic and proposal-only are the two built-in modes in the canonical model, not a claim that two modes are all a team could ever want.
A richer, team-configurable scheme — where a team defines its own roles and specifies which kinds of changes each role is expected to integrate from which others — is a plausible future direction that is deliberately left undesigned for now.
A separate scaling axis — letting larger structures emerge as graphs of cooperating small teams rather than one large team — is explored in [`Documentation/linked-teams.md`](Documentation/linked-teams.md); it is exploratory and not part of the canonical model yet.

Replication is mostly a consequence of integration intent, but it is not identical to it.
A lightweight proposal-discovery path must remain observable even when peers do not monitor the proposer's ordinary berth publications.

Local Hub authorization is different and real.
A participant's Hub enforces which client software may act in which berth on that device, protects provider credentials, and mediates Small Sea internet traffic.
Authorization language in this document refers to an enforced local boundary unless a passage explicitly says otherwise.

### Signed, Append-Only Teammate History

This signed, append-only lineage is named the **Team Constitution**, distinct from Core, which is the berth and database that carries it.
[`Documentation/team-constitution.md`](Documentation/team-constitution.md) is the field-level schema for it: the shared record envelope, the anchor mechanism, and the record catalog.

The target model stores significant teammate information as signed, append-only domain records in Core.
Admissions, device links and revocations, prepared recovery and recovery use, display-name and teammate-unification claims, berth integration-mode changes, exclusions, storage announcements, staleness observations, proposals, and endorsements append new facts rather than overwriting or deleting old ones.
Mutable tables and UI models may serve as rebuildable projections, but they are not the durable source of teammate history.
Where such a record carries personally identifying content — most clearly a display name or an identity claim — only the governance fact and a commitment to that content are durable chain data; the personal content itself is separable payload, as described under *Personal Data Is Not in the Long-Term Chain* below.

Each durable record identifies its author and carries a signature over canonical domain bytes.
Governance-bearing records also reference the causal Core state against which they should be evaluated.
Governance state is derived by replaying an accepted causal lineage, not by trusting wall-clock timestamps or whichever row happened to arrive last.
Operational announcement streams may define narrower domain-specific projection rules, but arrival order is never authority.
Historical questions such as “which devices could speak for this teammate?” or “who could endorse a Core proposal?” are answered at the record's referenced state.

Git remains the snapshot, transport, versioning, and three-way-merge framework.
The application database carries signatures for facts whose domain meaning must survive rebases, synthesized merge commits, and later history inspection.
Git provenance and signed domain provenance complement one another; Git commit authorship alone is not the authority for significant teammate facts.

Every Core database snapshot contains the complete signed teammate-history chain through that snapshot's state.
Current trust decisions must therefore be explainable from the current database without checking out discarded historical blobs or consulting a separate log service.
These cryptographically significant records are expected to be small and infrequent.
As a working assumption, a small-to-medium team generates on the order of a few hundred bytes of constitutional history per day — tens to hundreds of kilobytes per year, and perhaps a few megabytes over a team's lifetime — so retaining the complete chain is cheap at the expected scale.
Growth remains worth measuring, but it is not a reason to make constitutional history depend on Git object retention.

The chain carries governance facts, not personal data.
See *Personal Data Is Not in the Long-Term Chain* below for what is deliberately kept out of the permanent chain, and how content that must later be withheld or excised is handled without breaking that chain.

The Git commit DAG is also retained in full.
Cod Sync may compact its transport chain and may eventually dehydrate old bulk file contents, but it does not replace old commits with a fresh snapshot history or rebase away commit identities.
The intended shape is that Git commit metadata, stable commit IDs, and parent relationships remain available indefinitely, while the object data needed to rehydrate older snapshots may be dropped after a live-data window long enough for the team to notice, fetch, and converge.
That window is deliberately not precise yet, and it should be understood as a content-retention practice rather than an erasure guarantee.
Any teammate who has seen a snapshot may keep their own copy of it outside Cod Sync's retention policy.

A proposal preserves the proposer's signature over its exact payload and each automatic integrator's endorsement of that proposal digest.
If review or conflict resolution changes the payload, the result is a new proposal revision requiring fresh signatures rather than a silent mutation attributed to the original proposer.

### Personal Data Is Not in the Long-Term Chain

The signed, append-only Core chain is deliberately limited to a governance *skeleton*: per-team participant UUIDs, device public keys, the edges among them (admission, device link, revocation, exclusion, integration-mode change, endorsement), the endorsement thresholds, and the Core anchors those records reference.
Governance replay branches only on that skeleton.
Personally identifying information — display names, identity material attached at admission, free-text reasons, and similar human-readable labels — is intentionally not part of the durable governance skeleton.
The system can try to keep that content outside the always-retained Core event stream and outside non-dehydratable Git objects, but it cannot promise that no copy exists once a teammate has received a snapshot.

This is a deliberate invariant, not an oversight.
Three reasons make it load-bearing:

- A complete append-only governance log cannot be selectively erased without breaking replay, so anything that may later have to be withheld, encrypted, or excised from the ordinary retained dataset must not live in the permanent chain.
- Real-world identity is observer-relative: the chain can attest what a UUID *did*, but who that UUID *is* belongs to each participant's own knowledge, which is expected to accrete through interaction over time rather than being fixed by a one-time record.
- Keeping personal data out of the permanent skeleton is what lets pseudonymous participation be the safe default rather than a conspicuous opt-out.

When a team does want identity material attached to a membership change, the intended design has it ride *outside* the skeleton as inert payload.
The mechanism is not yet settled — the commitment scheme in particular still needs cryptographic analysis — but it must satisfy these properties:

- The chain stores only a hiding commitment to the payload, so the permanent commitment does not leak low-entropy content such as a name. A bare `hash(name)` is not hiding for low-entropy input, so the commitment must be salted or otherwise randomized.
- Signatures cover the commitment, never the raw payload, so the payload can be encrypted to a current-membership window or dropped entirely without invalidating any signature or any governance replay.
- Governance never reads the payload. Validity is decided from the skeleton alone, so a participant who cannot read the payload — a future member, or anyone after the payload is excised — replays to the identical result.

The consequence is explicit and accepted: once such a payload is excised, there is no way to recover what it was from the retained record alone.
The permanent record then proves only that the signers committed to and approved *some* payload with the recorded commitment, bound to a specific UUID's membership change.
If someone later presents a candidate payload and the commitment opening, the record may verify that candidate, but forensic reconstruction of the payload's content does not survive its excision.
The exact commitment scheme, the optional encryption-window key schedule, and the interaction-based identity-confidence model are mechanism details tracked in [`Documentation/open-architecture-questions.md`](Documentation/open-architecture-questions.md).

### Device Identity and Recovery

Each enrolled device has its own team-device key and one device must never impersonate another.
Ordinary device enrollment therefore creates and links a fresh key; it does not copy an existing device's operational private key.

A participant may prepare separate per-team recovery keys and supporting data in advance and keep them in user-controlled backup storage.
Recovery material is not an ordinary device identity and is not distributed through routine Small Sea sync.
Using it authorizes a fresh device key for the existing teammate identity through a separate, conspicuous recovery ceremony recorded in signed Core history.
That ceremony must be designed to make replay and rollback visible, retire or rotate used recovery capability where appropriate, and let peers distinguish recovery from routine device linking.

If no usable recovery material or already-enrolled sibling device exists, recovery falls into the deliberately painful tier-two case.
The person creates a new per-team teammate identity and rebuilds their connections through fresh admission rather than pretending to have recovered an old device or identity.
The exact backup format and recovery protocol remain implementation work, but these identity invariants are not deferred.

### Core as Constitutional History

An accepted Core lineage is the shared referent against which teammate recognition, device standing, integration modes, and proposal endorsements are evaluated.
A claim such as “Alice is a Core integrator” is therefore incomplete without the Core state at which it is evaluated.

Core validity and Core acceptance remain distinct.
A proposal can be replayably valid relative to its anchor because its authors and endorsers satisfy the rules recorded there.
That validity does not let anyone change another participant's clone by fiat.
The admission ceremony and future merge-request machinery replace a central server as validity mechanisms; social adoption and merging still produce convergence.

Ordinary app-berth divergence can be routine and healthy.
Persistent incompatible Core lineages disagree about the team's constitution and should be surfaced as an explicit team fork.
Append-only storage preserves every side of such a fork; it does not pretend the disagreement has disappeared.

Forking is a failure mode to understand, not a collaboration feature to optimize for.
The common case should remain convergence on one accepted Core lineage.

### Retention Horizons and Staleness

Keeping the Git commit DAG does not require keeping every historical bulk blob immediately rehydratable.
A live-data window may bound the content needed to reconstruct recent states while preserving commit identities and the complete signed constitutional log carried by every Core snapshot.
Core data is generally small, so its live-data window should default to a conservative horizon with little pressure to prune aggressively.

Occasionally a recognized teammate may be quiet or unable to publish for a long time while the rest of the team continues moving.
A useful candidate record is a signed Core staleness observation such as: “Alice has not observed Bob's berth clone advance since this head, for this much local time or this many accepted updates, and expects the live-data window to advance past it soon.”
The record should identify the observer, the teammate and berth observed, the last observed state, and objective counters or references where available.

A staleness observation is evidence and warning, not a command, exclusion, or unilateral declaration of finality.
Different teammates may have different observations because they have seen different network and clone states.
Recording those observations could make a later reconvergence attempt easier to diagnose and give a quiet teammate time to catch up before older bulk data is pruned.
It cannot itself advance another participant's retention horizon or recreate data that has already been discarded.

Any rule that turns a retention horizon into an accepted checkpoint needs an explicit signed protocol and validation rule.
Until that rule exists, implementations should preserve and surface late or divergent history rather than silently treating staleness as consent to abandon it.

## Technical Pillars

### 1. Fully Decentralized Team Management
Small Sea uses Signal-inspired cryptographic protocols ([X3DH](https://signal.org/docs/specifications/x3dh/) and [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/)) to manage identity and group membership. Teammates certify each other's identities, effectively building a decentralized web of trust.

**Read access is endpoint-trust-scoped.** Any admitted party — teammate or sibling device — can in principle proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this; it relies on the social commitment of admitted parties rather than a cryptographic enforcement boundary.

**Key rotation serves two purposes: exclusion and hygiene.** Exclusion handles removal and post-admission objections, both via the same rotate-with-exclusion primitive. Hygiene is routine and semantically neutral. Rotation is never used to admit a new party.

**Linked-device admission is a unilateral identity-owner act.** An existing sibling device bootstraps the new device by handing off current team state and the sibling's snapshot of peer sender keys. The sibling issues a `device_link` cert over the new device's concrete public keys and publishes it to the team DB. Other teammates observe the new device via the published cert; objection is handled post-hoc by exclusion. The new device's access is join-time-forward: it reads from what the sibling held at bootstrap time and does not receive historical ciphertext encrypted before the cert was published.

**Teammate admission is an inviter-orchestrated, transcript-bound, Core-integrator-quorum flow.**

- *Governance-snapshot anchor.* Every proposal is anchored to a verifiable team-history reference (the team's `Sync/core.db` commit hash). The anchor freezes the automatic Core integrator roster, membership roster, and teammate→device mapping. Every participant can independently replay team history to the anchor and verify the frozen state.
- *Proposal shell published at initiation.* The inviter allocates a fresh UUIDv7 `teammate_id` for the invitee and publishes a proposal shell to team DB before the invitee is contacted. Other automatic Core integrators in the frozen governance set see the proposal immediately and can withhold endorsement or object before the invitee has invested any effort.
- *Transcript binding.* The invitee generates fresh keys and signs an acceptance blob binding to the inviter-allocated `teammate_id`. The inviter assembles the full admission transcript over the invitee's concrete device keys and the allocated `teammate_id`. Transport metadata (cloud endpoints) is explicitly excluded from the immutable transcript; post-admission transport setup is a separate flow.
- *Teammate/device endorsement bridge.* Each endorsement is a teammate-scoped decision executed by a device-key signature. An endorsement is valid iff the signing key appears in a `device_link` cert at the anchor that maps to a teammate in automatic mode on Core. This bridge is a step-by-step derivation any verifier can replay: cert chain at the anchor → device key → teammate ID → Core integration mode. Endorsements from devices linked after the anchor, or from proposal-only Core teammates at the anchor, are rejected. Multiple device endorsements from the same teammate dedupe to one.
- *Inviter-published finalization.* The inviter observes quorum met and publishes the signed finalization record. The invitee never publishes their own admission. `quorum = 1` is the default; the inviter's own endorsement alone meets quorum and the end-to-end flow reduces to Alice-initiates → Bob-returns-signed-transcript → Alice-endorses-and-publishes.
- *Non-durable proposal eligibility.* Proposal records remain inspectable, but their eligibility is invalidated by any governance-state change relative to the anchor: automatic Core integrator changes, membership changes, or teammate→device mapping changes. Eligibility also expires after a per-team window. An ineligible proposal cannot be finalized and is not a durable bearer capability.

There is no central membership oracle, no globally authoritative service, and no globally authoritative automatic Core integrator.
Each participant maintains a local clone of the team's history and
therefore a local view of who is in the team and whose updates should count.
Those views can diverge. Small Sea aims for social convergence through shared
history and sync conventions, not for a magical elimination of disagreement.

The same distinction applies to devices: joining an existing **identity**
through NoteToSelf is not the same thing as joining every **team** known to
that identity. A new device may become part of Alice's identity first, learn
about Alice's teams from NoteToSelf, and then join only some subset of those
teams later.

### 2. Snapshot-Based 3-Way Merge (Git)
The baseline synchronization method is snapshot-based 3-way merge, utilizing `git`. While slower than CRDTs, it provides strong consistency for full-environment snapshots and allows for easier adaptation of existing software. 

### 3. Cod Sync
"Cod Sync" is the specific protocol used to sync git repositories over cloud storage. It encodes changes as a chain of git bundles uploaded to each user's cloud storage location. Teammates poll or receive notifications to pull and merge these bundles.

Cloud storage placement is explicitly provisioned rather than derived from
identity values. A Hub session authorizes an app to act in a berth; it does
not guarantee that cloud storage has been provisioned for that berth. The
Manager records the participant's cloud accounts and per-berth allocation
choices. The Hub performs provider I/O and reconciles those choices with the
provider, including recording provider-issued locators when materialization
returns one. Team-visible peer routing is teammate-plus-berth scoped, because
different teammates may store their clones of the same berth in different
providers or accounts.

## Design Principles & Constraints

### Human-Scale Coordination

Small Sea optimizes first for small teams and human-paced collaboration, not for
large-scale, low-latency consensus. Several dozen teammates should be treated
as a soft upper bound for a single team; larger communities should usually be
modeled as multiple related teams.

This scale assumption is an architectural constraint. When a conflict,
identity collision, or ambiguous sync result cannot be resolved simply and
safely, the system should preserve the competing states and make the ambiguity
visible rather than inventing a brittle automatic winner. A Hub rejection, a
Manager prompt, or a parked git branch is often the correct result.

The corresponding safety rule is strict: human-scale repair is acceptable, but
silent misresolution is not. Code must not grant access by arbitrary row order,
collapse distinct identities by friendly name, or discard one side of a
conflict just because the rare case is inconvenient.

### The Hub as the Sole Gateway
**All internet communication for Small Sea components must go through the Hub.**
Applications, synchronization protocols, and internal packages must never make
direct network calls to cloud storage, peers, or external services on their
own.

This does **not** forbid one device's Hub from talking directly to another
device's Hub. Hub-to-Hub transport, including future VPN-backed paths, still
fits the rule. What is forbidden is bypassing the local Hub.

This chokepoint enables transparent end-to-end encryption and consistent access
control.

#### Hub Deployment Shapes

The default Hub is a local device service.
That default is load-bearing: apps can be relatively simple and permissive
because the Hub is the security and privacy gateway that holds provider access,
enforces berth-scoped authorization, and mediates Small Sea internet traffic.

Near-term, Small Sea is built and shipped desktop-only.
The mobile question is real but not urgent.
The rest of this section records the current stance rather than a commitment.

**Letting every app become its own Hub is not the answer.**
It would force each app to reimplement berth isolation, authorization, provider
I/O rules, and sync validation correctly, eroding the model by a thousand small
cuts.
Embedded Hub-like runtimes may be acceptable for narrowly scoped seed apps on
platforms that force that shape, but they are not equivalent to the general
Hub boundary.
Seed apps are useful, production-intended applications that help prove and grow
the ecosystem; they have no special protocol status.
The Hedgerow, Tide Table, and Small Sea Collective Files are examples: each gives people
a reason to join the network without holding any architectural privilege.
The Manager is not a seed app.
It is the one currently special app — it writes to `SmallSeaCollectiveCore`
and so holds team membership state, device registration, and service
credentials.
Apps with write access to `SmallSeaCollectiveCore` are the special category;
seed apps are explicitly outside it.
Another Manager-class app could exist in principle, but introducing one is a
significant architectural move, not a routine addition.

**Android is a plausible first mobile experiment.**
A Hub on Android may be able to run as a foreground service with a persistent
notification, exposing a bound service or content provider that other apps
connect to.
This is close to the shape Tailscale, Syncthing, KDE Connect, and Briar use.
The model needs serious experimentation before Small Sea should promise Android
support: the persistent notification is user-visible, manufacturer-level
battery optimization may require Settings whitelisting, and the cross-app
authorization UX has to preserve berth isolation rather than becoming a loose
collection of app-specific permissions.

**iOS needs a model-preserving answer.**
iOS does not appear to provide the same straightforward background local daemon
shape that a desktop Hub uses.
That is not a complaint about the iOS ecosystem; it is an architectural
constraint Small Sea has to respect.
The ambition is for people to participate in many teams and use many apps, with
clear control over which software has access to which berths.
That authorization boundary is critical and cannot be left for each app to
reimplement.
Possible iOS shapes include a Network Extension hosting a tightly bundled app
set, a remote Hub the iOS app connects to over HTTPS, or some future pattern not
yet identified.
None is currently a committed roadmap item.

**The Home Hub is a Small Sea helper for technically capable users.**
It is its own value proposition for households or individuals with the comfort
to run a small server: a desktop that stays on, a NAS, a Pi-class home box, or
a small VPS.
It is not framed as the iOS workaround, though iOS users with such a setup can
use it.
The user runs a Hub on their own infrastructure and mobile or remote clients
connect to it over HTTPS.
This preserves the Hub as the policy gateway and avoids federation: there is
no global namespace, inter-Hub discovery fabric, or server-to-server social
protocol.
It is simply user-operated infrastructure for that participant or household.

A Home Hub is not just a relay.
If it holds Hub authority, cloud-provider access, or decrypted app state, it is
trusted infrastructure and must be hardened accordingly: TLS, strong device and
app pairing, narrow per-app/per-berth sessions, revocation for lost devices,
rate limiting, update hygiene, and visible access logs become part of the
minimum credible shape.
A detailed Home Hub threat model is important future work, not a prerequisite
for the desktop-first architecture.

### Research Notes from Other End-to-End Encrypted Products

These projects provide a little useful research context for deployment shape
and mobile.
Each made a different set of compromises; none is a model to copy wholesale,
and these notes should be revisited rather than treated as permanent claims.

- **Signal.**
  Beautiful E2EE on mobile achieved by accepting APNs/FCM push-metadata
  visibility and a primary-device coupling model for multi-device.
  Lesson: even the gold standard ships with named compromises; pretending
  otherwise is more dangerous than naming them.
- **Matrix / Element.**
  Real multi-device E2EE with cross-signing and key backup, and chronic
  "Unable to decrypt" mobile bugs as users hit edge cases in device management.
  Lesson: device-management UX bites harder than the cryptography.
- **Briar.**
  Excellent threat model, effectively no iOS presence, persistent
  manufacturer-battery-killer issues on Android.
  Lesson: strict infrastructure purity has real user-base costs.
- **Syncthing.**
  The closest direct analog to Small Sea on Android: foreground service, no
  Google Push by default, requires the user to whitelist the app on aggressive
  vendor OSes.
  Lesson: this works in production; the rough edges are user-facing setup, not
  protocol design.
- **Standard Notes, Notesnook, Obsidian Sync.**
  E2EE-claimed products that quietly accepted operator trust to make mobile
  signup and sync work.
  Lesson: "local-first" branding often hides a real operator role on mobile;
  Small Sea has not chosen this trade and should be explicit about that.

Future mobile work also needs a social-graph and notification-metadata threat
model.
Even when payloads are opaque, the fact that team X notified you at time Y may
be visible to whoever operates the push service or remote gateway.
This is not a problem to solve now, but it belongs on the architecture TODO
list rather than being quietly inherited.

### Database Access
**Only the Small Sea Manager reads the `SmallSeaCollectiveCore` database directly.** The `{team}/Sync/core.db` SQLite database is an internal implementation detail of the Manager.
The NoteToSelf-SmallSeaCollectiveCore berth specifically (the one holding device and identity state) is referred to as the **Core berth**. Other applications must obtain identity and session information through the Hub API (e.g., `GET /session/info`).

### App Bootstrap
Apps may request Hub sessions, but they do not register themselves. If an app
asks for a session before the participant or team has provisioned the relevant
berth, the Hub records a local sighting and returns a structured bootstrap
rejection. The Manager is the provisioning authority: it decides whether to
register the app for the participant, activate it for a team, suppress the
prompt on this device, or preserve ambiguity for human repair.

Participant-level registration and team-level activation are separate decisions.
The app's friendly name is a local claim and routing hint, not global identity.
If a friendly-name collision cannot be resolved simply and safely, the Hub must
surface ambiguity rather than choose a row implicitly.

Registration and activation authorize a berth; they do not make the Manager the
owner of an app's working tree. App data materialization is app-owned. The
Manager writes Core registration state, and the Hub reads that Core state by
framework contract, but arbitrary app homes are not Hub-readable databases.

### Security: PIN-Based Access

Before a client can access a berth, it must request a local session from the Hub.
The Hub generates a PIN and sends it to the user via OS notifications.
The user must enter this PIN into the client to complete the handshake.
This is a locally enforced client-to-Hub authorization boundary, not a team-wide decision about which teammate histories count.

## Terminology

- **Micro Tests**: We prefer the term "micro tests" over "unit tests." These are quick, frequent tests intended to catch simple mistakes during development.

## Per-Berth Integration Modes

The two integration modes — **automatic** and **proposal-only** — are defined under [No Team Server](#no-team-server) above, which is the canonical source.
This section elaborates what counts as a valid change under them, how authorship relates to integration, and how the current schema and removal flow approximate the model.

“Valid” still requires a recognized signer at the referenced state, the correct team and berth, a valid signature and causal base, app-specific structural validation, and any special domain rules.
Automatic integration is not permission to accept malformed or semantically invalid data.

Both modes may receive readable updates and author signed changes.
Readability remains endpoint-trust-scoped rather than cryptographically enforceable after keys or plaintext reach an admitted endpoint.
Integration mode answers what peers normally incorporate, not who is capable of writing bytes.
A proposal-only Core teammate can therefore sign a team-visible display-name proposal for an automatic Core integrator to endorse, while a purely local alias needs no team proposal.

The current schema stores `read-write` for approximately **automatic** and `read-only` for approximately **proposal-only**.
The current Hub watcher still discovers signals from every teammate, and the proposal-discovery mechanism does not yet exist.
Issue #162 tracks the runtime design needed before those stored values and UI labels can be renamed honestly.

The Manager-facing `steward` preset remains useful shorthand for automatic Core integration.
Protocol rules should still name automatic Core integrators when they mean anchor-relative standing that a verifier can replay.

“Remove teammate” therefore means appending a signed exclusion fact to the local Core lineage, publishing it, and rotating keys if future readable updates should exclude that person.
The earlier admission, device, and integration-mode records remain inspectable.
Other teammates may adopt that lineage, reject it, or race it with a conflicting lineage of their own.

Because Small Sea uses git history, maintaining a persistent split gets awkward
quickly. If Alice removes Carol and Carol removes Alice, the team has
effectively forked into two incompatible futures. Bob cannot comfortably remain
in both branches without some explicit translation layer. In practice, Small
Sea depends on social convergence to avoid or resolve such forks.

## Components

- **[Small Sea Hub](packages/small-sea-hub/README.md)**: Local service that mediates all access to general-purpose cloud services. Manages sessions, cloud storage proxying, notifications, and access control.
- **[Cuttlefish](packages/cuttlefish/README.md)**: Session-crypto layer. In production, the Hub uses Cuttlefish to encrypt and obscure team communication with cloud services.
- **[Wrasse Trust](packages/wrasse-trust/README.md)**: Identity and trust layer. Provides key hierarchies, certificates, ceremonies, revocations, and trust-chain evaluation for the web-of-trust model.
- **[Cod Sync](packages/cod-sync/README.md)**: Git-based synchronization protocol. Encodes deltas as a chain of git bundles uploaded to cloud storage.
- **[splice-merge](packages/splice-merge/README.md)**: Library for merging concurrent changes and resolving conflicts when automatic merging is not possible.
- **[Small Sea Client](packages/small-sea-client/README.md)**: Utility library for applications communicating with the Hub. Manages sessions and common workflows.
- **[Small Sea Manager](packages/small-sea-manager/README.md)**: The essential built-in application. Manages team membership, devices, cloud storage accounts, invitations, and the SmallSeaCollectiveCore database.
- **[Small Sea Collective Files](packages/ssc-files/README.md)**: Example application — team file sharing built on Small Sea.

## Typical Application Flow

1. **Session Start**: Client requests access to a berth from the local Hub.
2. **User Authorization**: User confirms access (via PIN/OS notification).
3. **Local Work**: Client performs operations on local state (e.g., a git repo).
4. **Bundle Creation**: Client creates a git bundle of new commits.
5. **Upload**: Hub encrypts and uploads the bundle to the user's cloud storage.
6. **Notification**: Hub sends a notification to teammates via a general-purpose service.
7. **Sync**: Teammates' Hubs download bundles and merge them into their local clones.
