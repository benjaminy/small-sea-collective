# Tracker and Plan Updates for the Evidence-DAG Redirection

Working drafts for the human deliverables named in NOTES-PLAN-FOLLOW-UP.md's Follow-up section.
Nothing here has been posted to GitHub.
Each amendment below states what the current issue text gets wrong under the evidence-DAG direction begun in commit `9a31d3e` and refined by this review round.
A human (or the agent with per-item approval) posts these; delete this file when that is done.

## Issue amendments

### #163 — tracking issue (rewrite body)

Now wrong:

- Calls `Documentation/team-constitution.md` "the field-level schema"; it is now target semantics with the SQL schema deliberately unfixed.
- The slice list assumes the superseded sequence (envelope → anchor_frontier → one projection rebuild → record types).
- "Projection-rebuild replacing direct mutation of `teammate`/`berth_role`/`invitation`/`team_device`" implies one required projection.

Replacement shape:

- The Constitution is a retained signed evidence DAG with clone-relative non-pruning after local adoption; participants apply versioned named local analyses, and there is no required one-true projection or global finalization.
- `team_id` is a technical origin and replay domain, not stable social identity; living team identity is refreshed through activity and relationships, and durable splits may create multiple operational continuations with shared ancestry.
- Keep the shipped envelope slice as history; note that its `constitution_digest` anchor and direct projection updates are transitional implementation state.
- Restructure remaining slices: (1) canonical doc rewrite (done in this branch), (2) bounded state-transition model (new issue N3), (3) evidence ingestion/quarantine and candidate-Core adoption (new issue N2), (4) envelope migration re-plan (#165), (5) multi-head causal context (#166 reframed), (6) evidence graph + named analyzers + projection provenance (#167 reframed), (7) admission evidence (#164 reframed), (8) exclusion/repudiation/ratification (#168 extended), (9) acknowledgment record families (new issue N1), (10) app-to-Core basis anchors (new issue N10), (11) staleness (#169, light touch).
- Note that #162's local-acceptance merge-request framing is now the native constitutional model, not an exception to it.

### #164 — admission (reframe)

Now wrong:

- "Only finalization makes a proposal effective" directly contradicts "no finalization record turns Bob on for every participant."
- `endorsement`/`finalization` are presented as the generalized global quorum gate.

Survives:

- Inviter allocates the invitee `teammate_id`; transcript binding; self-certifying `admission_acceptance` verified against the embedded invitee key; endorsement dedupe by teammate not device; dropping `role` from the proposal.

Replacement shape:

- Scope becomes the admission evidence family: proposal, invitee acceptance, completed transcript, inviter typed acknowledgment.
- `finalization` is reinterpreted (or renamed) as evidence that the inviter observed their configured ceremony complete; local analyses decide effect.
- Concurrent governance evidence does not invalidate the proposal; each analysis decides local effect (supersedes the blanket-eligibility rule).
- Initial integration expectations ride as distinct signed proposals; admission does not imply automatic Core integration (links to N7).
- Depends on the multi-head causal-context design (#166) for the proposal's context field; note the current `anchor_commit` + `governance_digest` is transitional.

### #165 — this branch (rescope body, not just a comment)

Replace the body with the rescope already written in NOTES-PLAN-FOLLOW-UP.md:

- Envelope-only migration of `key_certificate` and `teammate_berth_storage_announcement`, retaining the settled format decisions (full SHA-256 `record_id`, signed `schema_version = 2`, signed and stored `team_id` as the current technical-origin field, explicit expected-origin verification, pinned type/version, display-only `created_at`, delete-and-recreate flag day).
- Neither type gains interim single-lineage anchor columns; the announcement stays outside constitutional standing and keeps UUIDv7 selection for this slice.
- The whole-table fail-closed loader requirement is withdrawn pending the branch-local ingestion design (N2); no implementation may replace it with silent skipping that grants authority.
- Record that this branch's delivered artifact so far is the canonical doc rewrite.
- Be explicit about what the production-authority gate does and does not cover.
  The envelope-only migration of `key_certificate` and `teammate_berth_storage_announcement` changes no standing, grants no authority, and is **not** blocked by N2/N3/N8/N9: it may proceed once the #163/#165 amendments are posted.
  What is blocked is anything that makes causal context, revocation, acknowledgment, exclusion, or repudiation production-effective.
  Without this split the branch reads as fully paused behind four unfiled issues and has no next action.

### #166 — causal context (reframe; biggest contradiction)

Now wrong:

- Specifies one-tip-per-table `anchor_frontier` plus per-table `predecessor_record_id` chains; the one-tip sketch is explicitly superseded because it cannot represent concurrent heads without covert linearization.
- Its acceptance criterion of "one normative anchor-relative resolver" is withdrawn; target is a shared verified evidence graph plus small named analyzers.

Replacement shape:

- Design and implement the multi-head causal-context encoding: a signed reference set that can name multiple heads, reconstructible from retained record references alone, with no git-checkout, timestamp, or arrival-order dependency.
- A governance record's context names the minimum dependencies its meaning requires; fuller frontier disclosure is deliberate, separate visibility evidence.
  Head gossip, receipts, and basis anchors let post-hoc analyses surface selective anchoring, incompatible claims, and suspicious reuse of old evidence without treating those patterns alone as proof of deception or automatic invalidity.
- Define full visibility as possession and structural verification of a record plus its declared transitive causal closure; it never implies acceptance or proves that the author disclosed every head they actually knew.
- Retire the transitional `constitution_digest` live-query anchor.
- Distinguish unresolvable context (dependencies cannot be assembled → the dependent conclusion fails closed) from superseded context (newer evidence exists → an analysis choice), replacing the old malformed-vs-stale pair.
- Fold in the expanded "questions that block production authority" from NOTES-PLAN-FOLLOW-UP.md as the design checklist that gates any production effect for revocation, exclusion, repudiation, acknowledgment, mode, or delegation claims.
- The old six-question revocation gate and "disable `remove_teammate` when predecessor chains become authoritative" concern carries forward: the current cert-deletion removal path must be disabled or converted before any causal-context-bearing evidence becomes production-effective.

### #167 — projections (reframe)

Now wrong:

- `constitution_skeleton_at` / `constitution_projection` as the two functions implies one governance skeleton and one projection deserving the name "team state."

Replacement shape:

- Build the shared verified evidence graph (over ingestion classifications from N2) and small named analyzers with explicit inputs.
- Manager/Hub get one inspectable **default local analysis** for ordinary operation; its outputs populate the existing cache tables (`teammate`, `berth_role`, `invitation`, `team_device`).
- Every cached projection records a reproducible basis: analyzer name and semantic version, evidence-frontier and causal-closure digest, local-acceptance and policy-input digest, and canonical result digest; caches are invalidated or rebuilt when those change.
- A process may maintain an ephemeral projection revision for cache invalidation or display.
  It may reset after restart or rebuild and is never a signed field, durable basis identifier, global clock, cross-clone coordinate, or conflict-resolution rule.
- The canonical fold is invariant to row order and irrelevant reordering of compatible concurrent evidence; incompatible evidence yields an explicit deterministic conflict unless the named policy supplies a resolution justified by evidence content.
  Representation-derived tie-breaks (identifier, timestamp, table order, arrival order) are never such a justification, and naming a policy does not launder them into authority.
- Keep the #57 fold-in (trusted device sets are outputs of a named analysis) and the two inherited loose ends (vestigial `member.device_public_key` column; device-metadata home).

### #168 — exclusion (extend)

Survives verbatim:

- The salted-commitment guidance (never land a placeholder commitment construction; salted ≥128-bit commitment with the salt inside the separable payload).

Extend:

- Split "remove teammate" into **prospective exclusion** (stop recognizing after a causal point, prior standing preserved) and **repudiation** (decline to recognize a named admission as having conferred standing), plus **selective ratification** of individual acts by a disputed actor.
- Authority phrasing becomes analysis-relative: valid-when-signed-by-a-current-automatic-Core-integrator becomes "under the named local analysis"; the default signer/acknowledgment policy for repudiation is explicitly open.
- Acceptance criteria keep the containment side effects: receiver-state purge, sender-key rotation, exclusion-respecting redistribution, rejection of the excluded teammate's announcements on re-selection — all scoped to participants whose analysis accepts the claim.
- Add the hook to application repair: an accepting analysis identifies affected constitutional evidence and locally recorded reliance, then asks applications what repair capabilities they provide (N4).
  Neither the Constitution nor Cod Sync claims to attribute arbitrary Git changes to the disputed teammate.

### #169 — staleness (light touch)

- Wording only: "cannot make a lineage final" becomes "cannot make an analysis final"; the retention exemption is restated categorically (Constitution objects adopted into a clone's live Core database and their declared causal closures are non-prunable in later states of that clone; separable payloads, ordinary blobs, and fetched-but-unadopted parked input may dehydrate or remain bounded).
- Scope unchanged; the staleness-to-checkpoint rule stays open.

### #162 — merge requests (light amend)

- Note that the evidence-DAG reframe makes its core claim ("acceptance remains local; Alice might merge a proposal while Bob rejects it") the native constitutional model.
- The admission-reuse paragraph should stop describing admission as a quorum flow; the reusable shape is proposal → signed responses → typed acknowledgment → local integration.

### #123 — teammate transport announcements (amend, carried over)

- `select_effective_teammate_transport` still selects by raw identifier order.
- Decide whether the type remains at all; if it stays authoritative for anything, it must surface concurrent heads instead of choosing by identifier order, and it must name the signer/standing analysis it uses.

## New issues to file

- **N1 — Acknowledgment record families.** Design the minimal signed fields and distinct meanings for mechanical visibility, witnessed receipt, acceptance, ratification, interaction attestation, delegation acknowledgment, objection, repudiation, and reconciliation records.
  Prefer distinct record types over one generic table where that keeps semantics unconfusable.
  Prune aggressively against the model (N3); candidate families are not commitments.
  For each family, document the indirect personal metadata its existence adds to retained evidence (witnessed receipts, for example, durably trace when a person was at their device).
  Witnessed receipt needs explicit treatment as the weakest rung: no recipient can check it, it is the author's own client reporting on its own user, and any policy consuming it trusts that client's restraint.
- **N2 — Evidence ingestion, quarantine, resource safety, and candidate-Core adoption.** Implement the classifications (received / verified / unresolved / quarantined / parked authentic / locally accepted / locally effective) and the branch-local failure rule.
  Define the record-level transition that adopts verified evidence into the live Core database, including staging and atomicity when Git merges and SQLite candidates are involved.
  Adoption is a retention transition, not acceptance or local effect.
  Decide the quarantine representation, authentic-input intake budgets, local device suspension, clone-relative non-pruning after adoption, and whether a candidate SQLite database with malformed rows can be partially preserved or must be rejected while parked on a Git branch.
  Core rows arrive inside a merged tree, so non-adoption is an active merge operation: the driver must emit a tree that deliberately omits rows a parent commit contained, which makes the merge result depend on local intake state rather than on the merged inputs alone.
  Parked and declined state must persist outside the live Core database, or the next merge silently re-decides it.
  Note that non-pruning is an implementation obligation, not a peer-verifiable property; no analysis may read absence as proof of non-existence.
  Limits may park claimed repair input, but an operator must be able to inspect, resume, or explicitly override them; a sender cannot self-classify into an exempt repair lane.
  This replaces the whole-table fail-closed loader; silent skipping that grants authority remains forbidden.
- **N3 — Bounded executable state-transition model.** This blocks all production authority work.
  Cover admission, typed acknowledgments, declared causal visibility, interaction evidence, device linking, compromised-device suspension, revocation, exclusion, repudiation, ratification, concurrent histories, durable continuations, malformed/missing/abusive input, and application-declared repair.
  Model a versioned canonical fold whose compatible independent events commute and whose unresolved conflicts are explicit.
  Production analyzers must agree with the model over the bounded state space.
- **N4 — Cod Sync forward restoration and application repair capabilities.** Cod Sync owns generic forward-only restoration and overwritten-interval inventory, not authorship or replay honesty.
  Define a manifest that can name clean base / pre-repair head / omissions / conflicts / irreversible external effects and accurately declare whether replay is user-directed, author-asserted, or application-cryptographically attributable.
  App-specific replay follow-ups should begin with ssc-files `repair_niche`.
- **N5 — Causal berth-storage announcement protocol.** The preserved candidate design from NOTES-PLAN-FOLLOW-UP.md. Must name the local signer/standing analysis it uses; owns removal of `announcement_id` ordering, verified-graph selection, quarantine, checkpoints, Hub statuses, Manager repair, and the executable selection model.
- **N6 — Team policy claims and risk/availability profiles.** How a team records non-binding recoverable/guarded expectations as Constitution evidence, and how Manager/Hub consult them for automatic local behavior.
  This is the home of configurable team governance, which is expected to grow.
  Define which evidence rungs a profile may consume as effect inputs (witnessed receipts, locally observed silence at an explicit basis or cutoff, typed positions) and the fixed boundary: policy chooses local effect but never reinterprets record meaning or proves that nobody objected.
  Define the minimal non-configurable floor only for actions that can cause unrecoverable damage, such as disclosing fresh key material.
- **N7 — Invitation UX: separate admission from Core promotion.** Revisit the `steward` default; present initial integration expectations as distinct signed choices rather than an admission side effect.
- **N8 — Eventual visibility and causal-closure mechanism.** architecture.md promises eventual visibility ("a previously observed head must not silently disappear"), but nothing implements non-effectful head gossip, missing-dependency retrieval, or signed claims of possession of a declared causal closure.
  This is a production-authority prerequisite, not late discovery polish; it may share transport with #162 while keeping visibility distinct from integration.
- **N9 — Technical origins, living team continuations, and split namespaces.** Demote `team_id` to a replay domain/shared-ancestry marker and decide its long-term name or derivation.
  Model team identity as observer-recognized continuity refreshed through signed activity and relationships.
  Define when durable incompatible continuations receive distinct crypto, routing, application-storage, and external-delegation namespaces without claiming that either is the real team.
- **N10 — App-to-Core basis anchors.** Define a signed Core basis-anchor record and the coarse record digest carried by app-berth publications.
  Bind the digest to the update in device-signed publication metadata without requiring arbitrary application content to become Constitution evidence.
  The anchor is a claim about the publisher's named constitutional basis, not proof of possession, completeness, freshness, or global ordering; an unfamiliar anchor means mismatch rather than necessarily “receiver behind.”
  Specify retrieval, analyzer/evidence/local-input/policy/result commitments, privacy, and post-hoc signals for stale, selective, incompatible, or unavailable anchors.

## Remaining doc audit targets (plan step 4)

- `packages/wrasse-trust/README-brain-storming.md` — the global-finalization, accepted-lineage, and stale-epoch target language was aligned in this review round; continue auditing mechanism details against the canonical analysis-relative model.
- `Documentation/linked-teams.md` — stable-team-identity and automatic-integrator-quorum language was aligned to technical origin, living continuation, and acknowledgment-threshold policy in this review round.
  Continue auditing the exploratory record sketches as the canonical envelope evolves.
- `packages/small-sea-hub/spec.md` — session wording now identifies `team_id` as an opaque technical replay-domain identifier rather than a friendly or social identity.
  The Hub still has no constitutional-standing logic.
- `packages/wrasse-trust/device_provisioning_todo.md` — still entirely epoch-based (`MembershipEpoch`, `advance_team_epoch`, stale-epoch rejection); align with continuation/analysis language or mark as superseded planning.

## Suggested ordering

1. Post the #163 and #165 amendments (unblocks honest tracker state for the paused branch).
2. File N3 (model), N2 (ingestion/resource safety), N8 (visibility), and N9 (living continuations) — they gate everything marked "blocks production authority."
3. Post #166/#167/#164/#168 reframes, which reference N2/N3.
4. File the remaining new issues and light amendments (#169, #162, #123, N1, N4–N7, N10).
5. Finish the remaining mechanism-level doc audit in a small follow-up.
