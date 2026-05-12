# Review Note

This branch implements issue #130 by making Shared File Vault materialize
session-backed app state under
`participants/{participant_hex}/teams/{team_id}` instead of under the
friendly `team_name`. The Vault `team_id` is sourced from Hub
`session_info["berth_id"]` once, at login, and then lives in `metadata.json`
for offline resolution by every subsequent operation.

The key invariant: a Vault operation needs the Hub only when (a) it is
learning `team_id` for the first time, or (b) it is performing a
Hub-mediated action (push/pull/fetch/peer-listing). Every other operation
resolves `team_name → team_id` offline from `metadata.json`. There is no
legacy fallback — operations either have a real context derived from a real
session or they fail loudly.

The main review path is:

- `packages/shared-file-vault/shared_file_vault/vault.py` for
  `VaultMaterializationContext`, `materialize_team`, `iter_materialized_teams`,
  the `_validate_context` validator (no string fallback), path helpers, and
  SQLite key changes.
- `packages/shared-file-vault/shared_file_vault/sync.py` for `login_team`
  (which writes `metadata.json`), the offline `resolve_team_context`,
  `get_team_session` (no preemptive Hub validation), and the
  `TeamNotMaterializedError`/`AmbiguousTeamNameError` exceptions.
- `packages/shared-file-vault/shared_file_vault/cli.py` for the offline
  `_team_context` helper used by local commands, and the `login_cmd` that
  threads `vault_root` into `sync.login_team`.
- `packages/shared-file-vault/shared_file_vault/web.py` for the same
  offline resolver, the removal of `POST /teams/create`, and the index page
  that lists materialized teams directly from `iter_materialized_teams`.
- `packages/shared-file-vault/tests/test_vault.py`,
  `packages/shared-file-vault/tests/test_hub_sync.py`,
  `packages/shared-file-vault/tests/test_web_sync.py`,
  `packages/shared-file-vault/tests/test_sync.py`,
  `packages/shared-file-vault/tests/test_scenarios.py`, and
  `packages/shared-file-vault/tests/test_aspirational.py` for the new
  micro tests and the materialize-then-operate pattern.
- `packages/small-sea-manager/tests/test_create_team.py` for the negative
  assertions confirming Manager registration does not create Vault-owned
  directories.

Validation run:

```text
uv run pytest packages/shared-file-vault/tests -q
uv run pytest packages/small-sea-manager/tests/test_create_team.py -q
```
