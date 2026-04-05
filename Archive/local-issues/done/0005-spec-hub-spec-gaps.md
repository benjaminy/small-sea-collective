> Migrated to GitHub issue #29.

---
id: 0005
title: Fill in Hub spec gaps
type: spec
priority: medium
status: closed
---

## Context

`packages/small-sea-hub/spec.md` has nine TODO placeholders where sections haven't been written yet. These block collaborators from understanding how the Hub works.

## Work to do

The following items are **ready to document** — the implementation is complete and well-understood (clarified by the work in issues 0001–0003):

- **Session lifecycle** (line 30): Two-step flow — `POST /sessions/request` (returns `pending_id`,
  sends OS notification with 4-digit PIN, 5-min TTL) → `POST /sessions/confirm` (validates PIN,
  returns 32-byte Bearer token). Sessions have no expiry (`duration_sec = NULL`). `backend.py`
  `request_session` / `confirm_session` are the canonical reference.
- **HTTP API** (line 69): `POST /sessions/request`, `POST /sessions/confirm`, `POST /cloud_file`,
  `GET /cloud_file`, `POST /notifications`, `GET /notifications`. All cloud/notification endpoints
  require `Authorization: Bearer {token_hex}`. Upload accepts optional `expected_etag` for CAS;
  responds 409 on conflict. See `server.py`.
- **Shared database contract** (line 37): Hub reads from Manager-written DBs at
  `Participants/{hex}/NoteToSelf/Sync/core.db` (tables: `nickname`, `team`, `app`,
  `team_app_berth`, `cloud_storage`, `notification_service`) and
  `Participants/{hex}/{team_name}/Sync/core.db` (tables: `app`, `team_app_berth`) for non-NoteToSelf
  teams. Hub writes only to its own local DB. See `backend.py` comment: "duplicated in team manager —
  the DB is the contract".
- **Local database schema and on-disk directory layout** (line 75): Hub local DB is at
  `{root_dir}/small_sea_collective_local.db`; schema in `sql/hub_local_schema.sql` (tables:
  `session`, `pending_session`). Logs at `{root_dir}/Logging/small_sea_hub.log`.
- **Cloud storage adapters** (line 46): S3 (boto3, signature v4, bucket derived as
  `ss-{berth_id[:16]}`), Google Drive (OAuth2, token refresh), Dropbox (OAuth2, token refresh).
  Credentials in NoteToSelf `cloud_storage` table.
- **Notifications** (line 57): The spec says "not yet implemented" — this is wrong. ntfy is
  implemented. Topic derived as `ss-{sha256(team/app)[:16]}`. `POST /notifications` publishes;
  `GET /notifications` long-polls (`since`, `timeout` params).

The following items describe **unimplemented features** and should be marked as planned/speculative:

- Credential storage evolution (keyring/vault) (line 53)
- VPN/P2P (line 61)
- Encryption layer (line 65)

## References

- `packages/small-sea-hub/spec.md`
- `packages/small-sea-hub/small_sea_hub/backend.py` — canonical implementation
- `packages/small-sea-hub/small_sea_hub/server.py` — HTTP API
- `packages/small-sea-hub/small_sea_hub/sql/hub_local_schema.sql` — Hub local DB schema
- `Documentation/open-architecture-questions.md` — settled decisions that can inform some sections
