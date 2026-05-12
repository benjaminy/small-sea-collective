# Follow-Up

- Offline local CLI and web commands currently need a cached Hub session to be
  reachable if they must resolve a friendly `team_name` to Vault `team_id`.
  A future local resolver could use Vault `metadata.json` as an index, with
  explicit handling for duplicate friendly team names.
