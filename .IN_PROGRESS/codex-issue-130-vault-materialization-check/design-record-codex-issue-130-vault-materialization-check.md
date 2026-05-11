# Design Record: Vault Materialization Coordinates

Vault now has an explicit `VaultMaterializationContext`.
Session-backed flows construct it from Hub `/session/info` and use
`participant_hex` plus Vault `team_id` as the durable local materialization
coordinate.
The Vault `team_id` is sourced from Hub `session_info["berth_id"]`.

Friendly `team_name` remains display and pre-session selection data.
Vault writes it to local team metadata so UI listing can keep showing a
friendly name, but directory paths and SQLite lookup keys use `team_id`.

The local layout is now:

```text
{vault_root}/participants/{participant_hex}/teams/{team_id}/...
```

`checkouts.db` keys checkout rows, peer sync rows, and peer signal watermarks
by `team_id`.
Because this repo is pre-alpha, stale local SQLite state is recreated on schema
version mismatch instead of migrated.

Hub-backed cloud object keys now live within the Hub-provided session storage
boundary.
Vault uses `registry/` and `niches/{niche_name}/`; it does not add
`team_name` or Vault `team_id` to those object prefixes.
The implementation relies on the Hub session and backend scoping verified in
the issue plan.

The web layer now resolves an active cached session into the same materialization
context before calling Vault.
No-session local web and CLI flows still use an explicit local fallback context,
where the friendly label is also the local team ID.
