# Shared File Vault Hub Sync

Final branch record for
[Issues/0026-task-shared-file-vault-sync.md](Issues/0026-task-shared-file-vault-sync.md)
on branch `vault-building-blocks`.

## Branch Goal

Move Shared File Vault from a local-only prototype to a credible Small Sea app:

- Vault can open and reuse Hub team sessions
- Vault can push and pull niche data through the Hub
- the web UI can drive that flow, including PIN-based session confirmation
- Vault moves toward teammate-oriented sync UX without violating the
  Manager/Hub boundary rules

## What Landed

### 1. Hub-backed Vault sync layer

Shared File Vault now has a dedicated sync helper module that owns:

- config loading and persistence
- cached per-team Hub session tokens
- Hub remote construction
- higher-level push/pull helpers for niche and registry sync

This keeps Hub-specific logic out of the web layer and CLI command bodies.

### 2. Vault config and cached team sessions

Vault now supports a minimal config shape including:

- `vault_root`
- `participant_hex`
- `hub_port`
- cached per-team session tokens

This is enough for Vault to resume team sessions without repeated approval in
the common case.

### 3. CLI login and manual sync

Vault now supports:

- `shared-file-vault login TEAM_NAME`
- `shared-file-vault push TEAM_NAME NICHE_NAME`
- `shared-file-vault pull TEAM_NAME NICHE_NAME --from-member MEMBER_ID`

The login path supports both auto-approve and PIN-confirmation flows.

### 4. Web session flow and manual sync

The niche detail view now includes:

- team session status
- request session
- resend notification
- enter PIN / confirm session
- close session
- push
- pull through the Hub

This means a user can stay in the Vault web UI for the whole manual-sync flow.

### 5. Peer discovery through the Hub

The Hub now exposes `GET /session/peers`, and Vault uses it instead of reading
team DBs directly.

Vault's web UI no longer requires raw peer member ID entry for the normal pull
flow. It renders teammate-oriented pull controls driven by Hub peer discovery.

### 6. Team-scoped peer display names

While implementing peer discovery, it became clear that invitation-label
inference was too weak a foundation for teammate-oriented UI.

This branch therefore also added:

- `peer.display_name` in the team DB schema
- invitation-token support for `inviter_display_name`
- acceptance-flow writes so both sides store team-scoped peer display names
- Hub peer listing that prefers `peer.display_name` and falls back to older
  invitation-derived labels when necessary

This is slightly beyond the original narrow 0026 plan, but it materially
improves the product direction and keeps the API honest.

### 7. Conflict surfacing

Vault now has a typed merge-conflict path for pull behavior and surfaces
conflicting file paths so the user has something actionable rather than a vague
error.

## Architectural Checks

This branch preserves the important repo boundaries:

- Vault runtime code does not read team `core.db` directly
- peer discovery is routed through the Hub
- internet-facing sync still flows through the Hub
- local-only tests remain the main validation path

## Validation Completed

Micro tests now cover:

- config and remote-prefix helpers
- cached team session reuse
- CLI login
- CLI Hub-backed push/pull
- web session request and PIN-confirmation flow
- web push/pull through the Hub
- Hub peer discovery
- checkout refresh after pull
- conflict reporting
- invitation-flow population of team-scoped peer display names

## Remaining Follow-on Work

This branch is functionally complete against its plan. The next meaningful work
is no longer "finish this branch," but "build the next UX layer."

That follow-on is captured in:

- [Issues/0030-task-vault-peer-update-ux.md](Issues/0030-task-vault-peer-update-ux.md)

In short, what remains is:

- "who has changes?" / update-aware teammate UX
- background refresh of update hints
- eventually richer notification-driven awareness

## Outcome

The branch achieved the main goal: Shared File Vault now has a credible,
architecturally honest Hub-backed sync story for demos.

The user-facing story is now much closer to:

- request a team session
- confirm PIN if needed
- push your niche through the Hub
- pull a teammate's niche through the Hub

rather than:

- manually construct remotes
- read team DBs from Vault
- type raw internal peer IDs into the UI
