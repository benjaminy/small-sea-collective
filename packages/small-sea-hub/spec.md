---
id: small-sea-hub
version: 1
status: experimental
---

## Purpose

The Small Sea Hub is a local service that runs on each user's device.
It is the sole gateway between applications and Small Sea services.
Apps never access these services directly; they go through the Hub.

The Hub has two main jobs:
1. Mediate access to general-purpose cloud services (storage, notifications, VPN, etc.) on behalf of apps.
2. Gate that access through sessions, so users can control which apps access which stations.

Currently the Hub serves its API over HTTP.
There could be a reason for a different protocol in the future (direct IPC or something).
However, the whole Small Sea framework is designed with local-first application styles in mind, where waiting on responses from the network should be minimized.
So interactions with the Hub should generally be off an app's critical path.
If apps have a need for low-latency communication among teammates, they should open VPN connections through Small Sea, then sending communicating through the VPN will not involve the Hub.

## Sessions

Sessions are how apps gain access to Hub services.
A session is scoped to exactly one station (one team + one app).

Opening a session is a two-step flow that requires user approval:

**Step 1 — Request.**
The app posts `POST /sessions/request` with the participant nickname, app name, team name,
and a human-readable client name (shown in the approval prompt).
The Hub generates a random 4-digit PIN, writes a pending session row (TTL: 5 minutes),
and sends an OS notification to the user containing the PIN and the client name.
The response returns a `pending_id` that the app uses in the next step.

**Step 2 — Confirm.**
The user reads the PIN from the OS notification and types it into the requesting app.
The app posts `POST /sessions/confirm` with the `pending_id` and `pin`.
If the PIN matches and has not expired, the Hub creates a permanent session and returns
a 32-byte Bearer token (hex-encoded). All subsequent API calls use this token in the
`Authorization: Bearer <token>` header.

Sessions do not expire. There is no revocation endpoint yet — killing the Hub process
and clearing its local DB is the current escape hatch.

For automated tests, a client named `"Smoke Tests"` causes the Hub to echo the PIN
back in the `/sessions/request` response rather than sending an OS notification.

## Relationship with Small Sea Manager

The Hub has a special relationship with the Small Sea Manager app.
Small Sea Manager writes the databases that the Hub reads to do its work: team membership, app registrations, cloud service credentials, etc.

The Hub never writes to Manager's databases; it only reads them.
Manager never reads the Hub's local database; the session table is Hub-private.
The shared databases are the contract between them.

**Directory layout the Hub expects:**

```
{root_dir}/
  small_sea_collective_local.db          ← Hub-private session DB
  Logging/
    small_sea_hub.log
  Participants/
    {participant_hex}/
      NoteToSelf/
        Sync/
          core.db                        ← NoteToSelf DB (written by Manager)
      {TeamName}/
        Sync/
          core.db                        ← Team DB (written by Manager, synced via Cod Sync)
```

**Tables the Hub reads from the NoteToSelf DB** (`NoteToSelf/Sync/core.db`):

| Table | Used for |
|---|---|
| `nickname` | Resolving a participant by human name during session request |
| `team` | Looking up team ID for any session (including non-NoteToSelf teams) |
| `app`, `team_app_station` | NoteToSelf app/station lookup |
| `cloud_storage` | Routing cloud uploads/downloads for any session |
| `notification_service` | Routing notifications for any session |

**Tables the Hub reads from a team DB** (`{TeamName}/Sync/core.db`):

| Table | Used for |
|---|---|
| `app`, `team_app_station` | App/station lookup for non-NoteToSelf team sessions |

## Cloud Storage

The Hub's primary implemented service today is cloud storage.
Apps upload and download opaque files; the Hub routes them to the correct bucket/folder based on the session's station.

### Supported Protocols

**S3-compatible** (`protocol = "s3"`): Any S3-compatible endpoint (AWS, MinIO, etc.).
Requires `url`, `access_key`, `secret_key` in the `cloud_storage` row.
Bucket name is derived as `ss-{station_id_hex[:16]}`.
Uses AWS Signature Version 4.

**Google Drive** (`protocol = "gdrive"`): OAuth2.
Requires `client_id`, `client_secret`, `refresh_token`.
The Hub refreshes the access token transparently before each operation and persists
the new token back to `cloud_storage`.

**Dropbox** (`protocol = "dropbox"`): OAuth2.
Same token refresh pattern as Google Drive.

### Credential Management

Cloud storage credentials are stored in the user's NoteToSelf database (`cloud_storage` table).
For OAuth-based providers (Google Drive, Dropbox), the Hub handles token refresh transparently.

Credential storage is likely to evolve — options include OS keyring integration, a local vault,
or delegation to the encryption layer once that is implemented.

### CAS Uploads

`POST /cloud_file` accepts an optional `expected_etag` field.
When provided, the upload is conditional: if the current ETag of the remote file does not match,
the Hub returns `409 Conflict` with `detail: "CAS conflict: file was modified concurrently"`.
This is used by Cod Sync to detect concurrent writes to the bundle-chain head.

## Notifications

The Hub implements push notifications via [ntfy](https://ntfy.sh), a self-hosted or public
pub/sub notification service.

The ntfy topic for a session is derived as `ss-{sha256("{team}/{app}")[:16]}`, so all
participants sharing the same station converge on the same topic automatically.

`POST /notifications` publishes a message (with optional `title`) to the session's topic.
`GET /notifications` long-polls the topic for new messages; accepts `since` (ntfy message ID
or `"all"`) and `timeout` (seconds, default 30).

A `notification_service` row must be present in the participant's NoteToSelf DB, with
`protocol = "ntfy"` and the ntfy server `url`.

## Real-Time Connectivity

Not yet implemented. The Hub will negotiate VPN connections between devices.

## Encryption Layer

Not yet implemented. In production, the Hub will encrypt all outbound data and decrypt all inbound data, transparent to apps. See the top-level spec for context.

## HTTP API

All endpoints except `/sessions/request` and `/sessions/confirm` require a Bearer token:

```
Authorization: Bearer <session_token_hex>
```

### Session endpoints

**`POST /sessions/request`**

```json
{ "participant": "<nickname>", "app": "<app_name>", "team": "<team_name>", "client": "<client_name>" }
```

Response:
```json
{ "pending_id": "<hex>" }
```
(For `client = "Smoke Tests"`, also includes `"pin": "<4-digit string>"` for test automation.)

Errors: `404` if the participant/team/app is not found.

---

**`POST /sessions/confirm`**

```json
{ "pending_id": "<hex>", "pin": "<4-digit string>" }
```

Response: `"<session_token_hex>"` (bare string).

Errors: `404` if pending ID not found; `400`-level if PIN is wrong or expired.

---

### Cloud storage endpoints

**`POST /cloud_file`** — Upload a file.

```json
{ "path": "<remote path>", "data": "<base64>", "expected_etag": "<etag or null>" }
```

Response:
```json
{ "ok": true, "etag": "<etag>", "message": "" }
```

Errors: `409` on CAS conflict; `500` on storage error.

---

**`GET /cloud_file?path=<remote path>`** — Download a file.

Response:
```json
{ "ok": true, "data": "<base64>", "etag": "<etag>" }
```

Errors: `404` if not found.

---

### Notification endpoints

**`POST /notifications`**

```json
{ "message": "<text>", "title": "<optional title>" }
```

Response:
```json
{ "ok": true, "id": "<message_id>" }
```

---

**`GET /notifications?since=<id>&timeout=<seconds>`** — Long-poll for new messages.

Response:
```json
{ "ok": true, "messages": [ ... ] }
```

---

## Local Data

The Hub maintains its own local SQLite database at `{root_dir}/small_sea_collective_local.db`,
separate from Small Sea Manager's databases. It uses schema version (`PRAGMA user_version`) for
migrations.

**`session` table** — Active sessions:

| Column | Type | Notes |
|---|---|---|
| `id` | BLOB | UUID7, primary key |
| `token` | BLOB | 32 random bytes; used as Bearer credential |
| `created_at` | DATETIME | |
| `duration_sec` | INTEGER | NULL = no expiry (current default) |
| `participant_id` | BLOB | Matches `Participants/{hex}` directory name |
| `team_id`, `team_name` | BLOB / TEXT | From Manager's `team` table |
| `app_id`, `app_name` | BLOB / TEXT | From Manager's `app` table |
| `station_id` | BLOB | Drives bucket naming for S3 |
| `client` | TEXT | Human name of the requesting client |

**`pending_session` table** — In-flight approval requests (deleted on confirm or expiry):

| Column | Type | Notes |
|---|---|---|
| `id` | BLOB | UUID7, primary key |
| `participant_hex` | TEXT | |
| `team_name`, `app_name`, `client_name` | TEXT | |
| `pin` | TEXT | 4-digit zero-padded string |
| `created_at`, `expires_at` | TEXT | ISO 8601; TTL is 5 minutes |

## Open Questions

- Should the Hub enforce permissions, or is enforcement purely cryptographic? (Current design: permissions are a social contract; see top-level spec.)
- Can a single Hub instance serve multiple users on the same device?
- How will credential storage evolve when the encryption layer is implemented?
