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

Two different properties are easy to conflate here, so this document keeps them separate.

**Who is allowed to sign at all.** Every Constitution record is signed by a key a verifier can already check has standing — ordinarily an already-recognized teammate's device.
Exactly one record type breaks that: `admission_acceptance` (see below), signed by the invitee's brand-new device before any `device_link` exists for it, and therefore verified against a public key the record itself supplies rather than looked up in the device graph.
`admission_proposal` is *not* an exception on this axis — it is signed by the inviter, who is already recognized.

**How many signers are required.** Every record type is valid the moment one currently-recognized authority signs it, except `admission_proposal`, whose finalization requires a quorum of automatic Core integrators' endorsements.
This is a separate, tunable design choice, not a consequence of the invitee being unable to self-attest: a single recognized inviter's signature would already satisfy the "who is allowed to sign" concern above, which is exactly why `quorum = 1` (one recognized signer, no additional endorsers) is a valid and default configuration.
Requiring *more* than one endorser is optional hardening a team can choose for a high-stakes action, independent of who is doing the signing.

So there are two, independent, single-exception rules — not one action that is exceptional on both axes at once.
This is worth naming explicitly because it would be easy to over-generalize a proposal/endorsement/quorum mechanism onto every record type when the actual invariant ("the endorsement threshold is always at least one automatic integrator") is already satisfied by "one currently-recognized signer" for every action except admission's finalization.

## The shared envelope

Every Constitution record type shares one column prefix, produced by one shared signing helper instead of the three independent reimplementations of the same idiom found in `key_certificate`, `teammate_berth_storage_announcement`, and the admission-proposal code today (see `Archive/design-record-team-constitution-schema.md` for the survey).
Concretely:

| Column | Type | Meaning |
|---|---|---|
| `record_id` | `BLOB PRIMARY KEY` | `sha256(canonical_bytes)[:16]` — content-derived, matching the existing `cert_id` convention |
| `record_type` | `TEXT NOT NULL` | discriminator, useful for logging/tooling even though each type also has its own table |
| `author_teammate_id` | `BLOB NOT NULL` | the teammate this record speaks for |
| `author_device_key_id` | `BLOB NOT NULL` | the specific device key that signed |
| `created_at` | `TEXT NOT NULL` | ISO8601, for display and debugging only — never consulted to decide validity or ordering |
| `anchor_commit` | `TEXT` | git commit hash near authoring time — **informational only**, not what verification relies on; see *The Constitution anchor* below; `NULL` for the genesis record and for non-governance-bearing types |
| `anchor_frontier` | `TEXT` | canonical JSON reference to prior Constitution records — **authoritative**; see below; `NULL` for the genesis record and for non-governance-bearing types |
| `schema_version` | `INTEGER NOT NULL DEFAULT 1` | envelope/record-type format version — distinct from the whole-database `USER_SCHEMA_VERSION` |
| `signature` | `BLOB NOT NULL` | Ed25519 signature by `author_device_key_id` over the canonical bytes of every other envelope column plus each type's *signed* type-specific columns |

Not every type-specific column is signed.
A record type may declare one or more of its type-specific columns as **separable payload**: droppable, encryptable-to-a-window, or excisable personal content that must never be required to verify the record's signature or replay its governance effect.
Separable-payload columns are excluded from the canonical signing bytes entirely.
Only a paired `*_commitment` column — itself an ordinary *signed* type-specific column — stands in for that content in what actually gets signed.
This is the mechanism the *PII handling* section below depends on, not a separate rule layered on top of it; every PII-bearing type in the catalog below is written as a `*_commitment` (signed) plus a `*_payload` (separable) pair, never as one plain column.

Canonical bytes: the same idiom already in use, generalized.
Build a dict of every envelope column except `record_id` and `signature`, plus every type's *signed* type-specific columns — separable-payload columns are never part of this dict — hex-encode binary fields, and serialize with `json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")`.
`record_id` is derived from those bytes before signing; `signature` is computed over those same bytes and excluded from them, exactly like `key_certificate.cert_id` today.

`key_certificate` and `teammate_berth_storage_announcement` predate this envelope.
Bringing them onto it means adopting the shared `record_id`/`record_type`/`schema_version` columns and signing helper for consistency of tooling.
It does not require adopting `anchor_commit`/`anchor_frontier` — those stay `NULL` for `teammate_berth_storage_announcement`, which is not governance-bearing (see *Reused as-is* below).
This is part of the implementation work this document unblocks; it is not silently assumed to already be true.

## The Constitution anchor

Today's `admission_proposal` anchors to `anchor_commit` (a git commit hash) plus `governance_digest` (a hash of a live SQL query against the *current* connection at proposal-creation time).
That pairing is admission-specific and, generalized naively, has a real problem: `architecture.md` already states that Git commit authorship is not the authority for significant teammate facts, and Core's retention section promises only a *conservative* live-data window, not infinite blob retention — so an old `anchor_commit` is not guaranteed to be checkable out forever.
Meanwhile every Constitution record is retained forever in the live database by design (nothing is ever deleted), which means replay should never need to depend on git blob availability at all.

So the Constitution's anchor is record-based, not commit-based:

- `anchor_commit` is kept only as an informational git-commit reference for humans and debugging (roughly "authored around this point in the repo's history"). It is never consulted to decide validity.
- `anchor_frontier` is the authoritative reference: for each governance-bearing record type the author had observed, the `record_id` of the latest record in that type's own table at authoring time (or `null` if none yet exists). Each governance-bearing table's rows additionally carry a `predecessor_record_id` (nullable, self-referential within that same table), so a tip pointer plus predecessor-chain-following fully determines that table's history up to the anchor without consulting wall-clock order, arrival order, or any other table.

```
constitution_skeleton_at(frontier: dict[table_name, record_id | None]) -> {
    teammates: [{teammate_id, admitted_at_record, excluded: bool}],
    devices:   [{teammate_id, device_key_id, linked_at_record, revoked: bool}],
    modes:     [{teammate_id, berth_id, mode, changed_at_record}],
    recovery_keys: [{teammate_id, recovery_public_key, registered_at_record}],
}
constitution_digest_at(frontier) = sha256(canonical_json(constitution_skeleton_at(frontier)))
```

Any verifier recomputes `constitution_skeleton_at` independently by chain-following each table back from its `anchor_frontier` tip, using only records already present in their own local Constitution tables — never a git checkout, never the current live head unless that happens to be what the frontier names.
Two honest verifiers holding the same records reach the same skeleton for the same frontier.

The exact wire representation of `anchor_frontier` (one tip per table as sketched above, versus a single rolling accumulator digest, versus a Merkle structure) is not settled by this document — the requirement fixed here is only that it be reconstructible from record-to-record references alone.
See *Deliberately left open* below.

A record whose declared frontier cannot be chain-followed to a consistent skeleton in the verifier's own tables is malformed.
A record whose frontier is merely no longer the *current* tip (newer records have since been appended) is stale, not malformed — that is the same distinction `admission_proposal`'s existing freshness check already draws, just evaluated over the record-based frontier instead of a live SQL query.

## Record catalog

### Reused as-is

- **`key_certificate`** (`self_binding` [legacy], `membership`, `device_link`, `cross_certification`, `revocation`) — already signed, typed, and append-only.
  `device_link` and `revocation` are how a device is linked or revoked; no new table needed.
  The genesis, self-issued `membership` record is the Constitution's root.
- **`teammate_berth_storage_announcement`** — already signed and append-only, selected by descending UUIDv7 `announcement_id`, never by wall-clock time.
  Joins the shared envelope's `record_id`/`record_type`/`schema_version`/signing helper for consistency, but leaves `anchor_commit`/`anchor_frontier` `NULL`: it announces where a teammate's own data lives, not a fact about team standing, so it is not governance-bearing and nothing needs to replay a Constitution skeleton to validate it.

### Generalized: the one quorum-gated flow

- **`admission_proposal`** — the existing table (see `Archive/design-record-team-constitution-schema.md`), moved onto the shared envelope.
  Type-specific columns: `nonce`, `invitee_teammate_id` (freshly allocated), `invitee_label_commitment` (signed) and `invitee_label_payload` (separable — see *PII handling* below), `invitee_device_public_key`, `expires_at`.
  Drops `role`: admission no longer carries an integration-mode preset directly (see `integration_mode_change` below).
  The Manager UI still offers an `admin`/`contributor`/`observer` *preset* at invitation time, but it is realized as a set of `integration_mode_change` records appended alongside finalization, not a field on the proposal itself.
  This keeps "who is admitted" and "what mode do they start in" as separately inspectable facts.
- **`admission_acceptance`** — new, replacing the mutated acceptance columns on today's `admission_proposal` row.
  References `subject_record_id` (= `admission_proposal.record_id`), the same FK column name and target `endorsement` and `finalization` use below, so all three types that reference a proposal do so identically rather than three different ways.
  Carries the invitee's signed acceptance blob.
  Append-only: an invitee accepting is its own record, not an update to the proposal row.
  **This is the schema's one exception to "the signer already has standing"** (see *Why admission is the one quorum-gated action* above). The invitee's `teammate_id` was pre-allocated by the inviter and already exists, but the invitee's *device* has no `device_link` yet, so the usual verification rule (look up `author_device_key_id` in the device graph, confirm it resolves to `author_teammate_id`) does not apply. `admission_acceptance` is instead self-certifying: it carries `invitee_device_public_key` directly, and the record's signature is verified against that embedded key, not against a prior device-link cert. That key only becomes an ordinary recognized device once `finalization` succeeds.
- **`endorsement`** — generalized from `admin_approval`.
  References two distinct things, kept separate rather than conflated: `subject_record_id` (the FK — the `record_id` of the proposal being endorsed, e.g. `admission_proposal.record_id`) and `subject_digest` (a content-commitment over the exact reviewed payload, generalizing today's `transcript_digest`, which is computed independently of `proposal_id` and guards against the proposal's content changing out from under an endorser between review and use).
  If a proposal's payload needs to change after endorsements exist, that is a new `proposal_revision` record referencing the original `subject_record_id` with a fresh `subject_digest` — not a mutation of the original and not something existing endorsements silently carry over. (`proposal_revision` is named here as the concept `architecture.md` already commits to; its exact columns are not fully specified in this pass.)
  Deduplicates by `endorsing_teammate_id`, not by device — fixing the gap the survey found in today's `UNIQUE(proposal_id, approver_device_key_id)`, which dedupes by device and would currently double-count two devices of the same endorser.
  The type is written generically so a future higher-stakes action can reuse it if the team ever configures a threshold above one for it.
- **`finalization`** — new, small: references `subject_record_id`, records that the required endorsement count was observed and the subject is now effective.
  Only finalization "turns on" a proposal; an unfinalized proposal, however many endorsements it has, has no effect.

### New: single-signer governance records

Each of these is valid immediately when signed by the appropriate currently-recognized authority at its `anchor_frontier` — no endorsement round.

- **`integration_mode_change`** — `teammate_id`, `berth_id`, `mode` (`automatic` | `proposal-only`, the new vocabulary directly — this is a brand-new table with nothing to stay compatible with).
  Valid when signed by a current automatic integrator on that berth (or Core, for berths where Core itself gates mode changes).
- **`exclusion`** — `excluded_teammate_id`, `reason_commitment` (signed), `reason_payload` (separable — see *PII handling* below).
  Valid when signed by a current automatic Core integrator.
  Matches the Manager spec's existing description of "remove teammate" as a unilateral, socially-adopted-or-not act.
- **`prepared_recovery_registration`** — `teammate_id`, `recovery_public_key`.
  Self-registered: signed by an existing device of the same teammate, publishing the public half of a recovery capability prepared and stored outside routine sync.
  Does not itself authorize anything; see next.
  Not yet designed: the private-side format, storage, and rotation — tracked in `Documentation/open-architecture-questions.md`, not settled here.
- **`recovery_event`** — `teammate_id`, `new_device_public_key`, references the `prepared_recovery_registration` whose key it is signed by.
  Its signature is verified against the *registered recovery public key*, not against "a current automatic integrator" — a distinct authority class from ordinary device signing.
  Anti-replay/rollback fields are an open slot, not a settled design: this record type exists so downstream code has something to target, but its full ceremony (nonce scheme, expiry, single-use enforcement) is explicitly future work, cross-referenced in `open-architecture-questions.md`.
- **`display_name_claim`** — `teammate_id`, `name_commitment` (signed — the durable, hiding commitment), `name_payload` (separable — see *PII handling* below; may be absent).
  Self-signed.
  The commitment scheme itself (salting, hash construction) is not chosen here; it needs the cryptographic analysis `open-architecture-questions.md` already tracks.
  What *is* fixed now: the signature covers `name_commitment` only, never `name_payload`, so the payload can be dropped or encryption-windowed later without invalidating the record or any replay that depends on it.
- **`teammate_unification_claim`** — comes as a linked pair of records rather than one multi-signature record: one half signed by a device of the first candidate UUID, the other half signed by a device of the second, each referencing the other's `record_id`.
  Unification is only in effect once both halves exist, which is a simple existence check rather than a new co-signature envelope shape.
  Each half carries `evidence_commitment` (signed) and `evidence_payload` (separable — see *PII handling* below) rather than one plain `evidence` column.
- **`staleness_observation`** — `observing_teammate_id` (= `author_teammate_id`), `observed_teammate_id`, `observed_berth_id`, `last_observed_signal`, `local_update_counter_or_elapsed`, `warning_horizon`.
  Self-signed testimony.
  Explicitly not authoritative: it cannot exclude anyone, advance anyone's retention horizon, or declare finality — see `architecture.md`'s *Retention Horizons and Staleness*.
  Different observers may disagree; that is not a malformed state.

## PII handling: the general shape

Several record types above (`admission_proposal`'s invitee label, `display_name_claim`, `teammate_unification_claim`, `exclusion`) carry personal content that must not be permanent chain data, per `architecture.md`'s *Personal Data Is Not in the Long-Term Chain*.
Each such type follows the same shape, using the envelope's signed/separable-payload split defined under *The shared envelope* above rather than a bolt-on rule:

- a `*_commitment` column — signed, so that specific record is independently tamper-evident, but **not** part of `constitution_skeleton_at`: no other record's validity ever depends on inspecting a display name, an exclusion reason, or unification evidence, so there is no governance reason to fold it into the skeleton, and every reason not to (unbounded skeleton growth over a team's lifetime for data nothing consults)
- a `*_payload` column — separable, never signed, never part of `constitution_skeleton_at` either, and therefore droppable, encryptable-to-a-window, or physically deleted later without touching the signature or breaking replay

Skeleton verification — recomputing `constitution_digest_at` to check an anchor — therefore never touches any PII-adjacent field, commitment or payload, for any record type.
`*_commitment` is consulted only when someone wants to verify that one specific claim record's integrity, e.g. checking a later-presented payload against its own commitment — a per-record check, not a governance-replay input.

This document fixes that shape and, for each PII-bearing type, names the specific `*_commitment`/`*_payload` column pair.
It does not fix the commitment construction itself — that is a distinct, tracked, cryptography-review item, not a schema question.

## Replay and projections

```
def constitution_skeleton_at(conn, frontier: dict[str, bytes | None] | None = None) -> ConstitutionSkeleton: ...
def constitution_projection(conn, frontier: dict[str, bytes | None] | None = None) -> ConstitutionProjection: ...
```

`frontier=None` means "replay through everything currently held locally" (the live head, from this connection's point of view); an explicit frontier replays only through the referenced tips, per *The Constitution anchor* above.
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
- Aligning `key_certificate` and `teammate_berth_storage_announcement` onto the shared envelope (adding `record_id`/`record_type`/`schema_version` and the signing helper) is implementation work this document motivates but does not schedule
- The exact wire representation of `anchor_frontier` and `predecessor_record_id` — one tip per table plus per-table hash-linked predecessors is the sketch here, but a rolling accumulator or Merkle structure may be cheaper; either must satisfy the same requirement (reconstructible from record references alone, no git or wall-clock dependency)
- `proposal_revision`'s exact shape (referenced under `endorsement` above) is named but not fully specified in this pass
