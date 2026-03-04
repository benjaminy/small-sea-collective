---
id: small-sea-hub
version: 1
status: experimental
---

## Purpose

The Hub is a local service that runs on each user's device.
It is the sole gateway between Small Sea applications and the outside world.
Apps never make network calls directly; they go through the Hub.

The Hub has two main jobs:
1. Mediate access to general-purpose cloud services (storage, notifications, VPN, etc.) on behalf of apps.
2. Gate that access through sessions, so users can control which apps access which stations.

## Sessions

Sessions are how apps gain access to Hub services.
A session is scoped to exactly one station (one team + one app).

TODO: Describe the session lifecycle — opening, approval, duration, expiry.

## Relationship with the Team Manager

The Hub has a special relationship with the Team Manager app.
The Team Manager writes the databases that the Hub reads to do its work: team membership, app registrations, cloud storage credentials, etc.

TODO: Specify the shared database contract — which tables the Hub reads, where they live on disk.

## Cloud Storage

The Hub's primary implemented service today is cloud storage.
Apps upload and download opaque files; the Hub routes them to the correct bucket/folder based on the session's station.

### Supported Protocols

TODO: Document each adapter (S3, Google Drive, Dropbox) and its authentication model.

### Credential Management

Cloud storage credentials are stored in the user's NoteToSelf database.
For OAuth-based providers (Google Drive, Dropbox), the Hub handles token refresh transparently.

TODO: Credential storage is likely to change (e.g. keyring, vault, or encryption-layer integration).

## Notifications

TODO: Not yet implemented. The Hub will route notifications to the correct apps/teams.

## Real-Time Connectivity

TODO: Not yet implemented. The Hub will negotiate VPN connections between devices.

## Encryption Layer

TODO: Not yet implemented. In production, the Hub will encrypt all outbound data and decrypt all inbound data, transparent to apps. See the top-level spec for context.

## HTTP API

TODO: Document the Hub's HTTP endpoints — request/response formats, error handling.

## Local Data

The Hub maintains its own local SQLite database for session tracking, separate from the Team Manager's databases.

TODO: Document the Hub's local database schema and the on-disk directory layout it expects.

## Open Questions

- Should the Hub enforce permissions, or is enforcement purely cryptographic? (Current design: permissions are a social contract; see top-level spec.)
- Can a single Hub instance serve multiple users on the same device?
- How will credential storage evolve when the encryption layer is implemented?
