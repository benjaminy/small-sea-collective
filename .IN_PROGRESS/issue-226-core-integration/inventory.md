# core.db tables by content class (issue #226)

Schema: `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`, applied by `_init_team_db` (`provisioning.py:3175`).
`device_prekey_bundle` is also created lazily by `_ensure_device_prekey_bundle_table` (`provisioning.py:785`).
All paths below are under `packages/small-sea-manager/small_sea_manager/`.
The schema file defines 19 tables; all 19 are listed.

Classes:
- **signed history**: rows carry a signature over their content and are never changed after insertion.
- **imported shared data**: content another device authors and this device stores as it arrived.
- **local mutable projection**: rows this device computes or overwrites from other rows.
- **unclear**: the code does not settle the class.

"Merge today" means the `splice-sqlite-merge` git driver (`provisioning.py:3190`).
It computes row deltas against the ancestor, keeps ours on every conflict, and applies the rest with foreign keys off.
See "Where the merge runs" at the end.

## Summary

| Table | Signed rows | Mutation | Proposed class |
|---|---|---|---|
| `teammate` | no | inserted, then updated in place | unclear |
| `app` | no | insert only | unclear |
| `team_app_berth` | no | insert only | unclear |
| `berth_role` | no | inserted, updated in place | local mutable projection |
| `invitation` | no | no writer exists | unclear (dead table) |
| `team_setting` | no | overwritten | unclear |
| `admission_proposal` | yes | insert only | signed history |
| `admission_acceptance` | yes | insert only | signed history |
| `endorsement` | yes | insert only | signed history |
| `finalization` | yes | insert only | signed history |
| `admission_revocation` | no | insert only | unclear |
| `team_device` | no | inserted, then updated in place | local mutable projection |
| `key_certificate` | yes | insert only | signed history |
| `teammate_berth_storage_announcement` | yes | insert only | signed history |
| `integration_mode_change` | yes | insert only | signed history |
| `device_prekey_bundle` | inner JSON only | overwritten | unclear |
| `workhorse_delegation` | yes | insert only | signed history |
| `constitution_event` | yes | insert only | signed history |
| `constitution_event_pending` | yes | inserted, then deleted | local mutable projection |

## Per table

### teammate
- Writers: `provisioning.py:1643` (`INSERT OR IGNORE`, in `_upsert_teammate_row`), `provisioning.py:1662` (`UPDATE display_name / identity_public_key`), `constitution_projection.py:155` (`INSERT OR IGNORE` of a bare id).
- Callers of the upsert: `provisioning.py:5542` (create team), `6526` and `6981` (admission; the display name comes from the proposal's unsigned `invitee_label_payload`).
- Unsigned. Mutated in place.
- Class: unclear.
  The row's existence is derived from certificates (`constitution_projection.py:155`), which points to projection.
  `display_name` is not derived from anything signed, so it behaves like shared data that any writer can overwrite.

### app
- Writer: `provisioning.py:5683` (`_ensure_team_app_activation`; callers `5545`, `5745`).
- Unsigned. Insert only. `id` is a random uuid7 and `name` has no UNIQUE constraint.
- Class: unclear.
  Other code assumes one row per name (`_single_app_id_by_name_sa`, `provisioning.py:5610`, raises on duplicates).
- Not core.db: `provisioning.py:5627` and `5638` write `app` and `team_app_berth` in the NoteToSelf database.

### team_app_berth
- Writer: `provisioning.py:5699` (`_ensure_team_app_activation`).
- Unsigned. Insert only. Random uuid7 id.
- Class: unclear.
  Signed rows (`integration_mode_change.berth_id`, `workhorse_delegation.berth_id`, announcements) name berths by this id, but the berth row itself is not signed.

### berth_role
- Writers: `provisioning.py:5720` (initial role at app activation), `4960` (insert) and `4972` (update) in `_project_berth_role`.
- Projection callers: `_append_integration_mode_change` (`4944`), `_reproject_berth_roles` (`4985`, called at `6765` from the admission package import).
  The role follows the newest `integration_mode_change` per teammate and berth.
- Unsigned. Updated in place. Random uuid7 id; no UNIQUE on (teammate_id, berth_id).
- Class: local mutable projection of `integration_mode_change`.
  The `5720` insert seeds a role that no signed record backs, so the projection can start from unsigned data.

### invitation
- Writers: none in the repository. Readers: `admission_events.py:244`, `small_sea_hub/backend.py:1541`.
- Class: unclear.
  Nothing populates it, so it looks left over from before the admission records.
  It is still in the schema and still read.

### team_setting
- Writers: `provisioning.py:919` (`admission_quorum`) and `929` (`proposal_expiry_seconds`), both `INSERT OR REPLACE`.
- A reader expects a `team_id` key (`provisioning.py:3395`). No code writes that key.
- Unsigned. Overwritten.
- Class: unclear.
  The admission quorum is a governance parameter that affects who can be admitted.
  Today it is unsigned and last-writer-wins, unlike the signed records it governs.

### admission_proposal
- Writer: `provisioning.py:5855` (`create_invitation`).
- Signed, append-only. `_verify_proposal_row` re-verifies it before use (`provisioning.py:1201`, called at `6466`).
  `invitee_label_payload` is deliberately outside the signed bytes.
- Class: signed history.

### admission_acceptance
- Writer: `provisioning.py:6470` (`complete_invitation_acceptance`), inserting the invitee's signed record verbatim.
- Signed by the invitee. Append-only.
- Class: signed history.

### endorsement
- Writer: `provisioning.py:1511` (`_append_endorsement`, `INSERT OR IGNORE`).
- Signed. Append-only.
- `UNIQUE (subject_record_id, author_teammate_id)` means a second endorsement by the same teammate is silently ignored locally.
- Class: signed history.

### finalization
- Writer: `provisioning.py:1571` (`_append_finalization`).
- Signed. Append-only.
- Class: signed history.

### admission_revocation
- Writer: `provisioning.py:7753` (`revoke_invitation`, `INSERT OR IGNORE`).
- Unsigned; the schema comment calls it a "local disposition/projection".
  Rows are never deleted, and the row set travels with core.db, so a revocation reaches peers through merge.
- Class: unclear.
  It is stored as local state but replicated like shared data, and the only authentication is the signed git commit.

### team_device
- Writers: `provisioning.py:1682` (`INSERT OR IGNORE`) and `1695` (`UPDATE teammate_id, public_key`) in `_upsert_team_device_row`; `constitution_projection.py:148` (`INSERT OR IGNORE`).
- Callers of the upsert: `provisioning.py:3125`, `5030`, `5553`, `6528`, `6983`.
- Unsigned. Updated in place.
- Class: local mutable projection of `key_certificate` (device link certificates).
  The direct upserts do not go through a certificate, so the projection is not purely derived.

### key_certificate
- Writers: `constitution_projection.py:136` (from a `key_certificate` constitution event), `provisioning.py:3285` (`INSERT OR IGNORE`, called from `finalize_linked_device_bootstrap`, `3116`), `provisioning.py:3244` (`_store_team_certificate`; no caller found).
- Signed. Append-only; a conflicting row with the same `cert_id` is refused (`constitution_projection.py:134`).
- Class: signed history.

### teammate_berth_storage_announcement
- Writers: `provisioning.py:3702` (`_insert_teammate_berth_storage_announcement`, signs locally), `provisioning.py:6310` (imports a signed route sidecar after checking signer, teammate, berth and signature).
- Signed. Append-only. The newest announcement wins at read time (`selected_teammate_berth_storage_announcement`), so replacing a route means adding a row.
- Class: signed history.

### integration_mode_change
- Writer: `constitution_projection.py:251`, reached from `record_integration_mode_change` and from `apply_event`.
- Signed. Append-only. Also present as a `constitution_event`.
- Class: signed history.

### device_prekey_bundle
- Writer: `provisioning.py:827` (`INSERT OR REPLACE`; caller `_publish_local_device_prekey_bundle`, `1750` and `1763`).
- The stored JSON contains a signed bundle, but the row is replaced, and `published_at` is outside the signature.
- Class: unclear.
  It looks like imported shared data with latest-wins semantics, but nothing states that.

### workhorse_delegation
- Writer: `constitution_projection.py:201`, from a `workhorse_delegation` constitution event.
- Signed. Append-only, "no revocation yet" (schema comment).
- Class: signed history.

### constitution_event
- Writers: `constitution_store.py:51` (promotion of a pending event) and `96` (direct store), both through `add_event`.
- Signed, grow-only. A different encoding under an existing id raises `EventConflictError`.
- Class: signed history.
  `integrate_core_events` (`provisioning.py:6784`) unions this table from parked peer commits.

### constitution_event_pending
- Writers: `constitution_store.py:87` (insert when a parent is missing) and `57` (delete on promotion).
- Rows are signed events, but the table is a waiting room: rows leave when their parents arrive.
- Class: local mutable projection.

## Not in core.db
`admission_event_disposition`, `admission_event_store_meta`, `participant_app_sighting_disposition`, `team_app_sighting_disposition` (`provisioning.py:7931`, `7945`, `8138`, `8153`) and every `local.*` table live in the device-local database.
The merge never sees them.

## Where the merge runs
The git driver is installed for every team Core repo (`provisioning.py:2694`, `5589`, `5999`).
No non-test code calls `Repo.merge`, and the Manager describes incoming Core state as "fetch and park, never integrate" (`manager.py:1655`).
The only peer-to-core.db path in production is `integrate_core_events` (`provisioning.py:6784`), which unions signed `constitution_event` rows and projects them.
Tables outside that path (`teammate`, `app`, `team_app_berth`, `team_setting`, the four admission tables, `admission_revocation`, `device_prekey_bundle`, `invitation`) have no production route for peer changes.
The driver still runs when someone merges a Core repo by hand, and `tests/test_merge_conflict.py` exercises it.
`tests/test_core_merge_characterization.py` records what the driver does to each table.
