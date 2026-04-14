# Small Sea Manager — Spec

## Overview

Small Sea Manager is the essential built-in user application for Small Sea Collective. It manages:

- **Teams** — create, configure, and leave teams
- **Membership** — invite members, accept invitations, set berth permissions, remove members from the local team view
- **Devices** — link new devices, revoke old ones, manage the participant's device identity
- **Apps** — register which apps are active for each team (berth management)
- **Service Subscriptions** — configure the cloud storage accounts, notification services, and other general-purpose services that the Hub needs to operate
- **Identity/Trust** — key management, device linking, participant unification

The Manager comes with two primary user interfaces built on a shared business logic layer:

- **Web UI** — local server, accessed via browser
- **CLI** — command-line interface

## Architecture

### Manager ↔ Hub relationship

The Manager has a special but well-defined relationship with the Hub:

1. **Direct DB writes** — The Manager is the sole writer of the participant's `core.db` files (NoteToSelf and per-team). The Hub reads these databases directly (file-watch + cache flush). There is no API between them for this data. The Manager never calls Hub endpoints to read or write team/membership/invitation data.

2. **Hub as app client** — The Manager also uses the Hub as a regular app client (via `SmallSeaClient`) to perform cloud operations: pushing and pulling berth data through Cod Sync. This is done via Hub sessions, one per berth, opened lazily as needed.

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

### NoteToSelf storage

NoteToSelf is now split into:

- shared DB: `NoteToSelf/Sync/core.db`
- device-local DB: `NoteToSelf/Local/device_local.db`

The shared DB is safe to sync across a participant's devices. The local DB
holds credentials, private-key refs, and sender-key runtime state that should
never leave the current device.

### Shared NoteToSelf DB (`NoteToSelf/Sync/core.db`)

Stores this participant's personal Small Sea metadata. The Hub reads it to know what teams, apps, cloud accounts, and devices exist.

| Table | Purpose |
|-------|---------|
| `participant` | The participant's own identity record (name, canonical ID) |
| `participant_unification` | Maps device-local participant IDs to a canonical person ID (see §Device Management) |
| `user_device` | Devices registered as belonging to this participant; each carries explicit bootstrap encryption and signing public keys |
| `nickname` | Human-readable names associated with this participant |
| `team` | Lightweight pointer to each team the participant belongs to; `self_in_team` is the member ID in that team's DB |
| `app` | Apps registered for the NoteToSelf team |
| `team_app_berth` | Berths for the NoteToSelf team (one per app; carries `team_id` since NoteToSelf DB may host multiple teams' personal berths) |
| `cloud_storage` | Shared cloud locator metadata (`protocol`, `url`, `client_id`, `path_metadata`) |
| `notification_service` | Shared notification-service metadata (`protocol`, `url`) |
| `team_device_key` | Shared public metadata for this identity's team-device keys |

### Device-local NoteToSelf DB (`NoteToSelf/Local/device_local.db`)

| Table | Purpose |
|-------|---------|
| `cloud_storage_credential` | Per-device cloud auth material (S3 secrets, OAuth refresh/access tokens, etc.) |
| `notification_service_credential` | Per-device notification credentials |
| `team_device_key_secret` | Local private-key refs for this device's team keys |
| `team_sender_key` | Local sender-chain runtime state |
| `peer_sender_key` | Local receiver-chain runtime state |

Schema versions are tracked independently via SQLite `PRAGMA user_version`.

### Team DB (`{TeamName}/Sync/core.db`)

Stores the shared state for one team. All members maintain their own copy; changes are merged via Cod Sync and the splice-sqlite merge driver.

| Table | Purpose |
|-------|---------|
| `member` | One row per team member; the primary key is that member's team-local identity; member-facing fields such as `display_name` live here |
| `member_unification` | Maps multiple member IDs to the same person (for the oops-unification device flow; see §Device Management) |
| `app` | Apps active for this team |
| `team_app_berth` | Berths for this team (one per app; `team_id` omitted — implicit from which DB this is) |
| `berth_role` | Per-member, per-berth role assignments: `read-only` or `read-write` |
| `invitation` | Invitation records (pending, accepted, revoked) |
| `team_device` | One row per team device; device identity and cloud endpoint metadata used for Cod Sync pull live here |


---

## Roles

Roles are semantic shorthands. Underneath, everything is per-berth `read-only` / `read-write` in `berth_role`.

| Role | `{Team}/SmallSeaCollectiveCore` | All other berths |
|------|---------------------------------|------------------|
| **admin** | read-write | read-write |
| **contributor** | read-only | read-write |
| **observer** | read-only | read-only |

- **admin** — shorthand for "has write permission to the team's Core berth",
  and therefore can publish updates to membership and berth permissions
- **contributor** — can publish app data updates but, by convention, not Core
  updates
- **observer** — should continue receiving readable updates, but peers are not
  expected to merge their writes anywhere

The default role when accepting an invitation is **admin** for small teams. The inviter may specify a different role when creating the invitation.

Important clarifications:

- These roles are **local policy and protocol expectations**, not centrally
  enforced entitlements.
- `read-only` means peers participating in the protocol should continue doing
  whatever key exchange is needed for that member to read updates in the berth.
- `read-write` means peers participating in the protocol should merge that
  member's updates for the berth into their own clone.
- `admin` is not a special cryptographic authority. It just means
  `read-write` on `{Team}/SmallSeaCollectiveCore`.

> The Hub respects these roles when deciding whose changes to incorporate: it
> only merges changes from members who have `read-write` permission in its
> **local** copy of the team DB.

This means different participants can legitimately have different views of who
is an admin, who is a contributor, or who is still in the team at all.

---

## Operations

### First-Run Setup

**Create participant**

Creates the participant directory, initializes the shared and device-local
NoteToSelf DBs plus the NoteToSelf git repo, and generates a device key
(stored in the hardware secure enclave if available, otherwise in
`FakeEnclave/` with password-derived encryption). Should be the only operation
that creates a `participant_hex`.

Inputs: `root_dir`, `nickname`, optional `device` label.

After creation, the participant has a NoteToSelf berth but no cloud storage and no teams other than NoteToSelf.

---

### Device Management

A *participant* represents a person (or bot). A *device* is a specific hardware+software installation. One participant can have many devices.

#### Link new device — primary flow

The primary flow is now explicitly split into **identity join** and later
**team join**.

Identity join:

1. On the **new device**: the Manager generates a NoteToSelf device keypair,
   persists the private keys locally, and produces a small public join-request
   artifact (`device UUID + bootstrap-encryption public key + signing public key`)
   plus a short human-verifiable authentication string.
2. The join-request artifact is delivered out-of-band to an **existing
   device**.
3. On the **existing device**: the Manager compares the same short
   authentication string, adds the new device to shared `user_device`,
   commits NoteToSelf locally, publishes NoteToSelf through a normal
   NoteToSelf Hub session when the configured provider supports it,
   signs the welcome bundle plaintext with its NoteToSelf signing key,
   and returns a short-lived encrypted welcome bundle.
4. On the **new device**: the Manager decrypts the welcome bundle, initializes
   only device-local NoteToSelf state, asks its own local Hub for a
   bootstrap-scoped fetch capability, pulls NoteToSelf from the shared
   remote through that Hub transport, verifies the welcome-bundle signature
   against the pulled `user_device` signer key, and blocks further use if
   that verification fails.

After identity join, the new device knows about the participant's devices,
teams, and apps through NoteToSelf, but it does **not** automatically join
every team.

Per-team join remains a separate later flow:

1. The device generates a team-specific keypair.
2. The device requests or records team participation.
3. A device already participating in that team issues the necessary trust
   material (`membership` / `device_link`).

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

Creates `{TeamName}/Sync/` directory with a fresh team DB and git repo. Adds a
Team pointer to NoteToSelf DB. The creator is added as the first member with
`admin` role on all berths, meaning their local starting view is that they have
`read-write` access everywhere, including Core.

#### List teams

Reads the `team` table from NoteToSelf DB. Does not query the Hub.

#### Get team details

Reads from the team's `core.db` directly: members, berths, pending invitations.

#### Leave team

Removes the Team pointer from NoteToSelf DB. Deletes the `{TeamName}/Sync/` directory locally. Pushes the NoteToSelf change so other devices can pick it up and do the corresponding deletion. Other apps are responsible for cleaning up their own data in that team's berths.

> There is no "disband team" operation. Other members continue to exist. A member leaving is purely a local act.

---

### Membership

#### List members

Reads `member` + `berth_role` from the team DB. Does not query the Hub.

#### Set member role

Writes `berth_role` rows for the target member: sets
`read-write`/`read-only` on each berth according to the role mapping in
§Roles. Commits and eventually pushes.

This is a mutation to the local clone of the team DB. It becomes socially
important only insofar as peers adopt that updated view and behave accordingly.

#### Remove member

Deletes the member's `berth_role` rows and `peer` row from the local team DB
clone. Commits. Triggers key rotation so that peers following this updated view
can stop giving the removed member future readable updates.

This is not a magical globally authoritative act. It means, roughly, "my clone
now says this person is no longer part of the team, and I am publishing that
view." Other teammates may adopt that view, reject it, or publish a conflicting
view.

Because team history is kept in git, long-lived disagreement gets awkward
quickly. Conflicting removals effectively fork the team into incompatible
futures. Participants cannot comfortably inhabit both without an explicit
translation layer.

---

### Invitations

See §Invitation Protocol for the full step-by-step.

#### Create invitation

Inserts a pending `invitation` row in the team DB. Produces a token for out-of-band delivery.

Inputs: `team_name`, optional `invitee_label` (human note for who this is for), `role` (default: admin).

Token contents: invitation ID, nonce, team name, inviter member ID, inviter display name, inviter cloud endpoint (protocol + URL only — no credentials), inviter bucket name. Privacy is provided by E2E encryption (issue #0008), not by keeping the bucket private.

An invitation is therefore a coordination convenience, not the sole source of
truth for whether someone is "really" in the team. It is a conventional way of
proposing and propagating a membership update.

#### List invitations

Reads `invitation` rows from team DB. Does not query the Hub.

#### Revoke invitation

Updates `invitation.status` to `revoked` for a pending invitation. Commits.

#### Accept invitation (invitee side)

Takes an out-of-band token. All cloud I/O goes through the Hub:

1. Opens a NoteToSelf Hub session, calls `GET /cloud_proxy` (using an `ExplicitProxyRemote`) to download the inviter's team bundle chain. The Hub proxies the bytes using appropriate credentials — the Manager never contacts cloud storage directly.
2. Clones the team repo locally, records member/device rows in the shared team DB, installs the splice-sqlite merge driver, and adds a Team pointer to NoteToSelf DB. (All local DB/git ops; no network.)
3. Opens a team Hub session, calls `POST /cloud/setup` to create the acceptor's bucket, then pushes via Cod Sync through the Hub.

Returns an acceptance token for out-of-band delivery back to the inviter.

#### Complete invitation acceptance (inviter side)

Takes the out-of-band acceptance token. Validates nonce. Marks invitation
`accepted`. Adds the acceptor as a member with the role specified in the
original invitation. Adds or updates the acceptor's `team_device` row with its
cloud location. Commits.

As with any other Core-berth mutation, this matters to the broader team only to
the extent that other participants incorporate this updated view into their own
clone.

---

### App Management

App management is primarily berth management: controlling which apps are active for each team.

#### List apps for a team

Reads the `app` + `team_app_berth` tables from the team DB.

#### Add app to team (create berth)

Inserts an `app` row and a `team_app_berth` row in the team DB. Grants `berth_role` rows for all current members (using their existing role). Commits.

#### Remove app from team

Deletes the `team_app_berth` row (and cascades to `berth_role`). Commits. The app's data directory is not automatically deleted — that is the app's responsibility.

> *Service subscriptions:* In a future where apps have licensing or per-team billing arrangements, the Manager would also track app subscriptions here. Stubbed for now.

---

### Service Subscriptions

The Manager provides the UI for configuring the general-purpose services that
the Hub uses. The Hub reads shared locator metadata from
`NoteToSelf/Sync/core.db` and matching device-local credentials from
`NoteToSelf/Local/device_local.db`. The Hub never configures services itself.

Small Sea can operate without cloud storage (sync simply won't work), but this is an unusual configuration.

#### Cloud storage accounts

Add, update, or remove entries in the shared `cloud_storage` table plus the
matching local `cloud_storage_credential` row. Shared fields are `protocol`,
`url`, `client_id`, and `path_metadata`; device-local fields include S3
credentials and OAuth refresh/access material.

#### Notification services

Add or remove entries in the shared `notification_service` table plus the
matching local `notification_service_credential` row. Shared fields are
`protocol` and `url`; auth tokens stay local.

#### Other services (stub)

Future service types include VPN providers (e.g. Tailscale) and identity/authentication services (e.g. Auth0). The Manager will provide configuration UI for each. Details TBD.

---

### Sync

Sync is **user-initiated** by default, with the Hub providing reminders when action is needed.

The out-of-sync state is a two-part story:

1. **Incoming:** The Hub monitors teammates' cloud locations in the background. When new data arrives, the Hub places a notification in its mailbox for the Manager (and other apps) to consume. The Manager should surface this to the user.

2. **Outgoing:** The Manager tracks whether local commits have been pushed. If there are unpushed commits, the Manager should surface increasingly noticeable reminders.

**Push (user-initiated):** Opens a Hub session for the relevant berth (e.g. `{Team}/SmallSeaCollectiveCore`), triggers a Cod Sync push via the Hub. The Hub handles the actual cloud I/O.

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
  |  → grants Bob's berth_role
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
      |                                 | generate NoteToSelf device key
      |                                 | + public join request artifact
      |                                 |
      | <- join request artifact -----  |
      | compare short auth string       |
      | write shared user_device row    |
      | commit + push NoteToSelf        |
      | seal short-lived welcome bundle |
      |                                 |
      | --- welcome bundle (OOB) -----> |
      |                                 |
      |              decrypts bundle
      |              initializes only local NoteToSelf state
      |              pulls NoteToSelf/Sync
      |              learns teams/apps/devices
      |              does not auto-clone team repos
```

The currently implemented proof path is S3/MinIO through Hub-owned transport,
with `LocalFolderRemote` retained as a local-only fallback for tests and
simple setups. Real OAuth provider bootstrap remains follow-up work.

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

### Shared NoteToSelf schema (`small_sea_note_to_self/sql/shared_schema.sql`)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_device (
    id                        BLOB PRIMARY KEY,
    bootstrap_encryption_key  BLOB NOT NULL,
    signing_key               BLOB NOT NULL
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

CREATE TABLE IF NOT EXISTS team_app_berth (
    id      BLOB PRIMARY KEY,
    app_id  BLOB NOT NULL,
    team_id BLOB NOT NULL,
    FOREIGN KEY (app_id)  REFERENCES app(id)  ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES team(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cloud_storage (
    id            BLOB PRIMARY KEY,
    protocol      TEXT NOT NULL,
    url           TEXT NOT NULL,
    client_id     TEXT,
    path_metadata TEXT
);

CREATE TABLE IF NOT EXISTS notification_service (
    id       BLOB PRIMARY KEY,
    protocol TEXT NOT NULL,
    url      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_device_key (
    team_id     BLOB NOT NULL,
    device_id   BLOB NOT NULL,
    public_key  BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    revoked_at  TEXT,
    PRIMARY KEY (team_id, device_id),
    FOREIGN KEY (team_id) REFERENCES team(id),
    FOREIGN KEY (device_id) REFERENCES user_device(id)
);
```

### Device-local NoteToSelf schema (`small_sea_note_to_self/sql/device_local_schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS cloud_storage_credential (
    cloud_storage_id BLOB PRIMARY KEY,
    access_key TEXT,
    secret_key TEXT,
    client_secret TEXT,
    refresh_token TEXT,
    access_token TEXT,
    token_expiry TEXT
);

CREATE TABLE IF NOT EXISTS notification_service_credential (
    notification_service_id BLOB PRIMARY KEY,
    access_key TEXT,
    access_token TEXT
);

CREATE TABLE IF NOT EXISTS note_to_self_device_key_secret (
    device_id BLOB PRIMARY KEY,
    encryption_private_key_ref TEXT NOT NULL,
    signing_private_key_ref TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_device_key_secret (
    team_id BLOB NOT NULL,
    device_id BLOB NOT NULL,
    private_key_ref TEXT NOT NULL,
    PRIMARY KEY (team_id, device_id)
);
```

### Team schema (`sql/core_other_team.sql`)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS member (
    id BLOB PRIMARY KEY,
    display_name TEXT,
    identity_public_key BLOB
);

CREATE TABLE IF NOT EXISTS app (
    id   BLOB PRIMARY KEY,
    name TEXT NOT NULL
);

-- team_id is intentionally absent: in a team DB the team is implicit.
CREATE TABLE IF NOT EXISTS team_app_berth (
    id     BLOB PRIMARY KEY,
    app_id BLOB NOT NULL,
    FOREIGN KEY (app_id) REFERENCES app(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS berth_role (
    id        BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    berth_id  BLOB NOT NULL,
    role      TEXT NOT NULL CHECK(role IN ('read-only', 'read-write')),
    FOREIGN KEY (member_id) REFERENCES member(id)          ON DELETE CASCADE,
    FOREIGN KEY (berth_id)  REFERENCES team_app_berth(id)  ON DELETE CASCADE
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
    acceptor_device_key_id BLOB,
    acceptor_protocol TEXT,
    acceptor_url      TEXT
    -- no credential columns: privacy is E2E, not access-control
);

CREATE TABLE IF NOT EXISTS team_device (
    device_key_id BLOB PRIMARY KEY,
    member_id BLOB NOT NULL,
    public_key BLOB NOT NULL,
    protocol TEXT,
    url TEXT,
    bucket TEXT,
    created_at TEXT NOT NULL,
    -- no credential columns: credentials stay in the local Hub, never shared
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS device_prekey_bundle (
    device_key_id BLOB PRIMARY KEY,
    prekey_bundle_json TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (device_key_id) REFERENCES team_device(device_key_id) ON DELETE CASCADE
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
| **`manager.py` sessions** | The Manager now tracks berth-scoped Hub sessions lazily via its `_sessions` cache and the web/UI PIN flow. What is still missing is the broader multi-device `NoteToSelf` sync and update-awareness story. |
| **`participant` / `participant_unification` tables** | Not yet in the SQL schema; needs to be designed and added to NoteToSelf DB. |
| **NoteToSelf/{App} berths** | Per-app personal state outside of team context. Not yet designed; stub only. |
| **Sync mailbox API** | Hub needs a mailbox abstraction to notify the Manager (and other apps) when incoming changes arrive from the internet. Shape TBD. |
| **Credential storage** | Credentials now live in the device-local NoteToSelf DB, but they are still plaintext SQLite fields. Future work should move them behind OS keychain / vault references. |
