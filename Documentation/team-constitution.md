# The Team Constitution

Status: design document.
The signed, append-only governance model this document describes is committed to in [`architecture.md`](../architecture.md#no-team-server) and its `FOLLOW-UP.md` sequencing note from the permission-vocabulary branch.
This document is the field-level schema that turns that commitment into something implementable.
It is not exploratory the way [`linked-teams.md`](linked-teams.md) is — it is the foundation the FOLLOW-UP note calls out as blocking mode-aware replication, prepared recovery, and retention/staleness work.
It does not yet exist as code; no table here is live.

## Core vs. the Constitution

**Core** is the berth — `{Team}/SmallSeaCollectiveCore` — and the SQLite database that lives there.
The **Team Constitution** is the signed, append-only governance lineage that database carries: who is a teammate, which devices speak for them, their per-berth integration mode, and the handful of other facts whose history must remain inspectable rather than merely current.

Mutable tables (`teammate`, `berth_role`, `invitation`, `team_device`) are not part of the Constitution.
They are projections: deterministically rebuildable caches of "what does the Constitution currently say," kept around because rebuilding from scratch on every read would be wasteful, not because they are themselves durable history.

## Why admission is the one quorum-gated action

Every Constitution record is signed by a device belonging to an already-recognized teammate — except one.
Admission is establishing an identity that does not yet have standing to sign for itself, so nothing about admission can be self-attested the way "I am revoking my own device" can be.
That is the entire reason admission carries a transcript-bound, quorum-gated endorsement flow while everything else in this schema is a single-signer record, valid the moment a currently-recognized authority signs it.

This split — one quorum-gated action, everything else single-signer — is a schema decision worth naming explicitly, because it would be easy to over-generalize a proposal/endorsement/quorum mechanism onto every record type.
The actual invariant ("the endorsement threshold is always at least one automatic integrator") is already satisfied by "the signer currently holds standing" for every action except admission.

## The shared envelope

Every Constitution record type shares one column prefix, produced by one shared signing helper instead of the three independent reimplementations of the same idiom found in `key_certificate`, `teammate_berth_storage_announcement`, and the admission-proposal code today (see `NOTES.md` in this branch's working-docs folder for the survey).
Concretely:

| Column | Type | Meaning |
|---|---|---|
| `record_id` | `BLOB PRIMARY KEY` | `sha256(canonical_bytes)[:16]` — content-derived, matching the existing `cert_id` convention |
| `record_type` | `TEXT NOT NULL` | discriminator, useful for logging/tooling even though each type also has its own table |
| `author_teammate_id` | `BLOB NOT NULL` | the teammate this record speaks for |
| `author_device_key_id` | `BLOB NOT NULL` | the specific device key that signed |
| `created_at` | `TEXT NOT NULL` | ISO8601, for display and debugging only — never consulted to decide validity or ordering |
| `anchor_commit` | `TEXT` | git commit hash of the Core repo this record was authored against; `NULL` only for the genesis record |
| `anchor_digest` | `BLOB` | `sha256(canonical_json(constitution_skeleton_at(anchor_commit)))`; `NULL` only for the genesis record |
| `schema_version` | `INTEGER NOT NULL DEFAULT 1` | envelope/record-type format version — distinct from the whole-database `USER_SCHEMA_VERSION` |
| `signature` | `BLOB NOT NULL` | Ed25519 signature by `author_device_key_id` over the canonical bytes of every other column plus the type-specific columns below |

Canonical bytes: the same idiom already in use, generalized.
Build a dict of every envelope column (except `record_id` and `signature`) plus every type-specific column, hex-encode binary fields, and serialize with `json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")`.
`record_id` is derived from those bytes before signing; `signature` is computed over those same bytes and excluded from them, exactly like `key_certificate.cert_id` today.

`key_certificate` and `teammate_berth_storage_announcement` predate this envelope and do not carry `anchor_commit`/`anchor_digest`/`schema_version` today.
Bringing them onto the shared envelope (adding those columns, keeping their existing fields) is part of the implementation work this document unblocks — it is not silently assumed to already be true.

## The Constitution anchor

`anchor_commit` and `anchor_digest` generalize the pair `admission_proposal` already uses today (`anchor_commit` + `governance_digest`), which only covers the admins/teammates/teammate-devices snapshot needed for admission.
The Constitution version widens the digest to cover every record type's current effect — teammate standing, device sets, per-berth integration modes, exclusion state, registered recovery public keys — while deliberately excluding personal payloads, per the "governance skeleton" distinction `architecture.md` already draws under *Personal Data Is Not in the Long-Term Chain*.
A display-name claim's *commitment* is part of the skeleton; the name itself is not, so verifying an anchor never requires reading anyone's personal data.

```
constitution_skeleton_at(commit) -> {
    teammates: [{teammate_id, admitted_at_record, excluded: bool}],
    devices:   [{teammate_id, device_key_id, linked_at_record, revoked: bool}],
    modes:     [{teammate_id, berth_id, mode, changed_at_record}],
    recovery_keys: [{teammate_id, recovery_public_key, registered_at_record}],
}
constitution_digest_at(commit) = sha256(canonical_json(constitution_skeleton_at(commit)))
```

Any verifier recomputes this independently by replaying every Constitution table up to `commit`; two honest verifiers with the same records reach the same digest.
A record whose `anchor_digest` does not match a fresh replay at its own `anchor_commit` is malformed, not merely stale — staleness is a proposal whose anchor no longer matches the *current* head, which is a different, expected condition (see `admission_proposal`'s existing freshness check).

## Record catalog

### Reused as-is

- **`key_certificate`** (`self_binding` [legacy], `membership`, `device_link`, `cross_certification`, `revocation`) — already signed, typed, and append-only.
  `device_link` and `revocation` are how a device is linked or revoked; no new table needed.
  The genesis, self-issued `membership` record is the Constitution's root.
- **`teammate_berth_storage_announcement`** — already signed and append-only, selected by descending UUIDv7 `announcement_id`, never by wall-clock time.
  Not governance-bearing in the anchor sense above (it doesn't need `anchor_commit`); it announces where a teammate's own data lives, not a fact about team standing.

### Generalized: the one quorum-gated flow

- **`admission_proposal`** — the existing table (see `NOTES.md`), moved onto the shared envelope.
  Type-specific columns: `nonce`, `invitee_teammate_id` (freshly allocated), `invitee_label` (personal data — see PII handling below), `invitee_device_public_key`, `expires_at`.
  Drops `role`: admission no longer carries an integration-mode preset directly (see `integration_mode_change` below).
  The Manager UI still offers an `admin`/`contributor`/`observer` *preset* at invitation time, but it is realized as a set of `integration_mode_change` records appended alongside finalization, not a field on the proposal itself.
  This keeps "who is admitted" and "what mode do they start in" as separately inspectable facts.
- **`admission_acceptance`** — new, replacing the mutated acceptance columns on today's `admission_proposal` row.
  References `proposal_id`, carries the invitee's signed acceptance blob.
  Append-only: an invitee accepting is its own record, not an update to the proposal row.
- **`endorsement`** — generalized from `admin_approval`.
  References `subject_record_id` (today, an admission proposal's `transcript_digest`; the type is written generically so a future higher-stakes action can reuse it if the team ever configures a threshold above one for it).
  Deduplicates by `endorsing_teammate_id`, not by device — fixing the gap the survey found in today's `UNIQUE(proposal_id, approver_device_key_id)`, which dedupes by device and would currently double-count two devices of the same endorser.
- **`finalization`** — new, small: references `subject_record_id`, records that the required endorsement count was observed and the subject is now effective.
  Only finalization "turns on" a proposal; an unfinalized proposal, however many endorsements it has, has no effect.

### New: single-signer governance records

Each of these is valid immediately when signed by the appropriate currently-recognized authority at its `anchor_commit` — no endorsement round.

- **`integration_mode_change`** — `teammate_id`, `berth_id`, `mode` (`automatic` | `proposal-only`, the new vocabulary directly — this is a brand-new table with nothing to stay compatible with).
  Valid when signed by a current automatic integrator on that berth (or Core, for berths where Core itself gates mode changes).
- **`exclusion`** — `excluded_teammate_id`, `reason` (personal data — separable payload).
  Valid when signed by a current automatic Core integrator.
  Matches the Manager spec's existing description of "remove teammate" as a unilateral, socially-adopted-or-not act.
- **`prepared_recovery_registration`** — `teammate_id`, `recovery_public_key`.
  Self-registered: signed by an existing device of the same teammate, publishing the public half of a recovery capability prepared and stored outside routine sync.
  Does not itself authorize anything; see next.
  Not yet designed: the private-side format, storage, and rotation — tracked in `Documentation/open-architecture-questions.md`, not settled here.
- **`recovery_event`** — `teammate_id`, `new_device_public_key`, references the `prepared_recovery_registration` whose key it is signed by.
  Its signature is verified against the *registered recovery public key*, not against "a current automatic integrator" — a distinct authority class from ordinary device signing.
  Anti-replay/rollback fields are an open slot, not a settled design: this record type exists so downstream code has something to target, but its full ceremony (nonce scheme, expiry, single-use enforcement) is explicitly future work, cross-referenced in `open-architecture-questions.md`.
- **`display_name_claim`** — `teammate_id`, `name_commitment` (the durable, hiding commitment), `payload` (the separable, droppable personal content — may be absent).
  Self-signed.
  The commitment scheme itself (salting, hash construction) is not chosen here; it needs the cryptographic analysis `open-architecture-questions.md` already tracks.
  What *is* fixed now: the signature covers the commitment only, never the raw payload, so the payload can be dropped or encryption-windowed later without invalidating the record or any replay that depends on it.
- **`teammate_unification_claim`** — comes as a linked pair of records rather than one multi-signature record: one half signed by a device of the first candidate UUID, the other half signed by a device of the second, each referencing the other's `record_id`.
  Unification is only in effect once both halves exist, which is a simple existence check rather than a new co-signature envelope shape.
  `evidence` (personal data — separable payload) lives per-half.
- **`staleness_observation`** — `observing_teammate_id` (= `author_teammate_id`), `observed_teammate_id`, `observed_berth_id`, `last_observed_signal`, `local_update_counter_or_elapsed`, `warning_horizon`.
  Self-signed testimony.
  Explicitly not authoritative: it cannot exclude anyone, advance anyone's retention horizon, or declare finality — see `architecture.md`'s *Retention Horizons and Staleness*.
  Different observers may disagree; that is not a malformed state.

## PII handling: the general shape

Several record types above (`display_name_claim`, `teammate_unification_claim`'s evidence, `exclusion`'s reason) carry personal content that must not be permanent chain data, per `architecture.md`'s *Personal Data Is Not in the Long-Term Chain*.
The schema-level shape is the same in each case:

- a durable `*_commitment` (or the record simply omitting personal fields from what feeds `constitution_skeleton_at`) that the signature covers
- a separable `payload` column that may be null, encrypted-to-a-window, or physically deleted later without touching the signature or breaking replay

This document fixes that shape.
It does not fix the commitment construction — that is a distinct, tracked, cryptography-review item, not a schema question.

## Replay and projections

```
def constitution_skeleton_at(conn, commit: str | None = None) -> ConstitutionSkeleton: ...
def constitution_projection(conn, commit: str | None = None) -> ConstitutionProjection: ...
```

`constitution_skeleton_at` is the governance-only replay feeding anchor digests (above).
`constitution_projection` is the fuller rebuild that also populates the mutable convenience tables: `teammate`, `berth_role`, `invitation`, `team_device`.
Both are deterministic functions of the Constitution's record tables and nothing else — no wall-clock reasoning, no reliance on row-arrival order.
Manager write paths for "set integration mode," "remove teammate," "revoke device," and so on become "append the appropriate Constitution record, then rebuild the affected projections," replacing today's direct mutation of `berth_role`/`teammate`/`team_device` rows.

Because Cod Sync already ships the whole Core SQLite file on every sync (per `architecture.md`'s retention section), "every Core snapshot contains the complete signed history through that snapshot" is automatically true once the Constitution's tables exist and are populated — there is no separate index or log table to keep in sync.

## Schema-change mechanics

No migration system exists in this codebase, and this is a pre-alpha project that deliberately avoids compatibility shims.
Landing this schema in code means new `CREATE TABLE IF NOT EXISTS` statements in `core_other_team.sql`, a bump to `USER_SCHEMA_VERSION`, and the projection rebuild replacing direct mutation in the affected Manager operations — not a migration path from the current mutable rows.
Any existing team database created before that point is pre-alpha data; per the project's own stance, it is expected to be deleted and recreated, not migrated.

## Deliberately left open

- The PII commitment scheme (`Documentation/open-architecture-questions.md`, Section on personal data)
- The recovery ceremony's anti-replay/rollback mechanics (`open-architecture-questions.md`, Section 5)
- The staleness-to-checkpoint rule (`architecture.md`'s *Retention Horizons and Staleness*; `open-architecture-questions.md`, Section 4)
- Whether any action beyond admission ever needs a configurable endorsement threshold above one, and if so whether it reuses `endorsement`/`finalization` as written here or needs its own shape
- Aligning `key_certificate` and `teammate_berth_storage_announcement` onto the shared envelope (adding `anchor_commit`/`anchor_digest`/`schema_version`) is implementation work this document motivates but does not schedule
