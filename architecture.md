# Small Sea Collective Architecture

Small Sea Collective is a framework for building collaborative team applications on top of general-purpose cloud services (like Dropbox for storage or ntfy for notifications). It brings the local-first paradigm to team collaboration, ensuring that users own their data and do not depend on application-specific backend services.

## Core Concepts

- **Team**: The primary unit of collaboration.
  A stable technical origin groups related evidence, while living team identity is an observer-relative judgment refreshed through signed activity and relationships.
  There is no central registry or eternal identifier that decides which continuation is the real team after a split.
- **Application (App)**: A way to organize resources like storage, notifications, and identity. Apps are not specific client software but logical groupings of resources.
- **Berth**: The intersection of a specific technical team scope and a specific **App**.
  It is the fundamental unit of resource allocation, local client authorization, cryptographic readability, and teammate integration mode.
  A durable split may eventually fork that operational scope without deciding which continuation owns the original social identity.
- **Client**: Any software (GUI, CLI, agent) that accesses resources through the Small Sea Hub.
- **Hub**: By default, a local service that mediates all access to general-purpose cloud services.
  It acts as a security gateway and protocol translator.
  Experimental deployment shapes are discussed under "Hub Deployment Shapes."

A berth is technically `team scope x App`; a participant is not a third berth
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

Nine questions are easy to blur together:

- **Authenticity** asks whether canonical bytes were signed by the claimed device key and bound to the correct team.
- **Causal authority** asks what teammate and device evidence supported that key at the state the record references.
- **Visibility** asks which signed record and declared causal closure a participant has attested to obtaining and structurally verifying.
- **Acknowledgment** asks whether a participant explicitly accepts or ratifies a claim for a stated purpose.
- **Reliance** asks what evidence a participant or application acted upon.
- **Readability** concerns which teammates receive the key material needed to interpret future berth updates.
- **Integration mode** determines whether a teammate's ordinary berth publications are accepted automatically or only through explicit proposals.
- **Replication and discovery** concern how publications and proposals are noticed and fetched.
- **Local effect** is what one participant's current versioned analysis permits their Manager, Hub, and applications to do.

Only integration mode is a per-berth teammate category in this model:

- **Automatic** means peers following the policy are expected to monitor the teammate's ordinary berth publications and integrate every valid change by default.
- **Proposal-only** means peers are not expected to monitor ordinary berth publications from that teammate.
  The teammate may instead sign a change proposal, which becomes eligible for integration after endorsement by the berth's required number of automatic integrators.

A proposal-integration policy uses at least one automatic integrator under the named local analysis and may require additional acknowledgments.
Constitutional admission evidence is not globally finalized by that threshold.

Both modes describe teammates whom a particular local analysis recognizes as able to read, author, and sign data.
The mode changes what peers are expected to integrate, not whether the teammate can produce a change.
The current `read-write` and `read-only` schema values approximate `automatic` and `proposal-only` respectively; renaming those stored values is deferred until the proposal mechanism exists.
When an analysis needs replayable authority at a Core causal context, it should say **automatic Core integrator under that analysis** rather than treating a Manager role name as protocol state.
The current Manager-facing `steward` preset is shorthand for automatic integration on Core, but it is not a target default, central authority, or special key class.

These terms live on two deliberately separate layers, and the distinction is a standing convention rather than loose synonymy:

- **automatic Core integrator** — the *analysis* term.
  It is a causal-context-relative standing derived from signed evidence plus explicit local acceptance inputs.
  Protocol rules must name the analysis or policy assumptions under which that standing is claimed.
  "Core integrator" is an accepted short form; it always implies automatic mode, since proposal-only teammates do not integrate.
- **`steward` / `contributor`** — *current Manager-facing presets*.
  They are convenience bundles a human picks at invitation time (`steward` = automatic everywhere; `contributor` = proposal-only on Core, automatic elsewhere).
  They are not protocol state or retained target defaults.
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
Constitution head discovery must also remain possible without treating a discovered branch as accepted authority.

Local Hub authorization is different and real.
A participant's Hub enforces which client software may act in which berth on that device, protects provider credentials, and mediates Small Sea internet traffic.
Authorization language in this document refers to an enforced local boundary unless a passage explicitly says otherwise.

### Signed, Append-Only Constitutional Evidence

The retained signed evidence carried by Core is named the **Team Constitution**.
It is distinct from Core, which is the berth and database carrying that evidence.
[`Documentation/team-constitution.md`](Documentation/team-constitution.md) defines the target evidence model, shared record envelope, causal requirements, and record families.

The Constitution is a DAG of signed claims, not one linear roster and not a replicated value that must eventually converge.
Admissions, device links and revocations, acknowledgments, interaction attestations, delegations, recovery events, integration-mode claims, exclusions, repudiations, ratifications, staleness observations, proposals, and endorsements append evidence rather than overwrite prior claims.
Concurrent descendants remain visible.
No identifier order, timestamp, table order, or row-arrival order selects a winner.

Every participant may apply local or post-hoc analyses to the DAG.
Two analyzers with the same evidence, local acceptance inputs, and policy must be deterministic, but different analyses may answer different questions or reach different judgments.
There is no requirement that the evidence ever resolve to one true state.
Persistent disagreement may be an honest team split.

Mutable tables and UI models are rebuildable projections of a named analysis.
They are not durable team truth and must retain enough provenance to explain the analysis name and version, evidence frontier, acceptance choices, and policy that produced them.
A reproducible decision basis includes digests of the evidence closure, local inputs, and resulting canonical projection.
An implementation may expose an ephemeral local revision for cache invalidation or display, but it may reset after restart or rebuild and is never a durable identifier, global team clock, or way to choose between competing continuations.
The projection digest alone is insufficient because different evidence closures may currently produce the same roster.
A canonical projection is invariant to row order and irrelevant reordering of causally independent evidence; unresolved incompatible evidence becomes an explicit deterministic conflict rather than an arbitrary winner.
A record's signature proves what a device key signed; it does not by itself prove human intent, real-world identity, social acceptance, or externally binding authority.

Evidence can strengthen through duration.
Carol may first accept Alice's choice to admit Bob, later meet Bob and compare device-bound material in person, later rely on Bob's work, and later acknowledge a scoped delegation.
Those are separate facts.
The protocol must not silently collapse them into the initial admission ceremony.

Git remains the snapshot, transport, versioning, and three-way-merge framework.
Constitution records carry domain signatures whose meaning survives synthesized Git merges and later historical analysis.
Git provenance and signed domain provenance may complement one another, but Git commit authorship alone is never constitutional authority.
The Constitution does not require arbitrary application or Cod Sync content to carry constitutional signatures.
Each application decides whether its repair is based on user judgment, author-asserted provenance, or application-defined cryptographic provenance.

Every current Core database snapshot produced by a clone contains the complete Constitution evidence that clone has adopted into its live database.
Constitutional analyses must therefore be possible from current Core data without checking out historical blobs or consulting a separate log service.
Local Core adoption is the retention decision: an adopted Constitution object and its declared causal closure are non-prunable in later states produced by that clone.
Fetching a Git commit or attempting a Git merge is not by itself adoption.
Adoption is not acceptance and grants no local effect by itself.
Unsolicited authentic input has no automatic entitlement to effect or adoption and may be bounded or parked under resource policy at the publication and intake boundaries.
This is a clone-local data-model promise, not physical immortality, universal availability, or a claim that every clone holds the same evidence.
It is also an obligation on a well-behaved implementation rather than a property peers can check: a clone that never adopted a record and a clone that adopted it and dropped it publish the same absence.
An analysis may therefore treat a missing record as unavailable, never as proof that it never existed.

The complete Git commit DAG is also retained.
Cod Sync may compact its transport chain and dehydrate old non-constitutional blobs beyond a live-data window, but it does not rebase, reset, replace commit identities, or discard Constitution objects adopted by that clone.
Separable personal payloads may also leave the rehydratable window without invalidating their retained signed commitments.
The window is a shared content-retention practice, not an erasure guarantee.

A proposal preserves the proposer's signature over its exact payload.
A typed acknowledgment or ratification names the exact record or digest it accepts.
Changing a proposal creates new evidence rather than silently mutating what an earlier signer approved.

### Direct Identity Payload Is Separable; Metadata Remains

The retained Constitution DAG is deliberately limited to a governance *skeleton*: per-team participant UUIDs, device public keys, causal references, typed actions and acknowledgments, hiding commitments, and the edges among those objects.
Structural and authority analyses branch only on that skeleton.
Personally identifying information — display names, identity material attached at admission, free-text reasons, and similar human-readable labels — is intentionally not part of the durable governance skeleton.
The system can try to keep that content outside the always-retained Core event stream and outside non-dehydratable Git objects, but it cannot promise that no copy exists once a teammate has received a snapshot.

The skeleton itself still carries indirect personal information.
Stable pseudonyms, device counts, causal timing, relationship edges, delegations, and disputes may identify people or expose sensitive social structure.
The architectural promise is therefore minimization and separability of direct identity payload, not the absence of personal data from retained evidence.

This is a deliberate invariant, not an oversight.
Three reasons make it load-bearing:

- A complete append-only evidence DAG cannot be selectively erased without breaking later analysis, so anything that may later have to be withheld, encrypted, or excised from the ordinary retained dataset must not live in the retained skeleton.
- Real-world identity is observer-relative: the chain can attest what a UUID *did*, but who that UUID *is* belongs to each participant's own knowledge, which is expected to accrete through interaction over time rather than being fixed by a one-time record.
- Keeping direct identity payload out of the retained skeleton is what lets pseudonymous participation be the safer default rather than a conspicuous opt-out.

When a team does want identity material attached to a membership change, the intended design has it ride *outside* the skeleton as inert payload.
The mechanism is not yet settled — the commitment scheme in particular still needs cryptographic analysis — but it must satisfy these properties:

- The chain stores only a hiding commitment to the payload, so the retained commitment does not leak low-entropy content such as a name. A bare `hash(name)` is not hiding for low-entropy input, so the commitment must be salted or otherwise randomized.
- Signatures cover the commitment, never the raw payload, so the payload can be encrypted to a current-membership window or dropped entirely without invalidating any retained record.
- Structural verification and authority analyses never read the payload.
  A participant who cannot read it — a future teammate, or anyone after excision — obtains the same result from the same skeleton and analysis inputs.

The consequence is explicit and accepted: once such a payload is excised, there is no way to recover what it was from the retained record alone.
The retained record then proves only that the signers committed to and approved *some* payload with the recorded commitment, bound to a specific UUID's membership change.
If someone later presents a candidate payload and the commitment opening, the record may verify that candidate, but forensic reconstruction of the payload's content does not survive its excision.
The exact commitment scheme, the optional encryption-window key schedule, and the interaction-based identity-confidence model are mechanism details tracked in [`Documentation/open-architecture-questions.md`](Documentation/open-architecture-questions.md).
Every record family must separately justify the indirect metadata its existence adds to retained evidence and consider whether the same fact could remain local, pairwise, less precise, or unpublished.

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

### Core as Constitutional Evidence

A constitutional claim is incomplete without its causal context and the analysis under which someone wants to give it effect.
“Alice is a Core integrator” may be true under Carol's accepted evidence and false under Dave's, even when both hold authentic signed records.

Structural validity, branch-relative authority, acknowledgment, reliance, and local effect remain distinct.
Authentic evidence does not change another participant's clone by fiat.
Each participant may publish what they accept, reject, repudiate, or ratify in their own clone.
Other participants may acknowledge those claims or decline to do so.

Ordinary app-berth divergence can be routine and healthy.
Constitutional disagreement is more consequential because it affects whose future actions an analysis may accept, but the protocol still does not fabricate a single winner.
Append-only storage preserves every side.
If participants cannot reconcile their analyses, the result is a team split.

A split does not leave one objectively real owner of the original team name or technical identifier.
It leaves multiple living continuations with shared ancestry.
Participants and external counterparties decide which continuation they recognize through signed activity and relationships.
The continuations may need distinct operational namespaces and sender-key state so incompatible membership analyses do not continue sharing one cryptographic or routing scope; creating those namespaces makes no claim about which continuation is socially legitimate.
Because rival continuations legitimately sign with the same technical origin, `team_id` is a routing and storage convenience that carries no authorization weight on its own.
Every effectful path — app-berth integration, key distribution, storage selection — consults the local analysis rather than treating the shared identifier as a membership check.

The common case may be broad social convergence, but convergence is an outcome of accumulated evidence and human agreement rather than a protocol finality requirement.
Post-hoc analyses may remain useful even after active participants converge on a practical working projection.
Old evidence remains cryptographically authentic, but authenticity alone does not settle whether a long-quiet teammate or continuation should be treated as able to act now.
The protocol bakes in no dormancy threshold; it surfaces last observed activity and staleness evidence so people and local analyses can make that judgment openly.
Apparent dormancy can be manufactured by hiding someone's activity, so dormancy always means "not observed here," never "not active anywhere."
Any withered confidence is a local judgment, not deletion or retroactive invalidation of history.

An ordinary departure and a repudiated admission therefore share low-level facts but differ socially.
A prospective departure preserves the standing that an analysis attributed to Bob before departure.
A repudiation names Bob's admission and declines to recognize the standing derived from it.
Neither erases Bob's signatures, Alice's admission, or evidence that Carol relied on them.
Specific Bob-originated acts may later be ratified independently.

### Retention Horizons and Staleness

Keeping the Git commit DAG does not require keeping every historical bulk blob immediately rehydratable.
A live-data window may bound the non-constitutional content needed to reconstruct recent states while preserving commit identities and the locally adopted Constitution evidence carried by every current Core snapshot produced by that clone.
Adopted Constitution objects are non-prunable in later states of that clone; separable personal payloads and never-adopted authentic input do not inherit that promise merely by arriving.

Occasionally a recognized teammate may be quiet or unable to publish for a long time while the rest of the team continues moving.
A useful candidate record is a signed Core staleness observation such as: “Alice has not observed Bob's berth clone advance since this head, for this much local time or this many accepted updates, and expects the live-data window to advance past it soon.”
The record should identify the observer, the teammate and berth observed, the last observed state, and objective counters or references where available.

A staleness observation is evidence and warning, not a command, exclusion, or unilateral declaration of finality.
Different teammates may have different observations because they have seen different network and clone states.
Recording those observations could make a later reconvergence attempt easier to diagnose and give a quiet teammate time to catch up before older bulk data is pruned.
It cannot itself advance another participant's retention horizon or recreate data that has already been discarded.

Any rule that turns a retention horizon into a checkpoint used by a particular analysis needs an explicit signed protocol and validation rule.
Until that rule exists, implementations should preserve and surface late or divergent history rather than silently treating staleness as consent to abandon it.

## Technical Pillars

### 1. Fully Decentralized Team Management
Small Sea uses Signal-inspired cryptographic protocols ([X3DH](https://signal.org/docs/specifications/x3dh/) and [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/)) to manage identity and group membership. Teammates certify each other's identities, effectively building a decentralized web of trust.

**Read access is endpoint-trust-scoped.** Any admitted party — teammate or sibling device — can in principle proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this; it relies on the social commitment of admitted parties rather than a cryptographic enforcement boundary.

**Key rotation serves two purposes: containment and hygiene.**
A participant who accepts an exclusion or repudiation rotates with the relevant teammate or devices excluded from future redistribution.
Hygiene is routine and semantically neutral.
Rotation is never used to admit a new party or to erase earlier disclosure.

**Suspension can precede socially recognized eviction.**
A participant who detects a lost, compromised, or anomalous device may immediately stop integrating it and stop sending it future key material under local policy.
That containment does not erase the device's authentic evidence or force other participants to accept a revocation.
Per-device intake budgets and additional recent acknowledgments for high-amplification actions limit damage while signed compromise or exclusion evidence propagates.

**Linked-device admission is a unilateral identity-owner act.**
An existing sibling device bootstraps the new device by handing off current team state and the sibling's snapshot of peer sender keys.
The sibling issues a `device_link` cert over the new device's concrete public keys and publishes it to the team DB.
Other teammates observe the new evidence and decide its effect under their local analyses; objections may produce exclusion or repudiation evidence.
The new device's access is join-time-forward: it reads from what the sibling held at bootstrap time and does not receive historical ciphertext encrypted before the cert was published.

**Teammate admission is an inviter-orchestrated, transcript-bound evidence flow.**

- *Causal context.* Every proposal names the minimum Constitution evidence its meaning requires; fuller disclosure rides as separate visibility evidence.
  Git commit identity may help locate the surrounding snapshot, but record references carry the durable meaning.
  Later analyses may surface selective anchoring, such as acting against an older basis after acknowledging newer relevant evidence, without treating that pattern alone as proof of deception or automatic invalidity.
- *Proposal shell published at initiation.* The inviter allocates a fresh UUIDv7 `teammate_id` and publishes signed intent before contacting the invitee.
  Other participants can observe, acknowledge, object to, or ignore that claim.
- *Transcript binding.* The invitee generates fresh keys and signs acceptance binding the inviter-allocated `teammate_id`, proposal, team, and nonce.
  The completed transcript covers the concrete device keys and excludes transport metadata.
- *Inviter acknowledgment.* The inviter publishes the completed transcript and explicitly acknowledges their choice.
  This proves what the inviter's device attested; it is not global finalization.
- *Independent evidence.* Other participants may acknowledge the admission based on the inviter's judgment, later add direct interaction attestations about Bob, or publish objections and repudiations in their own clones.
- *Local activation policy.* A recoverable analysis may give the inviter's acknowledgment immediate local effect.
  A guarded analysis may wait for more independent acknowledgments before distributing future keys or automatically integrating Bob's work.
- *Concurrency tolerance.* Later or concurrent governance evidence does not make the signed proposal cease to exist or become malformed.
  Each analysis decides whether the causal context and competing evidence are sufficient for local effect.

Admission does not automatically grant automatic Core integration or authority to represent the team externally.
Those are separate, scoped claims whose evidence may accumulate over time.
This separation limits how far one mistaken admission can poison the team while still permitting optimistic application collaboration where a participant accepts the risk.

There is no central membership oracle, no globally authoritative service, no globally authoritative automatic Core integrator, and no required one-true projection of the Constitution DAG.
Each participant maintains a local clone of the team's history and
therefore a local view of who is in the team and whose updates should count.
Those views can diverge.
Shared history and sync conventions make social convergence possible, but the protocol neither requires it nor pretends to eliminate disagreement.

The same distinction applies to devices: joining an existing **identity**
through NoteToSelf is not the same thing as joining every **team** known to
that identity. A new device may become part of Alice's identity first, learn
about Alice's teams from NoteToSelf, and then join only some subset of those
teams later.

### 2. Snapshot-Based 3-Way Merge (Git)
The baseline synchronization method is snapshot-based 3-way merge, utilizing `git`.
While slower than CRDTs, it preserves causal history and provides a practical basis for explicit human repair and adaptation of existing software.

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

Cod Sync never repairs shared Git history by resetting a branch or moving a ref backward.
When a participant wants to restore an older application state, they append a new commit whose tree contains that state.
The old commits remain ancestors of the repair commit, so the system can calculate which intervening changes were overwritten and publish later replay commits for the changes people still want.
A repair series may be staged locally and published together, but every published operation advances history.
Cod Sync does not determine who authored the overwritten changes or whether replay is honest.
Applications own those semantics and may offer anything from user-directed restoration to cryptographically attributable replay.

Core uses the same forward-only Git rule, but its current database must still contain all Constitution objects that clone adopted.
A Core repair therefore appends repudiation, reconciliation, or ratification evidence and rebuilds a local projection; it never restores an old Core database that omits later constitutional evidence.

## Design Principles & Constraints

### Human-Scale Coordination

Small Sea optimizes first for small teams and human-paced collaboration, not for
large-scale, low-latency consensus. Several dozen teammates should be treated
as a soft upper bound for a single team; larger communities should usually be
modeled as multiple related teams.

Small team size does not make two-party partitions, equivocation, compromised devices, authentic-input flooding, or ambiguous authority disappear.
It lets Small Sea buy safety with techniques that do not scale: broad head gossip, non-pruning of locally adopted constitutional evidence, conspicuous acknowledgments, expensive local analysis, and human reconciliation of rare conflicts.
An algorithm that becomes operationally unpleasant at hundreds of teammates may be acceptable.
A safety rule that grants authority by lucky message order with three teammates is not.

This scale assumption is an architectural constraint. When a conflict,
identity collision, or ambiguous sync result cannot be resolved simply and
safely, the system should preserve the competing states and make the ambiguity
visible rather than inventing a brittle automatic winner. A Hub rejection, a
Manager prompt, or a parked git branch is often the correct result.

The strongest general convergence promise is **eventual visibility**, not one true eventual state.
Under eventual communication, authentic branches observed by honest participants should become visible to other reachable participants, and a previously observed head must not silently disappear.
Participants may still reject one another's interpretation or reconciliation forever.

A full visibility acknowledgment of head `X` is an accountable signed claim that its signer obtained and structurally verified `X` plus the transitive closure of `X`'s declared causal frontier.
It is not a cryptographic proof of possession, does not accept that evidence, does not prove that `X` disclosed everything its author knew, and does not reveal a concurrent sibling head by itself.
Head gossip and missing-dependency retrieval therefore remain distinct from effectful integration.

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

“Valid” is incomplete unless the speaker names the analysis.
Structural ingestion still requires the correct team and berth, a valid signature and causal base, app-specific validation, and any special domain rules.
Whether the signer is recognized and the change has local effect depends on causal evidence and explicit local analysis inputs.
Automatic integration is not permission to accept malformed or semantically invalid data.

Both modes may receive readable updates and author signed changes.
Readability remains endpoint-trust-scoped rather than cryptographically enforceable after keys or plaintext reach an admitted endpoint.
Integration mode answers what peers normally incorporate, not who is capable of writing bytes.
A proposal-only Core teammate can therefore sign a team-visible display-name proposal for an automatic Core integrator to endorse, while a purely local alias needs no team proposal.

The current schema stores `read-write` for approximately **automatic** and `read-only` for approximately **proposal-only**.
The current Hub watcher still discovers signals from every teammate, and the proposal-discovery mechanism does not yet exist.
Issue #162 tracks the runtime design needed before those stored values and UI labels can be renamed honestly.

The Manager-facing `steward` preset remains current shorthand for automatic Core integration.
It is not a target default or a constitutional key class.
Analysis rules should name automatic Core integrators only with the causal context and local inputs under which that standing is derived.

“Remove teammate” may mean a prospective exclusion or a repudiation of a named admission.
Either operation appends signed evidence to the author's clone and rotates keys if that participant wants future readable updates to exclude the person.
The earlier admission, device, acknowledgment, publication, reliance, and integration-mode records remain inspectable.
Other teammates may acknowledge the claim, reject it, or publish conflicting evidence of their own.

If Alice excludes Carol and Carol excludes Alice, they may derive incompatible futures from authentic evidence.
Bob may accept one, maintain separate analyses of both, or add reconciliation evidence.
If both futures continue operating, they may need distinct cryptographic and routing namespaces even though they share a technical origin and historical ancestry.
The protocol does not require social convergence and does not hide a persistent team split.

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
