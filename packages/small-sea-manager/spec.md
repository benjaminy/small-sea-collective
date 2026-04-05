# Small Sea Manager — Spec

## Overview

Small Sea Manager is the essential built-in user application for Small Sea Collective. It manages:

- **Teams** — create, configure, and leave teams
- **Membership** — invite members, accept invitations, set roles, remove members
- **Devices** — link new devices, revoke old ones, manage the participant's device identity
- **Apps** — register which apps are active for each team (station management)
- **Service Subscriptions** — configure the cloud storage accounts, notification services, and other general-purpose services that the Hub needs to operate
- **Identity/Trust** — key management, device linking, participant unification

The Manager comes with two primary user interfaces built on a shared business logic layer:

- **Web UI** — local server, accessed via browser
- **CLI** — command-line interface

## Architecture

### Manager ↔ Hub relationship

The Manager has a special but well-defined relationship with the Hub:

1. **Direct DB writes** — The Manager is the sole writer of the participant's `core.db` files (NoteToSelf and per-team). The Hub reads these databases directly (file-watch + cache flush). There is no API between them for this data. The Manager never calls Hub endpoints to read or write team/membership/invitation data.

2. **Hub as app client** — The Manager also uses the Hub as a regular app client (via `SmallSeaClient`) to perform cloud operations: pushing and pulling station data through Cod Sync. This is done via Hub sessions, one per station, opened lazily as needed.

This means the Manager has *two distinct layers*:

- **Provisioning layer** (`provisioning.py`) — direct filesystem and SQLite operations: creating participants, initializing databases, running git, writing invitation records. No network I/O.
- **Session layer** (`manager.py`, `TeamManager`) — Hub client sessions used only for cloud sync and any Hub-mediated network operations. Reads team data from local DB, not from the Hub API.


### Single participant per installation

Each Manager/Hub installation serves exactly one participant. There is no multi-user Hub or shared Manager instance. `root_dir` (the participant's data directory) is fixed at install time and provided to the Manager process as config. For testing, `root_dir` is passed as a CLI argument or environment variable.

---

## File Layout

```
{root_dir}/
  Participants/
    {participant_hex}/           ← UUIDv7 hex, device-local participant ID
      NoteToSelf/
        Sync/                    ← git repo
          core.db                ← NoteToSelf DB (see schema below)
          .gitattributes         ← splice-sqlite merge driver config
      {TeamName}/
        Sync/                    ← git repo
          core.db                ← Team DB (see schema below)
          .gitattributes
      FakeEnclave/               ← password-derived encrypted key storage
                                    (used on devices without a hardware secure enclave)
```

All IDs are UUIDv7 (time-ordered, random), stored as 16-byte BLOBs.

---

## Data Model

There are two kinds of `core.db`:

### NoteToSelf DB (`NoteToSelf/Sync/core.db`)

Stores this participant's personal Small Sea metadata. The Hub reads it to know what teams, apps, cloud accounts, and devices exist.

| Table | Purpose |
|-------|---------|
| `participant` | The participant's own identity record (name, canonical ID) |
| `participant_unification` | Maps device-local participant IDs to a canonical person ID (see §Device Management) |
| `user_device` | Devices registered as belonging to this participant; each has its own key |
| `nickname` | Human-readable names associated with this participant |
| `team` | Lightweight pointer to each team the participant belongs to; `self_in_team` is the member ID in that team's DB |
| `app` | Apps registered for the NoteToSelf team |
| `team_app_station` | Stations for the NoteToSelf team (one per app; carries `team_id` since NoteToSelf DB may host multiple teams' personal stations) |
| `cloud_storage` | Cloud storage accounts available to this participant (S3, Google Drive, Dropbox, etc.) — the Hub reads these to know where to push/pull |
| `notification_service` | Notification endpoints (e.g. ntfy) — the Hub reads these to know where to send/receive notifications |
| `team_signing_key` | Per-team Ed25519 signing key pairs; private key stored here (syncs across devices), public key also stored in the team DB's `member` row |

Schema version is tracked via SQLite `PRAGMA user_version`. Current: `USER_SCHEMA_VERSION = 47`.

### Team DB (`{TeamName}/Sync/core.db`)

Stores the shared state for one team. All members maintain their own copy; changes are merged via Cod Sync and the splice-sqlite merge driver.

| Table | Purpose |
|-------|---------|
| `member` | One row per team member; the primary key is that member's team-local identity; `public_key` holds their Ed25519 signing key for bundle verification |
| `member_unification` | Maps multiple member IDs to the same person (for the oops-unification device flow; see §Device Management) |
| `app` | Apps active for this team |
| `team_app_station` | Stations for this team (one per app; `team_id` omitted — implicit from which DB this is) |
| `station_role` | Per-member, per-station role assignments: `read-only` or `read-write` |
| `invitation` | Invitation records (pending, accepted, revoked) |
| `peer` | Each team member's cloud location(s) — used for Cod Sync pull |


---

## Roles

Roles are semantic shorthands. Underneath, everything is per-station `read-only` / `read-write` in `station_role`.

| Role | `{Team}/SmallSeaCollectiveCore` | All other stations |
|------|---------------------------------|--------------------|
| **admin** | read-write | read-write |
| **contributor** | read-only | read-write |
| **observer** | read-only | read-only |

- **admin** — full control, including changing membership and permissions
- **contributor** — can contribute data to the team's apps but cannot change team structure
- **observer** — read-only across the board

The default role when accepting an invitation is **admin** for small teams. The inviter may specify a different role when creating the invitation.

> The Hub respects these roles when deciding whose changes to incorporate: it only merges changes from members who have `read-write` permission in its local copy of the team DB.

---

## Operations

### First-Run Setup

**Create participant**

Creates the participant directory, initializes NoteToSelf DB and git repo, generates a device key (stored in the hardware secure enclave if available, otherwise in `FakeEnclave/` with password-derived encryption). Should be the only operation that creates a `participant_hex`.

Inputs: `root_dir`, `nickname`, optional `device` label.

After creation, the participant has a NoteToSelf station but no cloud storage and no teams other than NoteToSelf.

---

### Device Management

A *participant* represents a person (or bot). A *device* is a specific hardware+software installation. One participant can have many devices.

#### Link new device — primary flow

The intended flow for clean setup of a second device:

1. On the **existing device**: user initiates "Add new device" in the Manager UI. The Manager generates a short-lived *device-link token* (similar in shape to an invitation token) containing:
   - Enough to bootstrap the new device's identity (cloud storage credentials, enough key material to clone NoteToSelf)
   - A pre-allocated `user_device` row (device ID + public key placeholder)
2. Token is delivered out-of-band to the new device (paste, QR code scan, etc.)
3. On the **new device**: Manager uses the token to:
   - Clone `NoteToSelf/SmallSeaCollectiveCore` from cloud
   - Discover all teams via the `team` table
   - Clone each `{Team}/SmallSeaCollectiveCore` from cloud
   - Generate a device key, write it into the pre-allocated `user_device` row, commit, push

> The QR code UI is a known awkward point; the raw base64 token is acceptable for now.

#### Unify with existing identity — secondary (oops) flow

For the case where Small Sea was set up on a new device independently (creating a separate participant UUID) and the user wants to declare it the same person as an existing installation.

**Shallow unification (implemented):** Write a `participant_unification` row linking the two participant IDs. This tells the local Manager to treat them as the same person for display purposes. Team memberships on both sides remain separate.

**Deep unification (stub — not yet implemented):** Merging team memberships, reconciling member IDs across teams, and re-keying is deferred. The Manager should raise a clear `NotImplementedError` with an explanation if deep unification is attempted.

#### List devices

Returns all `user_device` rows for this participant.

#### Remove/revoke device

Deletes the `user_device` row and triggers key rotation (so the removed device cannot decrypt future data). Key rotation mechanics are TBD pending Cuttlefish integration.

---

### Team Management

#### Create team

Creates `{TeamName}/Sync/` directory with a fresh team DB and git repo. Adds a Team pointer to NoteToSelf DB. The creator is added as the first member with `admin` role on all stations.

#### List teams

Reads the `team` table from NoteToSelf DB. Does not query the Hub.

#### Get team details

Reads from the team's `core.db` directly: members, stations, pending invitations.

#### Leave team

Removes the Team pointer from NoteToSelf DB. Deletes the `{TeamName}/Sync/` directory locally. Pushes the NoteToSelf change so other devices can pick it up and do the corresponding deletion. Other apps are responsible for cleaning up their own data in that team's stations.

> There is no "disband team" operation. Other members continue to exist. A member leaving is purely a local act.

---

### Membership

#### List members

Reads `member` + `station_role` from the team DB. Does not query the Hub.

#### Set member role

Writes `station_role` rows for the target member: sets `read-write`/`read-only` on each station according to the role mapping in §Roles. Commits and eventually pushes.

#### Remove member

Deletes the member's `station_role` rows and `peer` row from the team DB. Commits. Triggers an immediate key rotation (so the removed member cannot decrypt further updates). The key rotation push should happen synchronously before any subsequent data changes are pushed.

---

### Invitations

See §Invitation Protocol for the full step-by-step.

#### Create invitation

Inserts a pending `invitation` row in the team DB. Produces a token for out-of-band delivery.

Inputs: `team_name`, optional `invitee_label` (human note for who this is for), `role` (default: admin).

Token contents: invitation ID, nonce, team name, inviter member ID, inviter display name, inviter cloud endpoint (protocol + URL only — no credentials), inviter bucket name. Privacy is provided by E2E encryption (issue #0008), not by keeping the bucket private.

#### List invitations

Reads `invitation` rows from team DB. Does not query the Hub.

#### Revoke invitation

Updates `invitation.status` to `revoked` for a pending invitation. Commits.

#### Accept invitation (invitee side)

Takes an out-of-band token. All cloud I/O goes through the Hub:

1. Opens a NoteToSelf Hub session, calls `GET /cloud_proxy` (using an `ExplicitProxyRemote`) to download the inviter's team bundle chain. The Hub proxies the bytes using appropriate credentials — the Manager never contacts cloud storage directly.
2. Clones the team repo locally, adds self as a member, installs the splice-sqlite merge driver. Adds a Team pointer to NoteToSelf DB. (All local DB/git ops; no network.)
3. Opens a team Hub session, calls `POST /cloud/setup` to create the acceptor's bucket, then pushes via Cod Sync through the Hub.

Returns an acceptance token for out-of-band delivery back to the inviter.

#### Complete invitation acceptance (inviter side)

Takes the out-of-band acceptance token. Validates nonce. Marks invitation `accepted`. Adds the acceptor as a member with the role specified in the original invitation. Adds acceptor's cloud location to `peer`. Commits.

---

### App Management

App management is primarily station management: controlling which apps are active for each team.

#### List apps for a team

Reads the `app` + `team_app_station` tables from the team DB.

#### Add app to team (create station)

Inserts an `app` row and a `team_app_station` row in the team DB. Grants `station_role` rows for all current members (using their existing role). Commits.

#### Remove app from team

Deletes the `team_app_station` row (and cascades to `station_role`). Commits. The app's data directory is not automatically deleted — that is the app's responsibility.

> *Service subscriptions:* In a future where apps have licensing or per-team billing arrangements, the Manager would also track app subscriptions here. Stubbed for now.

---

### Service Subscriptions

The Manager provides the UI for configuring the general-purpose services that the Hub uses. The Hub reads this configuration from `core.db` and never configures services itself.

Small Sea can operate without cloud storage (sync simply won't work), but this is an unusual configuration.

#### Cloud storage accounts

Add, update, or remove entries in the `cloud_storage` table (NoteToSelf DB). Fields: `protocol`, `url`, plus protocol-specific credentials (S3 keys, OAuth tokens for Google Drive/Dropbox, etc.).

> Credential storage will likely change (e.g. OS keychain or vault reference). The current schema stores credentials as plaintext columns.

#### Notification services

Add or remove entries in the `notification_service` table. Currently supported: `ntfy`. Fields: `protocol`, `url`.

#### Other services (stub)

Future service types include VPN providers (e.g. Tailscale) and identity/authentication services (e.g. Auth0). The Manager will provide configuration UI for each. Details TBD.

---

### Sync

Sync is **user-initiated** by default, with the Hub providing reminders when action is needed.

The out-of-sync state is a two-part story:

1. **Incoming:** The Hub monitors teammates' cloud locations in the background. When new data arrives, the Hub places a notification in its mailbox for the Manager (and other apps) to consume. The Manager should surface this to the user.

2. **Outgoing:** The Manager tracks whether local commits have been pushed. If there are unpushed commits, the Manager should surface increasingly noticeable reminders.

**Push (user-initiated):** Opens a Hub session for the relevant station (e.g. `{Team}/SmallSeaCollectiveCore`), triggers a Cod Sync push via the Hub. The Hub handles the actual cloud I/O.

> For testing, sync can be triggered immediately without user interaction via a config flag or test fixture.

---

## Invitation Protocol (detailed)

Invitation data is exchanged out-of-band. Hub API calls handle all cloud I/O.

```
Alice                                Bob
  |                                    |
  | create_invitation()                |
  |  → writes invitation row to        |
  |    ProjectX/Sync/core.db           |
  |  → commits git                     |
  |  → returns token_b64               |
  |                                    |
  | push via Hub (team session)        |
  |                                    |
  | ---- token_b64 (out of band) ----> |
  |                                    |
  |                   accept_invitation(token_b64)
  |                    → opens NoteToSelf Hub session
  |                    → Hub /cloud_proxy fetches Alice's
  |                      team bundle chain (anonymous read)
  |                    → clones ProjectX/Sync locally
  |                    → adds Bob as member in team DB
  |                    → adds Alice as peer in team DB
  |                    → pushes to Bob's cloud
  |                    → adds ProjectX pointer to
  |                      Bob's NoteToSelf DB
  |                    → commits git
  |                    → returns acceptance_b64
  |                                    |
  |                    → opens team Hub session
  |                    → pushes to Bob's cloud via Hub
  |                    → returns acceptance_b64
  |                                    |
  | <-- acceptance_b64 (out of band) - |
  |                                    |
  | complete_invitation_acceptance(acceptance_b64)
  |  → validates nonce
  |  → marks invitation accepted
  |  → adds Bob as member + peer
  |  → grants Bob's station_role
  |  → commits git
```

**Token contents:** invitation ID, nonce, team name, inviter member ID, inviter cloud endpoint (protocol + URL only — no credentials), inviter bucket name.

**Acceptance token contents:** invitation ID, nonce, acceptor member ID, acceptor cloud endpoint (protocol + URL only — no credentials), acceptor bucket name.

**Security model:** Inviter's bucket is publicly readable (anonymous reads via unsigned requests). Privacy is provided by E2E encryption (issue #0008), not by access control. Credentials are never transmitted in tokens or stored in the `peer` table.

**Double-accept protection:** `complete_invitation_acceptance` checks that the invitation status is `pending` before proceeding. A second acceptor presenting a different acceptance token will be rejected with a "not pending" error.

---

## Device Linking Protocol (detailed)

### Primary flow (clean new device)

```
Existing device                    New device
      |                                 |
      | "Add new device"                |
      | → generates device-link token   |
      |   (cloud creds + key bootstrap) |
      |                                 |
      | ---- token (out of band) -----> |
      |                                 |
      |              uses token to clone NoteToSelf/Sync
      |              discovers teams via `team` table
      |              clones each {Team}/Sync
      |              generates device key
      |              writes user_device row
      |              commits + pushes NoteToSelf
```

`make_device_link_invitation()` in `provisioning.py` is the stub for the token-generation side.

### Secondary flow (oops unification)

For when a device has already started operating as a separate participant and the user wants to declare it the same person.

**Shallow (implemented):** Write a `participant_unification` row. The two participant IDs are treated as the same person locally. Team memberships stay separate.

**Deep (stub):** Merging team membership records across the two identities is not yet designed. Raise `NotImplementedError("deep unification not yet implemented")` with a clear message.

---

## Secure Enclave and Key Storage

Each device generates its own key pair on first run.

- **Devices with a hardware secure enclave:** Keys are protected by the enclave. (Implementation TBD.)
- **Devices without a secure enclave:** Keys are stored in `FakeEnclave/` using password-derived encryption. The Manager should display a one-time warning that the device has no hardware enclave — but this warning should not be intrusive or repeat on every launch, since the user cannot change the hardware.

Key transfer between devices (e.g. sharing session keys or identity keys when linking a new device) follows the DAILY / GUARDED / BURIED protection levels defined in Cuttlefish. Details TBD pending Cuttlefish integration.

---

## SQL Schemas

### NoteToSelf schema (`sql/core_note_to_self_schema.sql`)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_device (
    id   BLOB PRIMARY KEY,
    key  BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS nickname (
    id   BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team (
    id           BLOB PRIMARY KEY,
    name         TEXT NOT NULL,
    self_in_team BLOB NOT NULL   -- member ID in the team's own DB
);

CREATE TABLE IF NOT EXISTS app (
    id   BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_app_station (
    id      BLOB PRIMARY KEY,
    app_id  BLOB NOT NULL,
    team_id BLOB NOT NULL,
    FOREIGN KEY (app_id)  REFERENCES app(id)  ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES team(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cloud_storage (
    id             BLOB PRIMARY KEY,
    protocol       TEXT NOT NULL,
    url            TEXT NOT NULL,
    -- S3-style credentials
    access_key     TEXT,
    secret_key     TEXT,
    -- OAuth fields (Google Drive, Dropbox)
    client_id      TEXT,
    client_secret  TEXT,
    refresh_token  TEXT,
    access_token   TEXT,
    token_expiry   TEXT,
    -- JSON dict mapping path → provider-specific metadata (e.g. Google Drive file IDs)
    path_metadata  TEXT
);

CREATE TABLE IF NOT EXISTS notification_service (
    id       BLOB PRIMARY KEY,
    protocol TEXT NOT NULL,
    url      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_signing_key (
    id          BLOB PRIMARY KEY,
    team_id     BLOB NOT NULL,
    public_key  BLOB NOT NULL,
    private_key BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (team_id) REFERENCES team(id)
);
```

### Team schema (`sql/core_other_team.sql`)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS member (
    id BLOB PRIMARY KEY,
    public_key BLOB
);

CREATE TABLE IF NOT EXISTS app (
    id   BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

-- team_id is intentionally absent: in a team DB the team is implicit.
CREATE TABLE IF NOT EXISTS team_app_station (
    id     BLOB PRIMARY KEY,
    app_id BLOB NOT NULL,
    FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS station_role (
    id         BLOB PRIMARY KEY,
    member_id  BLOB NOT NULL,
    station_id BLOB NOT NULL,
    role       TEXT NOT NULL CHECK(role IN ('read-only', 'read-write')),
    FOREIGN KEY (member_id)  REFERENCES member(id)           ON DELETE CASCADE,
    FOREIGN KEY (station_id) REFERENCES team_app_station(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invitation (
    id                BLOB PRIMARY KEY,
    nonce             BLOB NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    invitee_label     TEXT,
    role              TEXT NOT NULL DEFAULT 'admin',  -- role to grant on acceptance
    created_at        TEXT NOT NULL,
    accepted_at       TEXT,
    accepted_by       BLOB,
    acceptor_protocol TEXT,
    acceptor_url      TEXT
    -- no credential columns: privacy is E2E, not access-control
);

CREATE TABLE IF NOT EXISTS peer (
    id        BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    display_name TEXT,
    protocol  TEXT NOT NULL,
    url       TEXT NOT NULL,
    bucket    TEXT,
    -- no credential columns: credentials stay in the local Hub, never shared
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);
```

---

## Open Questions and Known Issues

| # | Issue |
|---|-------|
| **E2E encryption** | Tokens and peer table carry only cloud endpoints (no credentials). Data privacy depends on E2E encryption (issue #0008), which is not yet implemented. |
| **Deep device unification** | Merging team memberships across two separate participant identities is not designed. Shallow unification (NoteToSelf-level only) is the current scope. |
| **Key transfer between devices** | How DAILY/GUARDED/BURIED keys are shared with a newly linked device is TBD, pending Cuttlefish integration. |
| **`make_device_link_invitation()`** | Currently a stub (`pass`). The primary device-linking flow is not yet implemented. |
| **`manager.py` sessions** | `TeamManager.connect()` always opens a NoteToSelf session. It should open sessions for each relevant station lazily as needed. |
| **`participant` / `participant_unification` tables** | Not yet in the SQL schema; needs to be designed and added to NoteToSelf DB. |
| **NoteToSelf/{App} stations** | Per-app personal state outside of team context. Not yet designed; stub only. |
| **Sync mailbox API** | Hub needs a mailbox abstraction to notify the Manager (and other apps) when incoming changes arrive from the internet. Shape TBD. |
| **Credential storage** | `cloud_storage` credentials are stored as plaintext columns. Should migrate to OS keychain or vault reference. |
