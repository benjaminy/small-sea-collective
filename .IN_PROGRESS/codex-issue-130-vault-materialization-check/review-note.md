# Review Note

This branch implements issue #130 by making Shared File Vault materialize
session-backed app state under
`participants/{participant_hex}/teams/{team_id}` instead of under the friendly
`team_name`.
Vault `team_id` is sourced from Hub `session_info["berth_id"]`.

The main review path is:

- `packages/shared-file-vault/shared_file_vault/vault.py` for
  `VaultMaterializationContext`, path helpers, SQLite key changes, and local
  team metadata.
- `packages/shared-file-vault/shared_file_vault/sync.py` for Hub session
  context construction and within-session cloud prefixes.
- `packages/shared-file-vault/shared_file_vault/web.py` for using the active
  session context when the web UI calls Vault.
- `packages/shared-file-vault/shared_file_vault/cli.py` for using that same
  cached-session context in local CLI commands.
- `packages/shared-file-vault/tests/test_vault.py`,
  `packages/shared-file-vault/tests/test_hub_sync.py`, and
  `packages/shared-file-vault/tests/test_web_sync.py` for the new micro tests.

Validation run:

```text
uv run pytest packages/shared-file-vault/tests -q
uv run pytest packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_app_sightings_cleanup.py packages/small-sea-manager/tests/test_app_sightings_ui.py -q
```
