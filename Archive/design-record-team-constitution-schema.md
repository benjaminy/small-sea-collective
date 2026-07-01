# Team Constitution Schema Design Record

Written retroactively at archive time (see `Archive/branch-plan-team-constitution-schema.md`).
The design itself is in [`Documentation/team-constitution.md`](../Documentation/team-constitution.md); this record captures the choices a future reader might want to revisit, plus the code-grounding survey the branch's `NOTES.md` originally held.

## Naming

**Team Constitution** names the signed, append-only governance lineage carried inside a team's Core berth — deliberately distinct from **Core**, which stays the name for the berth/database that carries it. Chosen because "constitutional" was already the load-bearing metaphor throughout `architecture.md` (a "Core as Constitutional History" section already existed) before this branch; the branch just made it a proper noun and used it to disambiguate the previously-overloaded word "Core."

## Grounding survey (what existed before this schema)

The canonical-signing idiom — build a dict with binary fields hex-encoded, `json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")`, sign everything except `signature` itself — was already independently reimplemented three times with no shared helper: `wrasse_trust/identity.py:95-116` (`key_certificate`), `wrasse_trust/transport.py:84-116` (`teammate_berth_storage_announcement`), and private helpers in `small_sea_manager/provisioning.py` (the admission-proposal flow). `key_certificate.cert_id = sha256(canonical)[:16]` was already a content-derived ID, not a random one — the schema's `record_id` generalizes that pattern rather than inventing a new one. Signing is Ed25519 via `cryptography.hazmat.primitives.asymmetric.ed25519` (not PyNaCl); keys live as raw unencrypted bytes under a `FakeEnclave` directory, an existing, explicitly-named placeholder, not something this schema needed to design around.

The anchor mechanism already existed in miniature, scoped only to admission: `admission_proposal.anchor_commit` (a real git commit hash, `_team_head_commit()`) plus `governance_digest` (`sha256(canonical_json(governance_snapshot))` over just admins/teammates/teammate_devices, `provisioning.py:925-953`). The schema generalizes this to every governance-bearing record type — see *Anchor mechanism* below for why the generalization couldn't just widen the same git-commit-based approach.

A concrete existing bug, found and flagged but deliberately not fixed on this documentation-only branch: `admin_approval` dedupes `UNIQUE(proposal_id, approver_device_key_id)` — by *device*, not by teammate — so two devices of the same endorser currently double-count, contradicting the already-documented target behavior.

No migration system exists in this codebase (no Alembic, no `migrations/`); schema changes are `CREATE TABLE IF NOT EXISTS` plus a bump to the single `USER_SCHEMA_VERSION` constant, and both `_migrate_user_db`/`_migrate_team_db` explicitly `raise NotImplementedError` — pre-alpha migrations aren't supported, delete and recreate instead. This is why the schema doc doesn't design a migration path from the current mutable rows.

## Key design decisions, in the order they were pressure-tested

**Per-type SQL tables, not one generic table.** Considered and rejected a single type-tagged-JSON-blob table in favor of matching the codebase's existing style (one table per record type, like `key_certificate`/`admission_proposal` already are). Cost: every type shares a signing helper and envelope columns instead of separate ad hoc serialization, without losing FK/uniqueness constraints per-type tables give for free.

**Signed columns vs. separable payload, made structural, not incidental.** First draft signed "every type-specific column," which directly contradicted the requirement that PII payloads (`display_name_claim.payload`, `exclusion.reason`, etc.) be droppable without invalidating the record's signature. Fixed by making the split a property of the envelope itself: every PII-bearing type is a `*_commitment` (signed) / `*_payload` (never in the signing bytes) pair, applied uniformly rather than ad hoc per type.

**Anchor mechanism: record-based, not git-commit-based.** The first draft generalized `admission_proposal`'s `anchor_commit`/`governance_digest` pair directly — reasonable-looking, since it's exactly what the existing code does. It doesn't survive scrutiny: `architecture.md` already states Git commit authorship is not the authority for governance facts, and Core's retention section promises only a *conservative* live-data window, not infinite blob retention, so an old `anchor_commit` isn't guaranteed checkout-able forever. Since Constitution records are never deleted from the live database by design, replay should never need git blob access at all. Fixed by making `anchor_commit` informational-only and introducing `anchor_frontier` (per-table tip pointers, chained via `predecessor_record_id`) as the authoritative, git-independent reference. The exact wire encoding of the frontier (per-table tips vs. a rolling accumulator vs. a Merkle structure) is deliberately left open — the fixed requirement is only that it be reconstructible from record-to-record references alone.

**Two independent single-exception rules, not one exceptional action.** Early prose described admission as breaking both "who can sign" and "how many signers are required" at once. On closer inspection these are orthogonal: `admission_proposal` is signed by the inviter, who is already recognized — it is *not* an exception to "signer must be recognized." Its only exceptional property is needing quorum, and that's independently justified (a single recognized signer's word isn't deemed sufficient for creating new standing) — proven by the fact that `quorum = 1` is already a valid default, which would make no sense if quorum existed solely to compensate for an unrecognized signer. The actual "signer not yet recognized" exception belongs to exactly one type: `admission_acceptance`, which is self-certifying (verified against a public key the record itself supplies) rather than looked up in the device graph.

**Consistent FK naming.** `endorsement`/`finalization` referenced a proposal via `subject_record_id`; `admission_acceptance` initially referenced the same thing as `proposal_id` — a name that didn't even exist as a column once `admission_proposal` moved onto the shared envelope (its identifier is `record_id`, inherited). Unified all three onto `subject_record_id`.

**Commitments are signed but explicitly not in `constitution_skeleton_at`.** No other record's governance validity ever depends on inspecting a display name or exclusion reason, so there's no reason to fold commitments into the replay skeleton, and a real cost (unbounded skeleton growth over a team's lifetime for data nothing consults) to doing so anyway. Skeleton verification therefore never touches any PII-adjacent field — commitment or payload — for any record type; a `*_commitment` is consulted only when verifying that one specific record's own integrity, a per-record check, not a governance-replay input.

## Deliberately left open (unchanged from the doc, repeated here for visibility)

- the PII commitment scheme's exact construction (needs cryptographic analysis, tracked in `open-architecture-questions.md`)
- the recovery ceremony's anti-replay/rollback mechanics
- the staleness-to-checkpoint rule
- the exact wire representation of `anchor_frontier`/`predecessor_record_id`
- `proposal_revision`'s exact shape
- whether any action beyond admission ever needs a configurable endorsement threshold above one

## Issue survey outcome

Executed against the permission-vocab branch's `FOLLOW-UP.md` sequencing (`Archive/follow-up-codex-permission-vocab.md`). Opened #163 as the implementation anchor for this schema. Retitled #20 (dropped misleading "rebase" framing — it re-roots the cloud bundle chain, not the local git DAG) and #16 (confirmed via code that it's pure local Hub session authorization with no team dimension, not a mode-aware-replication question as originally assumed). Commented on #6, #162, #57, #150, #73, #43, #11, #12, #135 connecting each to the specific new doc section now bearing on it, correcting two of FOLLOW-UP's own groupings along the way (#43/#73 connect to `exclusion`-triggered rotation, not retention/staleness as originally grouped).
