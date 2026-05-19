# Follow-Up

- Consider a future hardening micro test for team-device rotation:
  a matching storage announcement signed by a prior local device key that is no
  longer the current `team_device_key` must not satisfy the own-storage
  bootstrap allowance.
- Deduplicate `_publish_storage_announcement_for_session` test fixture helpers.
  The helper now appears in several Hub, Manager, and Vault tests; a shared
  helper would make the next storage-announcement behavior change less tedious.
