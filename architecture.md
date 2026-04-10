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
Small Sea uses Signal-inspired cryptographic protocols ([X3DH](https://signal.org/docs/specifications/x3dh/) and [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/)) to manage identity and group membership. Teammates certify each other's identities, effectively building a decentralized web of trust. Key rotation helps exclude removed members from future readable updates.

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

### The Hub as the Sole Gateway
**All internet communication for Small Sea components must go through the Hub.** Applications, synchronization protocols, and internal packages must never make direct network calls to cloud storage, peers, or external services. This chokepoint enables transparent end-to-end encryption and consistent access control.

### Database Access
**Only the Small Sea Manager reads the `SmallSeaCollectiveCore` database directly.** The `{team}/Sync/core.db` SQLite database is an internal implementation detail of the Manager. Other applications must obtain identity and session information through the Hub API (e.g., `GET /session/info`).

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
  key exchange and future key rotation needed for that member to read updates
  in that berth.
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
