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
2. Gate that access through sessions, so users can control which apps access which berths.

Currently the Hub serves its API over HTTP.
There could be a reason for a different protocol in the future (direct IPC or something).
However, the whole Small Sea framework is designed with local-first application styles in mind, where waiting on responses from the network should be minimized.
So interactions with the Hub should generally be off an app's critical path.
If apps have a need for low-latency communication among teammates, they should open VPN connections through Small Sea, then sending communicating through the VPN will not involve the Hub.

## Sessions

Sessions are how apps gain access to Hub services.
A session is scoped to exactly one berth (one team + one app).

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

`GET /session/info` exposes the public metadata boundary for an existing
session. It currently returns stable hex-string IDs for `participant_hex` and
`berth_id`, plus friendly `team_name` and `app_name`, client, and mode. Hub
session rows also store stable opaque `team_id` and `app_id` internally. Those
fields may be exposed in a future API or wrapped by an app-home helper, but apps
should not infer identity from friendly names or read Manager/Core databases
directly to recover IDs.

### Bootstrap-scoped transport

Identity bootstrap now uses a second, narrower capability alongside normal
sessions.

- `POST /bootstrap/sessions` creates a short-lived bootstrap token bound to a
  specific remote descriptor (`protocol`, `url`, `bucket`).
- `GET /bootstrap/cloud_file` uses that token to read NoteToSelf bootstrap
  artifacts from the bound location.

These bootstrap tokens are intentionally **not** normal berth sessions:

- they do not rely on participant/team/app lookup
- they are not accepted by ordinary session routes like `/session/info` or
  `/cloud_file`
- they are limited to bootstrap transport only

The current proof path is S3/MinIO only. OAuth bootstrap remains deferred.

### Restart resilience

**Goal:** restarting the Hub should be minimally disruptive to apps. Open network
connections will close (unavoidable), but apps that reconnect should be able to resume
without re-opening sessions or losing sync state.

Everything important is persisted to durable storage:

| Data | Where stored |
|---|---|
| Session tokens and all session metadata | Hub SQLite DB (`small_sea_collective_local.db`) |
| Peer cloud locations | Team DB (`member_berth_storage_announcement`), synced via Cod Sync |
| Signal file contents (push counts) | Cloud storage (`signals.yaml`) |
| Cloud locator metadata | NoteToSelf shared DB (`cloud_storage` table) |
| Berth cloud allocations | NoteToSelf shared DB (`berth_cloud_allocation` table) |
| Cloud credentials | NoteToSelf device-local DB (`cloud_storage_credential` table) |

The Hub's in-process state (`watched_sessions`, `watched_peers`, `peer_counts`) is
derived entirely from these durable sources and is **rebuilt at startup**:

1. `watched_sessions` is repopulated by reading all confirmed session rows from the Hub DB.
2. `watched_peers` is repopulated by reading each session's team DB member list (excluding self).
3. `peer_counts` is repopulated by the peer watcher's first pass (which runs immediately on startup rather than after the full poll interval).

This means that after a Hub restart, an app that reconnects with its existing session token
and calls `POST /notifications/watch` will receive updated counts within the watcher's first
polling pass (~seconds), not after the full 60-second interval.

For automated tests, a client named `"Smoke Tests"` causes the Hub to echo the PIN
back in the `/sessions/request` response rather than sending an OS notification.

## Relationship with Small Sea Manager

The Hub has a special relationship with the Small Sea Manager app.
Small Sea Manager writes the databases that the Hub reads to do its work: team membership, app registrations, cloud service credentials, etc.

The Hub never writes to Manager's databases; it only reads them.
Manager never reads the Hub's local database directly; Hub-local tables are exposed,
when needed, through Hub APIs. Session rows remain Hub-private.
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
          core.db                        ← shared NoteToSelf DB (written by Manager)
        Local/
          device_local.db                ← device-local NoteToSelf DB (written by Manager)
      {TeamName}/
        Sync/
          core.db                        ← Team DB (written by Manager, synced via Cod Sync)
```

**Tables the Hub reads from shared NoteToSelf** (`NoteToSelf/Sync/core.db`):

| Table | Used for |
|---|---|
| `nickname` | Resolving a participant by human name during session request |
| `team` | Looking up team ID for any session (including non-NoteToSelf teams) |
| `app`, `team_app_berth` | Participant-level app registration and NoteToSelf berth lookup |
| `cloud_storage` | Shared cloud account locator metadata |
| `berth_cloud_allocation` | Local participant's selected storage location for a berth |
| `notification_service` | Shared notification-service metadata |

**Tables the Hub reads from device-local NoteToSelf** (`NoteToSelf/Local/device_local.db`):

| Table | Used for |
|---|---|
| `cloud_storage_credential` | Device-local cloud auth material |
| `notification_service_credential` | Device-local notification auth material |
| `team_sender_key`, `peer_sender_key` | Device-local encrypted-runtime state |

**Tables the Hub reads from a team DB** (`{TeamName}/Sync/core.db`):

| Table | Used for |
|---|---|
| `app`, `team_app_berth` | Team-level app activation and berth lookup |
| `member_berth_storage_announcement` | Peer-readable storage location for `(member_id, berth_id)` |

## Cloud Storage

The Hub's primary implemented service today is cloud storage.
Apps upload and download opaque files through the Hub.
A valid Hub session authorizes an app to act in a berth, but it does not imply
that cloud storage exists for that berth.
Cloud storage availability is Manager-provisioned state.

The target model separates four concepts:

- **Cloud account locator:** participant-scoped shared metadata in
  `cloud_storage`, such as provider protocol and endpoint.
- **Device cloud credential:** device-local auth material in
  `local.cloud_storage_credential`.
- **Berth cloud allocation:** the local participant's chosen provider-facing
  location for one berth, stored as explicit Manager-owned allocation state.
- **Member berth storage announcement:** a signed team-visible announcement
  for `(member_id, berth_id)` telling peers where that member stores readable
  data for that berth.

The Hub must not synthesize provider-facing locations from `berth_id` as a
hidden default. If a session is valid but no berth cloud allocation exists,
cloud-file operations return a structured repairable error.

### Supported Protocols

**S3-compatible** (`protocol = "s3"`): Any S3-compatible endpoint (AWS, MinIO, etc.).
Requires `url` in shared `cloud_storage`, a berth allocation whose `location`
is the bucket name, plus `access_key` and `secret_key` in local
`cloud_storage_credential`.
Uses AWS Signature Version 4.
For S3, Manager-generated requested bucket names should obey provider naming
rules, for example `ss-{uuid7_hex}`.
Current peer S3 reads use anonymous reads from public-readable buckets. Whether
public-readable buckets remain the medium-term model is an open question.

**Google Drive** (`protocol = "gdrive"`): OAuth2.
Requires shared `client_id`, a berth allocation whose provider-facing location
may be finalized during materialization, plus local `client_secret` and
`refresh_token`.
The Hub refreshes the access token transparently before each operation and
persists the new token back to local `cloud_storage_credential`.
The current `cloud_storage.path_metadata` field is adapter cache state, not a
berth storage location.

**Dropbox** (`protocol = "dropbox"`): OAuth2.
Same token refresh pattern as Google Drive. The berth allocation `location`
is the folder prefix.

### Credential Management

Cloud storage locator metadata is stored in shared NoteToSelf; credentials and
refresh state are stored in device-local NoteToSelf.
For OAuth-based providers (Google Drive, Dropbox), the Hub handles token refresh transparently.

Credential storage is likely to evolve — options include OS keyring integration, a local vault,
or delegation to the encryption layer once that is implemented.

### Berth Allocation Resolution

For own cloud-file operations, the Hub uses the confirmed session's resolved
`berth_id` to look up `berth_cloud_allocation` in shared NoteToSelf.
It then joins the allocation's `cloud_storage_id` to shared `cloud_storage`
and device-local `cloud_storage_credential`.

The Hub does not re-read the team Core DB on every file operation. The session
was opened only after the berth was resolved from the appropriate DB, and the
session carries that `berth_id`. If a team berth is later removed while an old
session still exists, that is a general stale-session problem; orphaned
allocation rows are inert unless a valid session resolves to the same
`berth_id`.

If the allocation is missing, the Hub returns `cloud_location_missing`. If the
allocation exists but this device lacks credentials for the selected cloud
account, the Hub returns `cloud_credentials_missing`.

### Provider Materialization

The Manager records desired or finalized provider-facing locations. The Hub
materializes them against the provider. Materialization is lazy but explicit:
the Hub may materialize the recorded allocation on `POST /cloud/setup` or on
the first storage operation that needs it. Team creation, app activation, and
session open do not pre-materialize storage.

Materialization is idempotent. The Hub persists no separate per-allocation
materialization status; the allocation row is the durable record. Implementations
may cache or short-circuit provider checks later, but correctness must not
depend on hidden Hub-only state.

Some providers accept a Manager-chosen locator. Others may return a
provider-issued final locator during materialization. When a provider returns a
different final locator, the Hub may write that locator back to
`berth_cloud_allocation` as a narrow exception to "Manager decides": the Hub is
recording provider reality, not choosing policy. The writeback must be
conditional on the allocation still matching the materialization request. If a
conditional update loses a local race, the Hub re-reads and proceeds if
possible, or returns `cloud_allocation_conflict`.

Peer-visible storage announcements must be published only after the
corresponding location is successfully materialized and any provider-issued
final locator has been durably recorded.

Materialization outcomes:

| Outcome | Storage operation response | `POST /cloud/setup` response |
|---|---|---|
| `materialized` | Proceed with the operation. | `200` with `{ "status": "materialized", "location": "..." }` |
| `materialized_with_locator` | Persist final locator, then proceed. | `200` with `{ "status": "materialized_with_locator", "location": "..." }` |
| `needs_user_action` | `409` with `reason: "cloud_user_action_required"` | `409` with `reason: "cloud_user_action_required"` |
| `failed` | `409` with `reason: "cloud_materialization_failed"` | `409` with `reason: "cloud_materialization_failed"` |
| conditional writeback conflict | Re-read and proceed if possible, otherwise `409` with `reason: "cloud_allocation_conflict"` | Same as storage operation |

### Peer Storage Routing

Peer reads are scoped by `(member_id, berth_id)`, not just by member and not
just by berth. For the current session's berth, the Hub selects the target
member's newest valid `member_berth_storage_announcement` by sorting
`announcement_id` descending. `announcement_id` is UUIDv7, so that ordering is
the "newest valid" rule. `announced_at` is display/audit data and is not the
ordering authority.

An announcement is valid when its signature verifies and the signer key is
currently trusted for the announcing member. There is no max-age policy in v1.
Valid announcements take precedence over legacy `team_device(protocol, url,
bucket)` fallback. Legacy fallback is allowed only when no valid announcement
exists and must be named as legacy behavior.

Remote reads from another device of the same member use the announcement path,
not this device's local allocation. This device's local allocation describes
where this device writes. A sibling device may have written the same berth to a
different location.

### Concurrency

V1 assumes at most one Hub process per device, per participant root. Multiple
Manager-like local clients, such as the Manager web UI, Manager CLI commands,
scripts, or test harnesses, coordinate through SQLite transactions and
conditional updates.

Cross-device first-use races are possible because sibling devices sync through
Cod Sync rather than shared SQLite locks. Two sibling devices may both
materialize locations before sync convergence. For provider-issued locators,
that can create orphaned provider objects. This is recoverable clutter, not a
correctness failure, as long as peers do not silently route to the wrong
location. If multiple same-member announcements briefly coexist, peers select
the newest valid announcement by UUIDv7 `announcement_id`; after sync
convergence, losing provider locations can be cleaned up by follow-up tooling.

### CAS Uploads

`POST /cloud_file` accepts an optional `expected_etag` field.
When provided, the upload is conditional: if the current ETag of the remote file does not match,
the Hub returns `409 Conflict` with `detail: "CAS conflict: file was modified concurrently"`.
This is used by Cod Sync to detect concurrent writes to the bundle-chain head.

## Notifications

The Hub supports push notifications via two adapters. A shared
`notification_service` row must be present in NoteToSelf, and any needed auth
tokens live in device-local `notification_service_credential`.

### Supported protocols

**ntfy** (`protocol = "ntfy"`): Self-hosted or public pub/sub service.
Requires `url` (ntfy server base URL). The ntfy topic is derived automatically as
`ss-{sha256("{team}/{app}")[:16]}`, so all participants sharing a berth converge on the
same topic without any configuration. Set `access_key` if your ntfy server requires auth.

`POST /notifications` publishes; `GET /notifications` long-polls with `since` (ntfy message ID
or `"all"`) and `timeout` (seconds, default 30).

**Gotify** (`protocol = "gotify"`): Self-hosted push notification server.
Requires `url` (Gotify server base URL), `access_key` (app token for publishing), and
`access_token` (client token for polling, defaults to app token if omitted).
`GET /notifications` returns messages with id > `since` (numeric string or `"all"`) but does
not long-poll — use Gotify's WebSocket stream (`/stream`) for real-time delivery.

### Watcher-triggered notifications

When the peer watcher detects that a teammate's push count has increased, it fires a push
notification to the participant's configured service ("A teammate has pushed new data").
At most one notification is sent per berth per watcher round, regardless of how many
sessions or peers triggered the change. If no notification service is configured, the watcher
skips silently.

When the peer watcher adopts a new local team-DB view, it also asks the
Manager-owned admission-event helper for newly visible teammate
`LINKED_DEVICE` events. The Hub does not inspect admission-event SQL or decide
event taxonomy; it only delivers the returned plain-text notification payloads
through the configured adapter. A missing adapter or failed publish records no
local `notified` mark, and the watcher retries eligible events on later ticks
using notification retry state separate from runtime-reconciliation revision
tracking.

### Future: Apprise

[Apprise](https://github.com/caronc/apprise) is a Python meta-library that wraps ~100
notification services (Slack, Telegram, Pushover, Gotify, ntfy, email, and many others) behind
a single `apprise://` URL scheme. Adding an Apprise adapter would give the Hub coverage of
most remaining services without individual implementations. Planned for the future.

## Real-Time Connectivity

Not yet implemented. The Hub will negotiate VPN connections between devices.

## Encryption Layer

Not yet implemented. In production, the Hub will encrypt all outbound data and decrypt all inbound data, transparent to apps. See the top-level spec for context.

## HTTP API

All app-facing endpoints except `/sessions/request` and `/sessions/confirm`
require a Bearer token:

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

If the participant or team is unknown, the Hub returns `404`.

If the request is well-formed but Manager action is required before the app can
open a berth session, the Hub returns:

```json
{
  "error": "app_bootstrap_required",
  "reason": "app_unknown",
  "app": "SharedFileVault",
  "team": "ProjectX"
}
```

The HTTP status is `409 Conflict`. `reason` is one of:

| Reason | Meaning |
|---|---|
| `app_unknown` | No app row matching the requested friendly name exists in either the participant's NoteToSelf registration scope or the requested team's activation scope. |
| `participant_berth_missing` | The app is activated for the requested team, but the participant has no NoteToSelf registration berth for it. |
| `team_berth_missing` | The participant has registered the app, but the requested team has not activated it. |
| `app_friendly_name_ambiguous` | More than one app row matches the requested friendly name in the relevant resolution scope; the Hub refuses to pick by row order. |

For v1, app IDs are locally generated and do not align across NoteToSelf and
team scopes. The Hub therefore bridges the two scopes by friendly-name match
only when each side has exactly one candidate. Friendly names are not global
identity; ambiguity is preserved for Manager repair.

---

**`POST /sessions/confirm`**

```json
{ "pending_id": "<hex>", "pin": "<4-digit string>" }
```

Response: `"<session_token_hex>"` (bare string).

Errors: `404` if pending ID not found; `400`-level if PIN is wrong or expired.

---

**`GET /sightings`**

Returns the Hub's local observations of app-bootstrap failures for Manager
review. Sightings are observations, not decisions: Manager owns registration,
activation, and any local disposition rules for suppressing repeated prompts.

Requires a Bearer token for a `SmallSeaCollectiveCore` session. The Hub returns
only sightings for that session's participant. Other app sessions receive `403`,
and unauthenticated callers receive `401`.

Each row includes `participant_hex`, `app_name`, `team_name`, `client_name`,
`first_seen_at`, `last_seen_at`, `seen_count`, and the latest `reason`.
The `last_seen_at` value is returned byte-identical to the stored column so
Manager can use it as a precondition on `POST /sightings/clear`.

Repeated requests from the same participant/app/team/client tuple update the
existing sighting atomically rather than appending unbounded rows.

`GET /sightings` is read-only.
It does not prune stale rows or otherwise mutate Hub state.

---

#### Sighting lifecycle

Sightings are **active local observations**, not durable audit history.

- A sighting is recorded when an app-bootstrap request fails on `_resolve_berth`.
- Once participant registration and team activation exist for the same tuple,
  Hub `_resolve_berth` opens the session and `request_session` does not record
  a fresh sighting.
- Resolved sightings are cleared by Manager refresh through
  `POST /sightings/clear`, after Manager re-evaluates the row against current
  local state and decides no prompt remains.
- Sightings that have not been bumped within the configured stale window
  (default 30 days) age out through `POST /sightings/prune-stale`.
- A future retry recreates the row, so cleanup is not a durable rejection.

Sightings remain local Hub state and are never synced to peers.
Apps cannot list, clear, or otherwise mutate them — only Manager/Core sessions
can.

Hub-written timestamps (`first_seen_at`, `last_seen_at`, and the stale-window
cutoff used for pruning) are canonical UTC ISO-8601 strings with exactly six
fractional digits and a `+00:00` offset, e.g. `2026-05-01T12:00:00.000000+00:00`.
That makes lexicographic SQL comparison match chronological order.

---

**`POST /sightings/clear`**

Deletes a single sighting whose `(app_name, team_name, client_name)` matches
the request body, scoped to the participant derived from the session token.
The body must also include a `last_seen_at` precondition: the Hub deletes only
when the row's stored `last_seen_at` is byte-identical to the supplied value.

```json
{
  "app_name": "SharedFileVault",
  "team_name": "ProjectX",
  "client_name": "shared-file-vault:default",
  "last_seen_at": "2026-05-01T12:00:00.000000+00:00"
}
```

All four fields are required. Empty strings are literal values; there is no
wildcard delete. Manager must echo `last_seen_at` from `GET /sightings`
without parsing or reformatting.

Response: `{ "deleted_count": 0 | 1 }`. The endpoint is idempotent: if no row
matches (already cleared, or `last_seen_at` was bumped by a concurrent retry),
the response is `200` with `deleted_count = 0`.

Authorization is identical to `GET /sightings`: a Bearer token for a
`SmallSeaCollectiveCore` session. Other app sessions receive `403`;
unauthenticated callers receive `401`.

---

**`POST /sightings/prune-stale`**

Deletes sightings for the session's participant whose `last_seen_at` is
strictly older than the stale-window cutoff (default 30 days). Rows exactly
at the cutoff survive until a later prune pass.

The request body is empty. `{}` is also accepted because some clients post
JSON bodies by default.

Response: `{ "pruned_count": <int> }`.

Authorization matches `GET /sightings` and `POST /sightings/clear`. Pruning
is participant-scoped: a session for participant A cannot prune participant
B's rows.

---

### Cloud storage endpoints

**`POST /cloud/setup`** - Materialize the provisioned cloud location for the current session's berth.
Safe to call multiple times.

Success response:
```json
{ "status": "materialized", "location": "<provider-facing locator>" }
```

or:
```json
{ "status": "materialized_with_locator", "location": "<provider-issued locator>" }
```

Repairable failures use the `cloud_storage_required` error family with status
`409`.

---

**`POST /cloud_file`** — Upload a file.

```json
{
  "path": "<remote path>",
  "data": "<base64>",
  "expected_etag": "<etag or null>",
  "notify": false
}
```

`notify` (default `false`): when `true` and the upload succeeds, the Hub atomically increments
`signals.yaml` in the session's cloud bucket and pulses the berth event so other sessions on
the same berth are notified without waiting for the next watcher round.
Cod Sync sets `notify=true` when uploading `latest-link.yaml`.

Response:
```json
{ "ok": true, "etag": "<etag>", "message": "" }
```

Errors:

- `409` on CAS conflict
- `409` with `{ "error": "cloud_storage_required", "reason": "cloud_location_missing" }`
- `409` with `{ "error": "cloud_storage_required", "reason": "cloud_credentials_missing" }`
- `409` with `{ "error": "cloud_storage_required", "reason": "cloud_user_action_required" }`
- `409` with `{ "error": "cloud_storage_required", "reason": "cloud_materialization_failed" }`
- `409` with `{ "error": "cloud_storage_required", "reason": "cloud_allocation_conflict" }`

---

**`GET /cloud_file?path=<remote path>`** — Download a file.

Response:
```json
{ "ok": true, "data": "<base64>", "etag": "<etag>" }
```

Errors: `404` if not found.

---

**`GET /peer_cloud_file?member_id=<hex>&path=<remote path>`** — Download a file from a peer's
cloud location via the Hub proxy. The Hub resolves the target member's readable
location through the newest valid `member_berth_storage_announcement` for
`(member_id, session.berth_id)`. Legacy `team_device` transport data may be
used only as an explicitly named fallback when no valid announcement exists.

Response: same as `GET /cloud_file`.

Errors: `404` if peer not found or file not found.

---

**`GET /peer_signal?member_id=<hex>`** — Return the parsed signal file for a peer.

Response:
```json
{ "version": 1, "berths": { "<berth_id_hex>": <count>, ... }, "etag": "<etag>" }
```

Returns `304` if `If-None-Match` header matches current etag.
Errors: `404` if the peer's `signals.yaml` does not exist yet.

---

### Sync notification endpoints

**`POST /notifications/watch`** — Long-poll for peer sync updates.

The client supplies its current known push counts per peer member. If the Hub already has higher
counts for any of them, returns immediately. Otherwise blocks until a peer's count increases,
or until timeout.

```json
{ "known": { "<member_id_hex>": <last_known_count>, ... }, "timeout": 30 }
```

Response:
```json
{ "updated": { "<member_id_hex>": <new_count>, ... } }
```

`updated` is empty on timeout or on a structural change (membership update, local `notify=True`
upload from another session on the same berth, or any local team-DB revision change detected by
the watcher). An empty response is not an error — it is the signal to re-enumerate local team
state before the next watch call.

---

### ntfy notification endpoints

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
| `berth_id` | BLOB | Resolves Manager-provisioned berth cloud allocation |
| `client` | TEXT | Human name of the requesting client |

The `notification_service` table lives in the participant's NoteToSelf DB (managed by Small Sea
Manager), not the Hub's local DB. See §Notifications above.

**`pending_session` table** — In-flight approval requests (deleted on confirm or expiry):

| Column | Type | Notes |
|---|---|---|
| `id` | BLOB | UUID7, primary key |
| `participant_hex` | TEXT | |
| `team_name`, `app_name`, `client_name` | TEXT | |
| `pin` | TEXT | 4-digit zero-padded string |
| `created_at`, `expires_at` | TEXT | ISO 8601; TTL is 5 minutes |

**`unknown_app_sighting` table** — Hub-local app-bootstrap observations:

| Column | Type | Notes |
|---|---|---|
| `participant_hex` | TEXT | Participant whose Hub observed the request |
| `app_name` | TEXT | Friendly app name claimed by the client |
| `team_name` | TEXT | Requested team name |
| `client_name` | TEXT | Stable app-chosen client installation label |
| `first_seen_at`, `last_seen_at` | TEXT | ISO 8601 timestamps |
| `seen_count` | INTEGER | Incremented on repeated sightings |
| `reason` | TEXT | Latest structured bootstrap rejection reason |

Unique key: `(participant_hex, app_name, team_name, client_name)`.
Sightings are local to this Hub and are not synced.
`first_seen_at` and `last_seen_at` are written through a single canonical
helper that uses `isoformat(timespec="microseconds")` with `+00:00`, so that
lexicographic comparison of the column matches chronological order.

## Open Questions

- Should the Hub enforce permissions, or is enforcement purely cryptographic? (Current design: permissions are a social contract; see top-level spec.)
- Can a single Hub instance serve multiple users on the same device?
- How will credential storage evolve when the encryption layer is implemented?
