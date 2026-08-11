# Small Sea Collective Architecture

Small Sea Collective is a framework for building collaborative team applications on top of general-purpose cloud services (like Dropbox for storage or ntfy for notifications). It brings the local-first paradigm to team collaboration, ensuring that users own their data and do not depend on application-specific backend services.

## Core Concepts

- **Team**: The primary unit of collaboration.
  A technical origin separates one Constitution event DAG from unrelated histories.
  The identifier does not itself grant authority or settle social identity.
- **Application (App)**: A way to organize resources like storage, notifications, and identity. Apps are not specific client software but logical groupings of resources.
- **Berth**: The intersection of a specific team scope and a specific **App**.
  It is the fundamental unit of resource allocation, local client authorization, cryptographic readability, and teammate integration mode.
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

Git-style local choice is intentional.
Fetching a history, retaining an event, moving a local head, merging a branch, recognizing a teammate, and distributing future key material are different actions.
The core Constitution protocol does not combine them into one membership transition.

The Manager currently offers `steward` and `contributor` presets and stores `read-write` and `read-only` berth roles.
Those are current product policy, not protocol vocabulary.
Likewise, automatic integration, proposal-only integration, endorsement thresholds, and admission finalization may be useful policies without becoming core rules.

Local Hub authorization is different and real.
A participant's Hub enforces which client software may act in which berth on that device, protects provider credentials, and mediates Small Sea internet traffic.
Authorization language in this document refers to an enforced local boundary unless a passage explicitly says otherwise.

### Signed Constitutional Event DAG

The signed event DAG carried by Core is named the **Team Constitution**.
It is distinct from Core, which is the berth and database carrying that evidence.
[`Documentation/team-constitution.md`](Documentation/team-constitution.md) defines the narrow core protocol and its extension boundary.

The Constitution core is a content-addressed DAG of signed event envelopes.
It verifies canonical bytes, content IDs, signatures, technical-origin binding, and declared parent links.
It does not interpret an event as admission, authority, acceptance, exclusion, or consensus.
Those meanings belong to versioned extensions and local policy.

Each participant may hold a different subset of events and different local heads.
Concurrent descendants are ordinary.
No identifier, timestamp, database order, Git order, or arrival order chooses a canonical head.
A later event may name multiple parents without implying that every ancestor is socially accepted.

Git remains the snapshot, transport, versioning, and three-way-merge framework.
Constitution events carry domain signatures whose meaning survives synthesized Git merges.
Git authorship is not Constitution-event authorship, and a Git merge must not rewrite signed event bytes or parent links.
The Constitution is thus a signed event DAG carried inside the Git commit DAG that transports it, but the two are independent: their parent links are unrelated, and the transport layer is built to converge while the Constitution deliberately preserves concurrent heads.

The core makes narrow security claims: integrity, authorship by a key, replay separation, and declared ancestry.
It does not prove human intent, key authority, complete disclosure, membership, consensus, availability, or the safety of an extension's automatic behavior.

Extensions may define admission, device recovery, key distribution, integration modes, projections, retention, and repair.
They may disagree and evolve independently so long as they preserve core objects and do not weaken core verification.
An unknown extension event remains structurally verifiable and eligible for relay under local resource policy without acquiring local effect.

### Constitution Bases

The core verifies events; it does not tell an application how to interpret them.
The generic application contract therefore offers a small opaque bookmark for the Constitution view currently active on this device.
The bookmark carries no roster, role, threshold, or other team-policy result.

**Basis object.**
A **basis** is an unsigned canonical object containing a basis-format version, the technical origin, and the minimal set of active tip event IDs.
Any tip already reachable from another tip is omitted.
The Manager computes the basis from its active local Constitution heads after core verification.
Selecting those heads is a local storage and integration choice; the basis describes that choice without declaring it socially correct.

The active view, not the stored event set, is what a basis represents.
Two devices holding identical events produce different bases if one has parked a sibling the other selected.
Multiple tips are ordinary, and an application does not wait for the Constitution to acquire one head before asking for a basis.

Manager's integration policy bounds the number of active tips.
It assigns each newly core-verified event a local handling state only after assigning states to that event's parents; ordering among concurrently ready events remains a local choice.
An arriving batch is not activated atomically from its final tips.
When activating another branch would exceed that bound, Manager parks the branch and any later event whose ancestry includes it.
Consequently, a batch containing more concurrent branches than the bound parks at least one branch before a descendant merge is considered.
No received event reactivates parked ancestry — not even a multi-parent event that would bring the tip count back under the bound, because a flooding device can publish its own merge.
Unparking is a local acceptance decision:
a device that never parked the branches integrates such a merge as an ordinary collapse of its active parents, while a device that parked them surfaces the merge through the Hub as a proposed reconciliation to accept into a new bounded active view.
Recovery from a compromised device usually needs no unparking at all:
the team removes the device on a surviving branch and continues, and the parked flood stays parked under ordinary local resource budgets.
Parking remains visible and reversible without being reported as verification failure, and Manager makes it observable to the Hub so the Hub can notify the user.

The basis operation remains total over the bounded active view and never refuses or truncates that view.
This policy bound limits bases produced locally, while the basis format fixes a maximum tip count that recipients enforce on untrusted bookmarks.
Because the canonical object is a format version, one origin, and fixed-size tip IDs, the tip limit also determines a maximum encoded size.
A recipient rejects input exceeding the maximum encoded size before canonical decoding.
During decoding it rejects an over-limit tip count before allocating or consuming the tip entries.
Either violation makes the whole basis malformed; a recipient never partially accepts a basis.
The policy may use a lower active-tip bound but must not exceed the format's tip limit.

A **basis ID** is a full content digest over a domain-separated canonical basis object.
It gives the bookmark a stable identity for comparison, indexing, and deduplication.
It is not a promise that the basis object can be fetched from a separate registry.

**Application flow.**
An application asks the Hub for the current basis within its existing team-scoped session.
The Hub exposes the client-facing operation, while Manager-owned Core access computes the object.
The application receives the canonical basis as opaque bytes and embeds those exact bytes in the application data that cites it.
The application does not parse the tip set or run Constitution policy.

Carrying the bookmark with its citation keeps the contract self-contained.
No replicated basis registry, retention rule, or cache-coordination protocol is required.
An application may deduplicate repeated basis objects internally using the basis ID, but that is an application storage choice rather than part of the Constitution contract.

**Meaning and limits.**
A basis is unsigned and has no author.
An application event that embeds one claims only that the event was made against that view.
The application event's own signature, when it has one, authenticates the claim; the basis itself asserts nothing.

A basis names a view, not the moment of use.
The active view may advance between issuance and use, and keeping those acceptably close is the application's responsibility.
The Hub operation may later offer freshness checking, but the generic contract does not require it.

A recipient can always inspect the bookmark carried by an application record, but may lack part of the named event closure.
That makes the basis incomplete locally rather than false and does not permit substituting the recipient's current view or a policy projection that happens to produce the same answer.
The basis also names only the view selected for a decision, not everything its author knew or could have fetched.
No participant can prove that another disclosed every concurrent event it had observed.

The basis reveals its technical origin and tip set to every holder of the application data that carries it.
Those tips can be joined with the Constitution DAG to infer the roster, recovery, or other personal events selected by that view.
This disclosure follows the application data; there is no separate team-wide registry of historical view selections.

Policy-specific evaluators, recorded local inputs, joins, time attestations, cross-team compositions, and event-retention promises are separate extension or application designs.
They can later consume the same canonical basis object without changing this application contract.

## Technical Pillars

### 1. Fully Decentralized Team Management
Small Sea uses Signal-inspired cryptographic protocols ([X3DH](https://signal.org/docs/specifications/x3dh/) and [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/)) to manage identity and group membership. Teammates certify each other's identities, effectively building a decentralized web of trust.

**Read access is endpoint-trust-scoped.** Any admitted party — teammate or sibling device — can in principle proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this; it relies on the social commitment of admitted parties rather than a cryptographic enforcement boundary.

**Membership and device management are extensions over the Constitution DAG.**
The current Manager implements one inviter-orchestrated, transcript-bound admission policy and one sibling-device linking policy.
Their roles, thresholds, finalization rules, rotation behavior, and recovery ceremonies are not core Constitution semantics.
An extension that distributes future key material must define and verify the authorization policy for that irreversible action.

There is no central membership oracle or globally authoritative service.
Each participant maintains a local clone of the team's history and
therefore a local view of who is in the team and whose updates should count.
Those views can diverge.
The Constitution preserves evidence of that history; extensions and people decide what to do with it.

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

Core uses the same forward-only Git rule.
Any stronger event-retention or projection-repair rule belongs to the Constitution storage implementation or to an extension, not to the event envelope.

## Design Principles & Constraints

### Human-Scale Coordination

Small Sea optimizes first for small teams and human-paced collaboration, not for
large-scale, low-latency consensus. Several dozen teammates should be treated
as a soft upper bound for a single team; larger communities should usually be
modeled as multiple related teams.

Small team size does not make partitions, equivocation, compromised devices, flooding, or ambiguous authority disappear.
It does make human inspection and reconciliation practical when an extension cannot resolve a conflict safely.

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

Device-related fields are placed by authority and lifecycle rather than collected into one device record.
The signed history associating a device key with a teammate is durable team state.
A participant's labels for their own devices belong in shared NoteToSelf state and do not affect trust.
Last-seen, reachability, and sync success are observations owned by the observing device or Hub, not team facts.
Peer storage routing is teammate-and-berth state rather than an intrinsic property of the announcing device.

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
Manager owns Core registration state, and the Hub obtains the framework state
it needs through a Manager-owned boundary.
Arbitrary app homes are not Hub-readable databases.

### Security: PIN-Based Access

Before a client can access a berth, it must request a local session from the Hub.
The Hub generates a PIN and sends it to the user via OS notifications.
The user must enter this PIN into the client to complete the handshake.
This is a locally enforced client-to-Hub authorization boundary, not a team-wide decision about which teammate histories count.

## Terminology

- **Micro Tests**: We prefer the term "micro tests" over "unit tests." These are quick, frequent tests intended to catch simple mistakes during development.

## Per-Berth Integration Modes

The current Manager/Hub policy uses two integration modes: **automatic** and **proposal-only**.
They are an extension above the Constitution core.

Structural ingestion still requires the correct team and berth, a valid signature and causal base, app-specific validation, and any special domain rules.
The Manager/Hub extension decides whether the signer is recognized and the change has local effect.
Automatic integration is not permission to accept malformed or semantically invalid data.

Both modes may receive readable updates and author signed changes.
Readability remains endpoint-trust-scoped rather than cryptographically enforceable after keys or plaintext reach an admitted endpoint.
Integration mode answers what peers normally incorporate, not who is capable of writing bytes.
A proposal-only Core teammate can therefore sign a team-visible display-name proposal for an automatic Core integrator to endorse, while a purely local alias needs no team proposal.

The current schema stores `read-write` for approximately **automatic** and `read-only` for approximately **proposal-only**.
The current Hub watcher still discovers signals from every teammate, and the proposal-discovery mechanism does not yet exist.
Issue #162 tracks the runtime design needed before those stored values and UI labels can be renamed honestly.

The Manager-facing `steward` preset remains current shorthand for automatic Core integration.
It is not a Constitution-core key class.

“Remove teammate” is also Manager/Hub and encryption-extension policy.
The Constitution core can carry a signed event for that extension, but it does not decide when removal takes effect or which keys rotate.
Conflicting removal events remain concurrent evidence until local policy or people resolve them.

## Components

- **[Small Sea Hub](packages/small-sea-hub/README.md)**: Local service that mediates all access to general-purpose cloud services. Manages sessions, cloud storage proxying, notifications, and access control.
- **[Cuttlefish](packages/cuttlefish/README.md)**: Session-crypto layer. In production, the Hub uses Cuttlefish to encrypt and obscure team communication with cloud services.
- **[Wrasse Trust](packages/wrasse-trust/README.md)**: Identity and trust layer. Provides key hierarchies, certificates, ceremonies, revocations, and trust-chain evaluation for the web-of-trust model.
- **[Cod Sync](packages/cod-sync/README.md)**: Git-based synchronization protocol. Encodes deltas as a chain of git bundles uploaded to cloud storage.
- **[splice-merge](packages/splice-merge/README.md)**: Library for merging concurrent changes and resolving conflicts when automatic merging is not possible.
- **[Small Sea Client](packages/small-sea-client/small_sea_client/)**: Utility library for applications communicating with the Hub. Manages sessions and common workflows.
- **[Small Sea Manager](packages/small-sea-manager/spec.md)**: The essential built-in application. Manages team membership, devices, cloud storage accounts, invitations, and the SmallSeaCollectiveCore database.
- **[Small Sea Collective Files](packages/ssc-files/README.md)**: Example application — team file sharing built on Small Sea.

## Typical Application Flow

1. **Session Start**: Client requests access to a berth from the local Hub.
2. **User Authorization**: User confirms access (via PIN/OS notification).
3. **Local Work**: Client performs operations on local state (e.g., a git repo).
4. **Bundle Creation**: Client creates a git bundle of new commits.
5. **Upload**: Hub encrypts and uploads the bundle to the user's cloud storage.
6. **Notification**: Hub sends a notification to teammates via a general-purpose service.
7. **Sync**: Teammates' Hubs download bundles and merge them into their local clones.
