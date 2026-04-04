> Migrated to GitHub issue #8.

---
id: 0020
title: Hub read-only API for app self-configuration
type: task
priority: medium
---

## Background

Apps other than the Manager need basic Small Sea identity information to
configure themselves: at minimum, the local participant's hex ID and the names
of teams they belong to. Currently there is no sanctioned way to get this —
apps must either ask the user to type their `participant_hex` into a config
file, or read `core.db` directly, which violates the access boundary.

The Manager is the sole writer of `core.db`. The Hub already reads it. The
Hub is therefore the right place to expose a read-only projection of this
data to other apps.

## Access boundary principle

Only the Manager and the Hub should directly access
`{Team}/SmallSeaCollectiveCore` stations (i.e. `core.db` files). All other
apps must use the Hub API described here.

## Proposed endpoints

### Tier 1 — No auth required (local-only, 127.0.0.1)

These read only from NoteToSelf and are safe to serve without auth because the
Hub only binds to localhost. They are intended for app self-configuration at
startup.

```
GET /info
→ { participant_hex, nickname }

GET /teams
→ [{ name, id }]
```

`/info` lets an app discover its own participant identity without the user
needing to know or type a hex string.

`/teams` lets an app enumerate the teams it can offer the user — e.g. a Vault
`serve` command can pre-populate a "which team?" picker instead of requiring
manual config.

### Tier 2 — Requires a SmallSeaCollectiveCore session

Full team details are more sensitive (member identities, roles) and require
a session scoped to the team's Core station.

```
GET /teams/{team_name}
→ { name, members: [{ id, role }], stations: [{ app_name, id }] }
```

The session must be for `{team_name}/SmallSeaCollectiveCore`. The Hub
validates this before returning the response.

## Impact on other apps

Once Tier 1 is available:

- **Shared File Vault** `serve` command can drop `participant_hex` from its
  config file — it reads `/info` at startup instead. The config file then only
  needs `vault_root` and optionally `hub_port`.
- Any future app follows the same pattern: configure `hub_port`, derive
  everything else from the Hub.

## Implementation notes

- The Hub already reads NoteToSelf DB to handle sessions; Tier 1 is a thin
  read-only projection of existing in-memory data.
- Tier 2 can reuse the existing session validation logic; the response is just
  a DB read from the team `core.db`.
- No writes through this API — all writes go through the Manager.

## References

- `packages/small-sea-hub/` — Hub server and backend
- `packages/small-sea-manager/spec.md` — Manager ↔ Hub relationship, schema
- `packages/shared-file-vault/shared_file_vault/cli.py` — current `serve`
  command that reads `participant_hex` from config file as a workaround
