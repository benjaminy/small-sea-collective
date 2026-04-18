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

Manager-local admission prompt dismissals are stored in a per-team sidecar DB outside `Sync/`, keyed by `(event_type, artifact_id)`, so ignored prompts persist across restarts without becoming synced team state.


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

#### Linked-device team bootstrap into an existing team

The current implemented slice is **per-team** and **same-member**: an already
linked device can be bootstrapped into one existing team by another device that
already belongs to the same participant in that team. This is not a blanket
"join every known team" operation. Each team is bootstrapped independently.

Protocol/product boundary:

- **Payload 0 prerequisite** — Before linked-team bootstrap begins, the joining
  device must already know the team exists in shared NoteToSelf and must already
  have a readable baseline clone of that team's repo. The current same-member
  bootstrap flow does **not** solve team discovery or baseline delivery by
  itself.
  Evidence: `prepare_linked_device_team_join(...)` looks up the team from the
  local NoteToSelf `team` row via `_team_row(...)` in
  `small_sea_manager/provisioning.py`; the micro tests still prepare that team
  baseline explicitly with `_copy_team_baseline(...)` in
  `tests/test_linked_device_bootstrap.py`.
- **Scope of the current slice** — The current flow bootstraps the new device
  into one team using a sibling device of the same member. As part of bootstrap,
  the sibling hands off its snapshot of peer sender keys, giving the new device
  join-time-forward access across all senders the sibling held. Each team is
  bootstrapped independently; this is not a blanket "join every known team"
  operation.
  Evidence of bootstrap flow structure (peer-sender-key handoff is B3 scope;
  see implementation-status note below):
  `prepare_linked_device_team_join(...)`,
  `create_linked_device_bootstrap(...)`,
  `finalize_linked_device_bootstrap(...)`, and
  `complete_linked_device_bootstrap(...)` in
  `small_sea_manager/provisioning.py`.
- **Payload 3 transport status** — The joining device still returns its sender
  distribution payload to the authorizing device as an explicit follow-up
  artifact. This slice does not yet define a fully automatic Hub-mediated
  return path.
  Evidence: `finalize_linked_device_bootstrap(...)` returns
  `sender_distribution_payload`, and `complete_linked_device_bootstrap(...)`
  consumes it in `small_sea_manager/provisioning.py`.

Current same-member flow:

1. The joining device calls `prepare_linked_device_team_join(...)`, generates a
   fresh team-device keypair plus X3DH prekeys, stores the bootstrap session in
   device-local NoteToSelf state, and emits a signed join-request bundle.
   Evidence: `prepare_linked_device_team_join(...)` and the
   `linked_team_bootstrap_session` table in
   `small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`.
2. An already-live sibling device calls `create_linked_device_bootstrap(...)`,
   verifies the NoteToSelf signature and proposed team-device signature, issues
   the `device_link` cert, encrypts the current sender-key distribution through
   X3DH + ratchet, and records a pending bootstrap breadcrumb.
   Evidence: `create_linked_device_bootstrap(...)` in
   `small_sea_manager/provisioning.py`.
3. The joining device calls `finalize_linked_device_bootstrap(...)`, verifies
   the cert and authorizing device signature, stores the sibling device's sender
   state locally, persists its own team-device key, initializes its own sender
   state, stores the `device_link` cert in the local team DB, and emits payload
   3.
   Evidence: `finalize_linked_device_bootstrap(...)` in
   `small_sea_manager/provisioning.py`.
4. The authorizing sibling device calls `complete_linked_device_bootstrap(...)`
   and stores the joining device's sender state as a peer receiver record.
   Evidence: `complete_linked_device_bootstrap(...)` in
   `small_sea_manager/provisioning.py`.

Historical boundary and visibility:

- **The access policy is join-time-forward.** The new device inherits the
  sibling's snapshot of peer sender keys at bootstrap time and can read forward
  from that point across all senders the sibling held. It does not receive
  ciphertext from before the `device_link` cert was published — that historical
  material was encrypted without the new device's keys.
  Evidence: `test_linked_device_bootstrap_round_trip_same_member` asserts that
  a pre-bootstrap encrypted message cannot be decrypted after bootstrap.
- That test is **repo-local protocol evidence**, not a full cryptographic
  assurance. It depends on current Cuttlefish group-encryption behavior in a
  pre-alpha repo where crypto internals are still evolving.
  Evidence: `group_encrypt(...)` / `group_decrypt(...)` in
  `packages/cuttlefish/cuttlefish/group.py`, plus the linked-device bootstrap
  test above.

> **Implementation status (B3 scope):** The peer-sender-key handoff is not yet
> fully implemented. In the current code, the sibling does not yet pass its
> snapshot of peer sender keys as part of the bootstrap bundle.
> `test_linked_device_bootstrap_requires_real_redistribution_for_other_senders`
> exercises this code gap and is retired by B3. The normative model above
> describes the accepted design; the current code reflects interim state.

Retry/idempotency status in the current slice:

- Repeating `prepare_linked_device_team_join(...)` while an unfinalized bootstrap
  session already exists for the same team is rejected rather than silently
  orphaning the first session.
  Evidence: `prepare_linked_device_team_join(...)` and
  `test_linked_device_bootstrap_prepare_reentry_is_rejected`.
- Repeating `finalize_linked_device_bootstrap(...)` for the same bootstrap bundle
  is idempotent once the response payload has been stored.
  Evidence: `finalize_linked_device_bootstrap(...)` and
  `test_linked_device_bootstrap_retry_after_interrupted_finalize_is_idempotent`.
- Repeating `complete_linked_device_bootstrap(...)` with the same signed payload
  is idempotent after the peer sender state has already been stored, even if the
  previous attempt crashed before clearing the pending breadcrumb.
  Evidence: `complete_linked_device_bootstrap(...)` and
  `test_linked_device_bootstrap_retry_after_interrupted_complete_is_idempotent`.
- Repeating `create_linked_device_bootstrap(...)` with the exact same valid join
  request bundle is handled by **store-and-replay**: the authorizing device
  returns the originally stored bootstrap bundle from the pending bootstrap
  breadcrumb instead of minting a fresh encrypted response, and does not create
  another cert commit for the same logical create step.
  Evidence: `create_linked_device_bootstrap(...)` and
  `test_linked_device_bootstrap_create_replay_returns_stored_bundle_without_extra_commit`.
- Crash-mid-create is still a current limitation. If the authorizing device
  crashes after cert issuance but before the pending breadcrumb with stored
  bootstrap bundle is written, the team DB can contain the issued `device_link`
  cert while no replayable bootstrap bundle exists. In that state the joining
  device has nothing to finalize, retry cannot replay from stored state, and
  operator recovery requires manual cleanup or a new bootstrap attempt with
  fresh request material.

Storage boundary:

- Linked-team bootstrap session state is device-local NoteToSelf state, not
  shared NoteToSelf sync state. The `linked_team_bootstrap_session` table lives
  in `NoteToSelf/Local/device_local.db` and persists as normal local schema; it
  is not a shared `NoteToSelf/Sync/core.db` table.
  Evidence: `small-sea-note-to-self/small_sea_note_to_self/sql/device_local_schema.sql`
  and `test_linked_device_bootstrap_round_trip_same_member`.

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

Initiates an invitation proposal: allocates a fresh UUIDv7 `member_id` for the
prospective invitee, anchors the proposal to the current team-history commit
hash (freezing the admin roster, membership roster, and member→device mapping
at that snapshot), records a proposal shell in team DB, commits and pushes, and
returns a proposal token for out-of-band delivery to the invitee.

Inputs: `team_name`, optional `invitee_label`, `role` (default: admin).

Token contents: proposal ID, nonce, team name, inviter member ID, inviter display
name, inviter cloud endpoint (protocol + URL only — no credentials), and the
pre-allocated invitee `member_id`. Privacy is provided by E2E encryption
(issue #0008), not by access control.

The proposal shell is visible to all admins in the frozen governance set as
soon as it is pushed — before the invitee is contacted.

#### List invitations

Reads invitation proposal rows from team DB. Does not query the Hub.

#### Revoke invitation

Marks a pending proposal as revoked. A revoked proposal cannot be finalized.
Commits.

#### Accept invitation (invitee side)

Takes an out-of-band proposal token. All cloud I/O goes through the Hub:

1. Opens a NoteToSelf Hub session, calls `GET /cloud_proxy` to download the
   inviter's team bundle chain. The Hub proxies the bytes — the Manager never
   contacts cloud storage directly.
2. Clones the team repo locally, installs the splice-sqlite merge driver, and
   adds a Team pointer to NoteToSelf DB. (All local DB/git ops; no network.)
3. Generates a fresh team device keypair (bootstrap-encryption key + signing
   key).
4. Signs an acceptance blob binding to the inviter-allocated `member_id` and
   the proposal ID/nonce. Cloud endpoints are **not** included in the
   acceptance blob — transport is configured post-admission (B7 scope).

Returns the signed acceptance blob for out-of-band delivery back to the
inviter. The invitee does **not** write any rows to the shared team DB at this
stage. The invitee never publishes their own admission.

#### Finalize invitation (inviter side)

Takes the invitee's out-of-band acceptance blob. The inviter:

1. Verifies the acceptance blob (signature valid, binds to the correct
   `member_id` and proposal nonce).
2. Assembles the full admission transcript: proposal ID/nonce, team-history
   anchor reference, frozen-governance-state digest (covers admin roster,
   membership roster, and member→device mapping at the anchor), inviter/
   finalizer `member_id`, pre-allocated invitee `member_id`, and the invitee's
   signed acceptance blob carrying the invitee's concrete device keys.
   Transport metadata is explicitly excluded from the transcript.
3. Signs an approval over the transcript (counts as 1 toward quorum). Publishes
   transcript + approval as an update to the existing proposal row.
4. For `quorum > 1`: waits for other admins' approval signatures to accrue in
   team DB. Each approval is valid iff its signing key appears in a
   `device_link` cert at the anchor that maps to a current-admin `member_id`
   (the member/device bridge derivation). Quorum counts distinct
   `admin_member_id`s over valid approval rows; multiple approvals from
   different devices of the same admin dedupe to one vote.
5. Upon observing quorum met, signs and publishes the finalization mutation.
   Commits.

After finalization, the newly admitted member sets up their incoming cloud
endpoint via the member-transport-configuration flow (B7) and then publishes
their own sender key via `redistribute_sender_key(...)`.

DB schema for proposals, acceptance transcripts, and approval signatures: see
[SQL Schemas → Team schema](#sql-schemas) below — the current `invitation`
table is a placeholder pending the B5 schema definition.

#### Member transport configuration (B7)

Mutable incoming transport is member-scoped state published after admission,
not part of the immutable admission transcript.

The team DB therefore carries a separate append-only signed table:

| Column | Meaning |
|-------|---------|
| `announcement_id` | UUIDv7 primary key; also the ordering key for "newest wins" |
| `member_id` | Team-local identity whose incoming transport is being published |
| `protocol` | Transport protocol (`s3`, `dropbox`, `localfolder`, etc.) |
| `url` | Endpoint base URL |
| `bucket` | Bucket or folder prefix peers must use for this member's incoming path |
| `announced_at` | Display/audit timestamp only; not the ordering authority |
| `signer_key_id` | Device key that signed the announcement |
| `signature` | Signature over the canonical payload |

Canonical signed payload fields are:

- `announcement_id`
- `member_id`
- `protocol`
- `url`
- `bucket`
- `announced_at`
- `signer_key_id`

Verification and selection rules:

1. Team DB sync may bring in any syntactically valid row.
2. Effective transport for a member is selected by descending UUIDv7
   `announcement_id`, not by `announced_at`.
3. A row is usable iff its signature verifies under `signer_key_id` and that
   signer resolves, at derivation time, to one of the member's currently
   trusted device keys via the team DB's `key_certificate` history.
4. Invalid or no-longer-trusted rows remain inert data; they do not become
   effective routing state.

Important implementation bridge:

- team DB `key_certificate` rows omit `team_id` because the team is implicit in
  the DB file
- when reconstructing `wrasse_trust.identity.KeyCertificate` objects from DB
  rows, callers must inject the enclosing team's `team_id`
- callers must also bridge DB field names to dataclass fields:
  `issuer_member_id -> issuer_participant_id`,
  `issued_at -> issued_at_iso`,
  and JSON-decode `claims TEXT -> claims dict`

Current trust-removal semantics are intentionally modest: in today's codebase,
"signer no longer trusted" means the relevant trust path is no longer present
in the adopted `key_certificate` view. B7 does not depend on a revocation-cert
issuance path.

Current runtime status exposed by Manager reads is:

- `announced` — a valid transport announcement is selected
- `legacy-fallback` — routing still relies on legacy `team_device` transport
  fields
- `missing` — no usable current transport exists

The `legacy-fallback` path is **temporary** compatibility infrastructure while
current admission flows still populate `team_device(protocol, url, bucket)`.
Once B5 removes admission-time transport coupling, this fallback should be
deleted and peer routing should rely only on the B7 announcement flow.

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
The inviter orchestrates the entire flow; the invitee never writes to the
shared team DB.

```
Alice (inviter)                    Bob (invitee)         Other admins
  |                                     |                      |
  | create_invitation()                 |                      |
  |  → allocates invitee member_id      |                      |
  |  → anchors to team-history commit   |                      |
  |    hash (freezes admin/member/      |                      |
  |    device mapping at snapshot)      |                      |
  |  → publishes proposal shell to      |                      |
  |    {TeamName}/Sync/core.db            |                      |
  |  → commits + pushes via Hub         |                      |
  |  → returns token_b64                |                      |
  |                                (proposal now visible)      |
  |                                     |                      |
  | ---- token_b64 (out of band) -----> |                      |
  |                                     |                      |
  |                     accept_invitation(token_b64)           |
  |                      → opens NoteToSelf Hub session        |
  |                      → Hub /cloud_proxy fetches Alice's    |
  |                        team bundle chain (anonymous read)  |
  |                      → clones {TeamName}/Sync locally        |
  |                      → generates fresh team device keypair |
  |                      → signs acceptance blob binding to    |
  |                        inviter-allocated member_id and     |
  |                        proposal nonce                      |
  |                        (NO cloud endpoint in blob)         |
  |                      → returns acceptance_b64              |
  |                                     |                      |
  | <--- acceptance_b64 (out of band) - |                      |
  |                                     |                      |
  | finalize_invitation(acceptance_b64) |                      |
  |  → verifies acceptance blob         |                      |
  |  → assembles admission transcript   |                      |
  |    (anchor, member_id, invitee keys;|                      |
  |     NO transport metadata)          |                      |
  |  → signs approval over transcript   |                      |
  |  → publishes transcript + approval  |                      |
  |    as update to proposal row        |                      |
  |  → commits + pushes via Hub         |                      |
  |                                     |           (syncs; verifies transcript
  |                                     |            against anchor; signs
  |                                     |            approval if quorum > 1;
  |                                     |            pushes approval row)
  |                                     |                      |
  | [inviter observes quorum met]       |                      |
  |  → publishes finalization mutation  |                      |
  |  → commits + pushes via Hub         |                      |
  |                                     |                      |
  | ---- finalization notice (OOB) ---> |                      |
  |                                     |                      |
  |              [Bob runs B7 transport-config flow to stand   |
  |               up incoming cloud endpoint, then publishes   |
  |               own sender key via redistribute_sender_key]  |
```

**Token contents:** proposal ID, nonce, team name, inviter member ID, inviter
display name, inviter cloud endpoint (protocol + URL only — no credentials),
pre-allocated invitee `member_id`.

**Acceptance blob contents:** proposal ID, nonce, the invitee's concrete device
bootstrap-encryption key and signing key, confirmation of the inviter-allocated
`member_id`. No cloud endpoint — transport is not part of the admission
transcript.

**Security model:** Inviter's bucket is publicly readable (anonymous reads via
unsigned requests). Privacy is provided by E2E encryption (issue #0008), not
by access control. Credentials are never transmitted in tokens.

**Quorum at default (`quorum = 1`):** Other-admin approval step is skipped;
inviter proceeds directly from publishing transcript + own approval to
publishing finalization.

**Proposal invalidation:** If the admin roster, membership roster, or
member→device mapping changes relative to the anchor before finalization, the
proposal is invalid and cannot be finalized. The inviter must start a new
proposal from the updated state.

**Member/device bridge for approvals:** Each approval signature is validated
against the member→device mapping frozen at the anchor. An approval is valid
iff the signing device key appears in a `device_link` cert at the anchor that
maps to a current-admin `member_id`. Approvals by post-anchor devices or
non-admins are rejected; multiple approvals from devices of the same admin
dedupe to one vote per `admin_member_id`.

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

-- [SCHEMA TBD — to be defined in B5]
-- Target fields (from accepted model):
--   proposal table: proposal_id, nonce, team_history_anchor (commit hash),
--     frozen_governance_digest, inviter_member_id (= finalizer_member_id),
--     pre_allocated_invitee_member_id, role, invitee_label, state,
--     created_at, expires_at
--   acceptance_transcript: proposal_id (FK), invitee_device_bootstrap_key,
--     invitee_device_signing_key, invitee_acceptance_signature
--   admin_approval_signatures: proposal_id (FK), admin_member_id,
--     approver_device_key_id, transcript_digest, signature, created_at
-- Transport metadata (cloud endpoints etc.) is NOT part of this schema;
-- that is configured post-admission via the B7 member-transport flow.

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
