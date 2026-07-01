# Survey Notes

Grounding survey of code the Team Constitution schema is meant to reuse or generalize, per
`FOLLOW-UP.md` from the permission-vocab branch: "Reuse the canonical-signing patterns already
established by `key_certificate` and `teammate_berth_storage_announcement` rather than creating a
parallel notion of authorship."

## Existing canonical-signing idiom (duplicated three times, no shared helper)

Every existing signed-record type independently reimplements the same pattern:

- `packages/wrasse-trust/wrasse_trust/identity.py:95-116` — `_canonical_cert_bytes` for `key_certificate`
- `packages/wrasse-trust/wrasse_trust/transport.py:84-116` — two `canonical_*_bytes` functions for `teammate_berth_storage_announcement`
- `packages/small-sea-note-to-self/small_sea_note_to_self/bootstrap.py:39-48` — `_canonical_json` and friends
- `packages/small-sea-manager/small_sea_manager/provisioning.py` — private `_json_bytes`/`_sha256_bytes` helpers used by the admission-proposal flow

The idiom: build a dict with binary fields hex-encoded, serialize with
`json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")`, sign everything except the
`signature` field itself. `key_certificate`'s `cert_id` is `sha256(canonical)[:16]` — a content-derived
ID, not a random one.

Signing is Ed25519 via `cryptography.hazmat.primitives.asymmetric.ed25519` (not PyNaCl). Keys are
stored as raw unencrypted bytes under a `FakeEnclave` directory — an explicit, named placeholder for
a future secure enclave, not something to design around.

**Decision for the new schema:** factor this duplicated idiom into one shared envelope/helper instead
of writing a fourth copy. This directly satisfies the follow-up note's instruction and fixes a small
existing inconsistency (each file's field-ordering/hex-encoding choices are subtly different from the
others').

## The anchor mechanism already exists, in miniature, for admission only

`packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql:49-84` (current, authoritative —
`spec.md`'s schema-TBD block for B5 is stale prose):

```sql
CREATE TABLE IF NOT EXISTS admission_proposal (
    proposal_id BLOB PRIMARY KEY, nonce BLOB NOT NULL, team_id BLOB NOT NULL,
    inviter_teammate_id BLOB NOT NULL, invitee_teammate_id BLOB NOT NULL,
    invitee_label TEXT, role TEXT NOT NULL DEFAULT 'admin',
    anchor_commit TEXT NOT NULL, governance_digest BLOB NOT NULL,
    governance_snapshot_json TEXT NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    acceptance_recorded_at TEXT, invitee_device_public_key BLOB,
    invitee_bootstrap_key BLOB, acceptance_signature BLOB,
    transcript_digest BLOB, transcript_json TEXT,
    finalized_at TEXT, finalization_signature BLOB, invalid_reason TEXT
);
CREATE TABLE IF NOT EXISTS admin_approval (
    approval_id BLOB PRIMARY KEY, proposal_id BLOB NOT NULL,
    admin_teammate_id BLOB NOT NULL, approver_device_key_id BLOB NOT NULL,
    transcript_digest BLOB NOT NULL, signature BLOB NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (proposal_id, approver_device_key_id),
    FOREIGN KEY (proposal_id) REFERENCES admission_proposal(proposal_id) ON DELETE CASCADE
);
```

`anchor_commit` is a real git commit hash of the team sync repo (`_team_head_commit()`,
`provisioning.py:806-810`). `governance_digest` is `sha256(canonical_json(governance_snapshot))` where
the snapshot is admins/teammates/teammate_devices as of that commit (`_governance_snapshot`,
`provisioning.py:925-953`). Freshness is checked by recomputing the digest and comparing
(`provisioning.py:994`).

**Decision:** this is exactly the anchor concept `architecture.md` describes ("replayable relative to
an anchor") — just scoped only to admission today. Generalize it into one `constitution_anchor`
(commit + digest over the *full* replayable projection, not just admins/teammates/devices) that every
governance-bearing record type references, instead of inventing a separate anchor notion per type.

**Known existing gap to fix when this ships in code (not in this design doc):**
`admin_approval` dedupes `UNIQUE(proposal_id, approver_device_key_id)` — i.e. by *device*, not by
teammate. `FOLLOW-UP.md` and `architecture.md` both say multiple devices of the same endorser should
dedupe to one. Current code doesn't actually do that yet. Flagged, not fixed here.

## Other existing record families to reuse as-is

- `key_certificate` (`identity.py:63-76`, `SUPPORTED_CERT_TYPES`: `self_binding`, `cross_certification`,
  `membership`, `device_link`) — already a signed, typed, append-only record family. `device_link` and
  the genesis `membership` cert map directly onto Constitution record types; no need to reinvent them.
- `teammate_berth_storage_announcement` (`transport.py:24-34`) — already signed and append-only,
  selected by descending UUIDv7 `announcement_id`, never by `announced_at`. Reuse as-is.

## Schema-change mechanics

No migration system exists (no `migrations/` dir, no Alembic). Versioning is one constant,
`USER_SCHEMA_VERSION` (`provisioning.py:1645`), applied via `CREATE TABLE IF NOT EXISTS`. Both
`_migrate_user_db` and `_migrate_team_db` explicitly `raise NotImplementedError` with a comment that
pre-alpha migrations aren't supported — delete and recreate instead. A newer-than-expected DB raises
`FutureTeamDatabaseVersionError` rather than silently proceeding.

**Implication:** new Constitution tables are just new `CREATE TABLE IF NOT EXISTS` statements plus a
version bump, whenever this becomes a code change. No compatibility shim to design.
