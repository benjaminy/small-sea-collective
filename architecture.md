# Small Sea Collective Architecture

Small Sea Collective is a framework for building collaborative team applications on top of general-purpose cloud services (like Dropbox for storage or ntfy for notifications). It brings the local-first paradigm to team collaboration, ensuring that users own their data and do not depend on application-specific backend services.

## Core Concepts

- **Team**: The primary unit of collaboration. In Small Sea, teams are decentralized; there is no central registry.
- **Application (App)**: A way to organize resources like storage, notifications, and identity. Apps are not specific client software but logical groupings of resources.
- **Berth**: The intersection of a specific **Team** and a specific **App**. It is the fundamental unit of resource allocation and access control.
- **Client**: Any software (GUI, CLI, agent) that accesses resources through the Small Sea Hub.
- **Hub**: A local service that mediates all access to general-purpose cloud services. It acts as a security gateway and protocol translator.

## Technical Pillars

### 1. Fully Decentralized Team Management
Small Sea uses Signal-inspired cryptographic protocols ([X3DH](https://signal.org/docs/specifications/x3dh/) and [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/)) to manage identity and group membership. Teammates certify each other's identities, effectively building a decentralized web of trust.

**Read access is endpoint-trust-scoped.** Any admitted party — teammate or sibling device — can in principle proxy plaintext or hand over receiver state to anyone they choose. The protocol cannot prevent this; it relies on the social commitment of admitted parties rather than a cryptographic enforcement boundary.

**Key rotation serves two purposes: exclusion and hygiene.** Exclusion handles removal and post-admission objections, both via the same rotate-with-exclusion primitive. Hygiene is routine and semantically neutral. Rotation is never used to admit a new party.

**Linked-device admission is a unilateral identity-owner act.** An existing sibling device bootstraps the new device by handing off current team state and the sibling's snapshot of peer sender keys. The sibling issues a `device_link` cert over the new device's concrete public keys and publishes it to the team DB. Other teammates observe the new device via the published cert; objection is handled post-hoc by exclusion. The new device's access is join-time-forward: it reads from what the sibling held at bootstrap time and does not receive historical ciphertext encrypted before the cert was published.

**Teammate admission is an inviter-orchestrated, transcript-bound, admin-quorum flow.**

- *Governance-snapshot anchor.* Every proposal is anchored to a verifiable team-history reference (the team's `Sync/core.db` commit hash). The anchor freezes the admin roster, membership roster, and member→device mapping. Every participant can independently replay team history to the anchor and verify the frozen state.
- *Proposal shell published at initiation.* The inviter allocates a fresh UUIDv7 `member_id` for the invitee and publishes a proposal shell to team DB before the invitee is contacted. Other admins in the frozen governance set see the proposal immediately and can withhold approval or object before the invitee has invested any effort.
- *Transcript binding.* The invitee generates fresh keys and signs an acceptance blob binding to the inviter-allocated `member_id`. The inviter assembles the full admission transcript over the invitee's concrete device keys and the allocated `member_id`. Transport metadata (cloud endpoints) is explicitly excluded from the immutable transcript; post-admission transport setup is a separate flow.
- *Member/device approval bridge.* Each admin approval is a member-scoped vote executed by a device-key signature. An approval is valid iff the signing key appears in a `device_link` cert at the anchor that maps to a current-admin `member_id`. This bridge is a step-by-step derivation any verifier can replay: cert chain at the anchor → device key → member ID → admin roster check. Approvals from devices linked after the anchor, or from non-admins at the anchor, are rejected. Multiple approvals from different devices of the same admin dedupe to one vote.
- *Inviter-published finalization.* The inviter observes quorum met and publishes the finalization mutation. The invitee never publishes their own admission. `quorum = 1` is the default; the inviter's own approval alone meets quorum and the end-to-end flow reduces to Alice-initiates → Bob-returns-signed-transcript → Alice-approves-and-publishes.
- *Non-durable proposals.* Proposals are invalidated by any governance-state change relative to the anchor: admin roster changes, membership roster changes, or member→device mapping changes. Proposals also expire after a per-team window. An invalidated proposal cannot be finalized; it is not a durable bearer capability.

There is no central membership oracle and no globally authoritative admin
service. Each participant maintains a local clone of the team's history and
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

### Database Access
**Only the Small Sea Manager reads the `SmallSeaCollectiveCore` database directly.** The `{team}/Sync/core.db` SQLite database is an internal implementation detail of the Manager. Other applications must obtain identity and session information through the Hub API (e.g., `GET /session/info`).

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

### Security: PIN-Based Access
Before a client can access a berth, it must request access from the Hub. The Hub generates a PIN and sends it to the user via OS notifications. The user must enter this PIN into the client to complete the handshake, ensuring that only authorized software can access team data.

## Terminology

- **Micro Tests**: We prefer the term "micro tests" over "unit tests." These are quick, frequent tests intended to catch simple mistakes during development.

## Permissions

For each berth, a member can have either **read-only** or **read-write**
access. These are enforced as a social contract via encryption and sync
conventions rather than by a central authority.

In concrete technical terms:

- **Read permission** means peers participating in the protocol should do the
  key exchange needed for that member to read updates in that berth. This is a
  protocol convention enforced by social contract, not a cryptographic
  enforcement boundary: admitted parties can in principle proxy plaintext or
  receiver state out of band.
- **Write permission** means peers participating in the protocol should pay
  attention to that member's updates for that berth and merge them into their
  own clone.

A common shorthand organization is:

- **Admin**: Read-write to all berths, including the team's Core berth (team metadata).
- **Contributor**: Read-write to all berths _except_ Core (their changes to team metadata are ignored by peers following the conventional role mapping).
- **Observer**: Read-only to all berths.

`Admin` is not a special cryptographic authority. It is just shorthand for
"has write permission to `{Team}/SmallSeaCollectiveCore`", the berth where
membership and berth-role data live.

"Remove member" therefore means: remove that person from my local clone of the
team DB, push that change, and rotate keys if I want future readable updates to
exclude them. Other teammates may adopt that view, reject it, or race it with a
conflicting view of their own.

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
- **[Shared File Vault](packages/shared-file-vault/README.md)**: Example application — team file sharing built on Small Sea.

## Typical Application Flow

1. **Session Start**: Client requests access to a berth from the local Hub.
2. **User Authorization**: User confirms access (via PIN/OS notification).
3. **Local Work**: Client performs operations on local state (e.g., a git repo).
4. **Bundle Creation**: Client creates a git bundle of new commits.
5. **Upload**: Hub encrypts and uploads the bundle to the user's cloud storage.
6. **Notification**: Hub sends a notification to teammates via a general-purpose service.
7. **Sync**: Teammates' Hubs download bundles and merge them into their local clones.
