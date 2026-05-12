# Design Record: Vault Materialization Coordinates

Vault has an explicit `VaultMaterializationContext` carrying
`(participant_hex, team_id, team_name, app_name)`. The Hub session is the one
and only source of `team_id`: every Vault context is constructed from a
`/session/info` response. After construction, `team_id` lives on disk in
`metadata.json` and is durable across restarts and offline operation.

## Two reasons to need a session

A Vault operation needs to contact the Hub in exactly two situations:

1. **First-time team discovery.** The first interaction with a friendly
   `team_name` requires a session so Vault can learn the corresponding
   `team_id` and write `metadata.json` under
   `participants/{participant_hex}/teams/{team_id}/`. This is what
   `sync.login_team` does.
2. **Hub-mediated operations.** Push, pull, fetch, peer listing,
   notifications — these intrinsically talk to the Hub. They use the cached
   session token directly; if the Hub rejects it, the caller learns at that
   moment and re-runs `login_team` to refresh.

All other Vault operations resolve `team_name → team_id` offline by reading
`metadata.json`. `sync.resolve_team_context` performs that scan. No Hub
call. If no materialization exists, it raises `TeamNotMaterializedError`
with a clear "log in first" message. If two materialized teams share the
same friendly name, it raises `AmbiguousTeamNameError`
(#113/#115 territory).

There is no legacy fallback. Vault operations either have a real context
derived from a real session, or they error.

## Local layout

```text
{vault_root}/participants/{participant_hex}/teams/{team_id}/
  metadata.json     # {team_id, team_name, app_name}
  registry/git/, registry/checkout/
  niches/{niche_name}/git/
```

`checkouts.db` lives at the participant level and keys checkout rows, peer
sync rows, and peer signal watermarks by `team_id`. Because the repo is
pre-alpha, stale local SQLite state is recreated on schema version mismatch
rather than migrated. The old TOML `peer_signal_watermarks` section is no
longer written; watermarks now live in `checkouts.db`.

## Cloud paths

Hub-backed cloud object keys live within the Hub-provided session storage
boundary. Vault uses `registry/` and `niches/{niche_name}/` only; it does
not add `team_name` or `team_id` to those object prefixes. The
implementation relies on Hub session/backend scoping verified in
`small_sea_hub.backend.SmallSeaBackend._cloud_adapter`, where S3 buckets
derive from `ss_session.berth_id`, and in peer reads, where ordinary app
sessions derive the peer bucket from the current session berth.

## Boundaries between layers

- `vault.*` operations take a `VaultMaterializationContext`. The
  `_validate_context` helper raises `TypeError` if anything else is passed,
  and `ValueError` if `context.participant_hex` does not match the caller's
  `participant_hex`. There is no string-accepting branch.
- `sync.resolve_team_context(vault_root, participant_hex, team_name)`
  produces a context from local metadata. It is the bridge between
  friendly-name UI inputs and the strict context API.
- `sync.get_team_session` returns a `SmallSeaSession` built from the cached
  token without contacting the Hub. The Hub call happens at point of use.
- `sync.login_team(vault_root, ...)` is the CLI entry point that
  legitimately needs the Hub to discover `team_id`.
  It delegates to `sync.finalize_login(vault_root, team_name, participant_hex, session)`,
  the shared helper that validates `session_info`,
  calls `vault.materialize_team`,
  and caches the session token.
- The two web session endpoints (`POST /teams/{team}/session/request` and
  `POST /teams/{team}/session/confirm`) also go through `finalize_login`,
  so a web login materializes the team identically to a CLI login.
- `vault.iter_materialized_teams` validates each `metadata.json` against
  Vault's integrity rules: `app_name` must equal `"SharedFileVault"`,
  `team_id` must equal the directory name, and `team_name` must be
  non-empty.
  Entries failing those checks are skipped silently.
  This keeps a tampered or stale metadata file from yielding a context
  that points to the wrong team or wrong app.
- Team creation is a Manager function and does not go through Vault.
  The former `POST /teams/create` web endpoint and its index-page form
  have been removed; the empty-state index page points users to
  `shared-file-vault login <team_name>` on the CLI.
