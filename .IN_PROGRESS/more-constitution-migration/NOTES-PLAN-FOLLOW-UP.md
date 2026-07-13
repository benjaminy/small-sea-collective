This is a living document for the branch.

Branch: `more-constitution-migration` — originally GitHub issue #165, part of #163.

The review-by-review discussion that produced the superseded single-lineage plan is preserved in [`Archive/branch-plan-issue-165-committee-history.md`](../../Archive/branch-plan-issue-165-committee-history.md).
That history is evidence, not active guidance.

# Notes

## Architectural redirection

The branch began as a representation migration for `key_certificate` and `teammate_berth_storage_announcement` onto the shared Constitution envelope.
Before implementation, the Team Constitution model changed materially.

The Constitution is now a retained signed evidence DAG rather than one accepted governance lineage that every participant is expected eventually to share.
Participants may accept different evidence, publish typed acknowledgments or repudiations in their own clones, apply different post-hoc analyses, and remain in an honest team split.
The architecture does not require the DAG ever to resolve to one true state.

This change supersedes several premises of the previous #165 plan:

- one globally meaningful current projection;
- one-tip-per-table constitutional frontiers;
- inviter-published admission finalization as a globally effective transition;
- blanket invalidation of an admission proposal after any concurrent governance change;
- treating one unreadable independent governance leaf as invalidating every otherwise useful trust analysis;
- assuming admission and automatic Core authority are one grant.

The branch must finish the canonical documentation and re-plan the implementation before changing record formats or production authorization.
Issue boundaries do not constrain that re-plan.
The project is research and should change any affected schema or workflow required by the evidence-DAG model.

## Durable safety invariants

These constraints outrank issue convenience and sequencing:

1. Constitution evidence is append-only.
   No production path emits a later state of a clone that deletes a Constitution object that clone already adopted.
   This binds our implementation; it is not peer-verifiable, because never adopting and dropping after adoption produce the same observable absence.
   No analysis may infer non-existence from a record's absence in someone else's published state.
2. The Constitution does not have one required projection.
   Every operational projection names its analyzer and version, evidence frontier, local acceptance inputs, and policy.
   It records digests of the evidence closure, local inputs, and canonical result.
   A runtime may use an ephemeral local revision for caching or display, but it may reset and is not part of the durable decision basis.
3. Governance causal context is multi-head and record-based.
   No timestamp, UUID order, table order, row-arrival order, or Git authorship chooses authority.
   A named policy may resolve a conflict only on grounds justified by evidence content; naming a policy does not license a representation-derived tie-break.
4. A named analysis is deterministic.
   The same evidence, local inputs, and policy produce the same answer; different analyses may disagree.
5. Authenticity never implies local effect.
   A signature proves what a key signed, not human intent, social acceptance, real-world identity, or external agency.
6. Missing, malformed, or unauthenticated evidence is inert.
   It cannot create a head, suppress another record, or grant standing.
7. Failure is branch-local when records can be separated safely.
   An unresolved dependency fails closed for conclusions that depend on it without erasing independent accepted evidence.
   An unreadable database container may still make the containing candidate snapshot unusable.
8. Records are signed and verified for one exact technical origin and supported record version.
   That origin is a replay domain and shared ancestry marker, not eternal social team identity.
9. Exclusion and repudiation preserve confidentiality as well as projection state.
   An accepting participant purges receiver state, rotates sender keys where needed, and excludes removed devices from redistribution.
10. Admission, automatic Core integration, and external authority are separate claims.
    One mistaken admission must not automatically amplify into unrestricted governance authority.
11. Local Core adoption is the retention decision: a Constitution object adopted into a clone's live Core database, plus its declared causal closure, is non-prunable in later states produced by that clone.
    Adoption is not acceptance and grants no local effect by itself.
    This is not physical immortality or a claim that every clone has the same set.
    Separable direct-identity payloads, ordinary application blobs, and fetched-but-unadopted input may leave the live-data window or remain resource-bounded.
    Because Core is a SQLite database carried in Git, declining to adopt is an active merge operation rather than passive non-action, and parked or declined state must persist outside the live Core database to survive restart.
12. Git repair only moves forward.
    A new commit may contain an old state, but shared refs are not reset and history is not rebased away.

## Evidence classifications

Production loading and analysis need at least these classifications:

- **received input** — bytes observed from sync or another source, with no implication of authenticity, retention, or effect;
- **verified evidence** — supported format, correct team binding and content ID, valid signature, and available structural dependencies;
- **unresolved evidence** — structurally plausible but missing a dependency or signer interpretation;
- **quarantined input** — malformed, wrong-team, unsupported, or invalidly signed material held outside the verified graph;
- **parked authentic input** — verified but fetched-and-not-adopted independent input held outside the live Core database under resource policy;
- **locally accepted evidence** — verified evidence selected as an input to one named participant analysis;
- **locally effective projection** — a cache derived from accepted evidence plus explicit policy and acknowledgment choices.

Raw row presence is never authority.
Unknown or quarantined rows remain available for diagnostics and later re-evaluation where safe, but do not poison unrelated evidence merely by existing.
Authenticity alone creates neither an effect nor an entitlement to adoption.
Adoption brings a record and its declared causal closure inside that clone's non-pruning boundary.

Candidate Core adoption still uses staging and validation.
The new design must decide whether a candidate database can preserve quarantined rows separately or whether SQLite/container constraints require rejecting that candidate commit while retaining it on a parked Git branch.
The old whole-table fail-closed loader is not automatically the target answer.

## Settled shared-envelope properties

The architectural redirection does not undo these record-format decisions:

- `record_id` is the full SHA-256 digest of version-specific canonical signed bytes.
- Signed `schema_version = 2` identifies the full-digest format.
- Every team-stored envelope record carries signed `team_id BLOB NOT NULL` as the current technical-origin and replay-domain field.
- Verification requires an explicit expected technical origin and pins `(record_type, schema_version)`.
- Record-ID derivation is checked independently of signature verification.
- Database bridges reject a stored team mismatch before returning a domain object.
- `created_at` is display and diagnostic data, never authority or ordering.
- Diagnostics use a total, bounded formatter for hostile SQLite values.
- Existing development databases cross a pre-deployment delete-and-recreate schema boundary with no compatibility layer.

The exact multi-head causal-context field and predecessor representation are no longer settled.
The long-term name and derivation of the technical origin are also open; `team_id` must not be treated as stable social identity.
Neither `key_certificate` nor `teammate_berth_storage_announcement` should gain interim single-lineage anchor columns during the envelope-only migration.
The operational storage announcement remains outside constitutional standing.

## `key_certificate` migration properties

- SQL adopts shared envelope names while the domain dataclass may retain truthful issuer vocabulary behind one explicit mapping.
- Team binding is signed and stored.
- Pre-team certificates may exist in memory but may not enter a team database.
- Revocations fold into the same retained record family as negative evidence and never create positive trust edges.
- Free-text revocation reasons do not enter retained signed evidence.
- A well-formed revocation changes no production standing until named analyses and causal semantics are implemented.
- The old `CertGraph` remains a traversal utility, not a globally authoritative resolver.

The previous requirement that any unreadable certificate invalidate the entire trust view is withdrawn pending the branch-local evidence design.
No implementation may replace it with silent skipping that accidentally grants authority.

## `teammate_berth_storage_announcement` migration properties

The envelope migration preserves current operational behavior until a dedicated causal-announcement issue changes it:

- The record uses the shared envelope plus its existing routing fields.
- `record_id` becomes the primary key while signed `announcement_id` remains the unique UUIDv7 ordering key.
- The record is operational, not constitutional standing.
- A row is eligible only when its signature and claimed signer verify under the named local analysis.
- An unknown signer remains inert and diagnostic until later evidence permits re-evaluation.
- Among eligible rows, descending `announcement_id` preserves the current selection behavior for this migration slice.
- Publication still requires a materialized local allocation and durable provider-issued locator where applicable.

No raw identifier ordering should remain authoritative indefinitely.
The dedicated follow-up owns causal successors, divergent-head handling, checkpoints, repair, and new Hub statuses.

## Candidate causal-announcement follow-up

The earlier candidate design remains useful but is not implemented by this branch:

- Replace `announcement_id` ordering with signed, sorted, unique predecessor references.
- Build the active structural graph only from records whose signatures and signer interpretation verify.
- Keep unknown-signer rows quarantined outside the active graph until re-evaluation.
- Treat a verified record that explicitly references unavailable ancestry as unresolved rather than silently bypassing the dependency.
- Keep historical signature verification separate from current local standing.
- Allow content-identical trusted heads to route while content-divergent trusted heads block.
- Surface uncovered leaves from no-longer-accepted signers instead of silently choosing stale storage.
- Permit explicit signed checkpoints for repair without erasing concurrent heads.
- Require Manager repair to name the complete locally observed verified leaf set and a successfully materialized local allocation.
- Ensure repair racing a concurrent publication leaves the new head visible and repeats rather than erasing it.
- Verify the production selector against an executable bounded reference model.

The evidence-DAG architecture may change the exact meanings of “trusted” and “current standing” in this candidate.
The follow-up must name the local analysis it uses before implementation.

## Questions that block production authority

Before any revocation, exclusion, repudiation, admission acknowledgment, integration-mode claim, or delegation affects a production decision, the design must answer:

1. What exact evidence is structurally required?
2. Which causal context does the claim name?
3. Which minimum dependencies must the claim's context name, and which declared closure must a visibility acknowledgment possess?
4. Which named and versioned local analysis is being run, and what digests identify its evidence, local inputs, and canonical result?
5. Which typed acknowledgments or policy inputs does that analysis consult?
6. What effect is prospective, what is repudiated, and what remains available for later ratification?
7. Which missing or malformed dependencies block only this branch, and which authentic but abusive input may be resource-bounded?
8. Which operational side effects follow, including local suspension, key rotation, sender-key redistribution, quarantine, continuation namespace changes, and application-declared repair?

A single normative resolver for all purposes is no longer a goal.
The implementation should instead provide a shared verified evidence graph plus small named analyzers whose inputs and guarantees are explicit.

## Open review threads

Under active discussion; resolve before the tracker updates are posted, or record them in the relevant new issues.

Resolved so far:

- `team_id` is a convenience that carries no authorization weight; effectful paths consult the local analysis.
  Now stated in architecture.md and the envelope table.
- Resource policy is an intake-boundary rule, not a retention rule.
  The protocol imposes no replicated quota on local authorship; every receiving clone may suspend or park any source, including claimed repair input.
  Core intake defaults to low thresholds surfaced for human override, and a sender cannot bypass them by labeling input as repair.
  Now stated in team-constitution.md and open-architecture-questions.md; rate-limit prior art pointers live there too.
- Governance causal contexts name minimum dependencies; fuller frontier disclosure is deliberate, separate visibility evidence, and head gossip, receipts, and basis anchors let analyses surface selective or incompatible patterns without proving deception automatically.
  Now stated in team-constitution.md and architecture.md; #166 draft updated.
- Retention is clone-relative adoption: a Constitution object adopted into a clone's live Core database, plus its declared causal closure, is non-prunable in later states produced by that clone.
  Adoption is a retention choice, not an acceptance or effect decision.
  It is not a promise of physical survival, universal availability, or a globally complete Constitution set.
  The retention wording sweep is done (constitution, architecture, open questions, README, cod-sync README and format-spec, Manager spec, wrasse-trust brain-storming, invariant 11 above).
- The Constitution DAG is a hash-linked record DAG carried by Git commits, not literal Git commits.
- The app-to-Core link remains a useful direction when treated as a signed basis claim rather than a possession or freshness proof.
  An unfamiliar anchor signals a basis mismatch, and post-hoc analyses may surface stale, selective, incompatible, or unavailable anchors; the basis-anchor record and privacy shape remain open.
- Durable projection identity comes from basis and result digests.
  Any numeric local revision is ephemeral cache/display state and may reset.
- Non-pruning binds a well-behaved implementation and is not peer-verifiable; absence is unavailability, never proof of non-existence.
  Stated in the constitution retention section, architecture.md, open questions, cod-sync README and format-spec, and invariant 1 above.
- Adoption inside a Git-carried SQLite database is an active merge operation, not passive non-action.
  Declining a row means emitting a tree that omits rows a parent commit contained, which makes the merge depend on local intake state; parked/declined state must persist outside the live Core DB or the next merge re-decides it.
  Stated in the constitution retention section and open questions; folded into N2.
- A named policy may resolve concurrent incompatible evidence only on grounds justified by evidence content.
  Representation-derived tie-breaks (identifier, timestamp, table order, arrival order) stay forbidden even under a named policy — closing the loophole that let single-lineage selection back in.
- Witnessed receipt is the weakest rung and the only one no recipient can check; it is the author's client reporting on its own user, and a policy consuming receipts trusts that client.
- The Seven Questions now include adoption, which is orthogonal to authenticity, visibility, acknowledgment, and effect.
- The `permanent` vocabulary is gone from retention-sense prose rather than defended by a footnote; unrelated senses (Hub sessions, key hierarchies, "permanent social identity") are untouched.

Still open:

- The per-node acknowledgment direction: every Constitution node is acknowledged separately from repo-level merge.
  Refined semantics: the routine ack is a lightweight **witnessed receipt** — the person had a reasonable opportunity to look — so clients must not emit one for changes fetched while the person was absent, and the earlier auto-emitted-policy-ack idea is withdrawn for this rung.
  The ladder is: mechanical visibility attestation (device possession), witnessed receipt (human opportunity), deliberate typed positions (acceptance, objection, ratification).
  Effect under a policy may precede witnessed receipt; "integrated, not yet reviewed" stays honest visible state.
  Resolved: whether receipts gate anything is configurable team governance (N6 scope); policies may consume receipts and locally observed silence as effect inputs, but record meaning is fixed and the policy never proves that nobody objected.
  Delayed or hidden evidence may change the projection and trigger repair.
  Open mechanics: acknowledgment records themselves require no acknowledgment (regress guard); batch receipts over a named set as the volume answer; defining an explicit local cutoff without turning presence metadata or wall time into authority.

# Plan

1. Rewrite canonical architecture documentation.
   Define the retained evidence DAG, plural analyses, eventual visibility, typed acknowledgments, duration, local repudiation, team splits, authority separation, and forward-only repair.
2. Rewrite `Documentation/team-constitution.md` as target semantics rather than a prematurely fixed one-lineage SQL schema.
   Preserve the shared envelope properties that survive the redesign and mark multi-head context and new record families open.
3. Align Manager, Cod Sync, README, open questions, and this branch plan.
   Distinguish current implemented finalization/quorum behavior from target semantics.
4. Audit the repository for stale claims about one accepted lineage, global finalization, blanket proposal invalidation, whole-view failure, and reset-based repair.
5. Produce a new bounded state-transition model before production authority work.
   Cover admission, typed acknowledgments, declared causal visibility, direct interaction evidence, device linking, compromised-device suspension, revocation, exclusion, repudiation, ratification, durable team continuations, concurrent histories, malformed and abusive input, and application-declared repair.
6. Re-plan the envelope migration against the model.
   Retain the settled format decisions only where they do not preclude multi-head evidence or branch-local analysis.
7. Implement in coherent slices after the tracker and branch plan match the new architecture.

# Follow-up

The tracker changes below are human deliverables and require explicit approval before posting:

- Amend or replace #163 and #165 so they no longer assume one accepted Constitution lineage or a single normative resolver.
- Reframe #166 from “implement authoritative revocation” to verified evidence ingestion plus named revocation analyses.
- Reframe #167 around evidence-graph construction, inspectable local projections, and cache provenance.
- Require #167 projections to name an analyzer and semantic version and retain evidence, local-input, and result digests; any numeric local revision is ephemeral cache/display state.
- Extend #168 to distinguish prospective exclusion, admission repudiation, selective ratification, key containment, and application repair.
- Create or amend an issue for typed acknowledgment, interaction-attestation, delegation, repudiation, and ratification record families.
- Create a generic Cod Sync forward-restoration and repair-capability issue without assigning application authorship semantics to Cod Sync, with application-specific replay follow-ups.
- Make eventual head visibility and declared causal-closure acknowledgments production prerequisites rather than late discovery polish.
- Create an app-to-Core basis-anchor issue that treats the anchor as a signed, inspectable claim rather than proof of possession, completeness, or freshness.
- Create or amend an issue for technical origin, living team continuations, and post-split operational namespaces.
- Add authentic-input resource safety and indirect-metadata minimization to the ingestion and record-family work.
- Preserve the dedicated causal berth-storage announcement issue and require it to name the local signer/standing analysis it uses.
- Revisit the current `steward` invitation default and any admission mode plan that grants automatic Core integration as an incidental side effect.

Research should focus on narrow blocked questions rather than importing an internet-scale consensus system wholesale.
Useful comparisons remain Keybase sigchains, Secure Scuttlebutt identity forks, Matrix room-state authorization, MLS epochs, Certificate Transparency structures, and human-readable eventually consistent repair systems.

At the end of the branch, `DESIGN-RECORD.md` should record why the earlier single-lineage #165 plan was abandoned before implementation and which envelope decisions survived.
It should not duplicate the canonical evidence-DAG architecture.
