# The Team Constitution

Status: architectural design document.
The signed record envelope exists in partial form, but the evidence-DAG interpretation, typed acknowledgments, repudiation, and repair model described here are not yet implemented.
This document defines the target semantics before fixing their wire representation or SQL schema.

## Core vs. the Constitution

**Core** is the berth — `{Team}/SmallSeaCollectiveCore` — and the SQLite database that carries team evidence.
The **Team Constitution** is the retained set of signed, causally related records through which people make claims about a team: admission, device identity, acknowledgments, authority, integration expectations, exclusion, repudiation, recovery, and related facts.

The Constitution is an evidence DAG, not a replicated register that must eventually resolve to one true team state.
Different participants may hold different subsets, accept different branches, or apply different analyses to shared evidence.
They may converge later, remain in durable disagreement, or discover years later that a different interpretation of retained evidence is useful.
The protocol preserves the signed evidence that participants adopted into their Core databases; it does not appoint one analysis as metaphysically final.
It does not promise that authentic input no clone chose to adopt remains available for later reinterpretation.

A stable technical origin is not the same thing as a stable social identity.
The signed `team_id` currently separates one team's records from unrelated records and prevents cross-origin replay.
It names a shared birth namespace, not the one eternally real continuation of that team.
If Alice and Bob continue as Falcons without Carol while Carol continues separately under the same history and name, the evidence has two descendants with shared ancestry.
No identifier decides which descendant is “the real Falcons.”
Each participant or external counterparty recognizes a living continuation through signed activity, relationships, and the analysis they apply to that evidence.
The exact cryptographic origin reference and the operational namespace used after a durable split remain open.

Mutable tables such as `teammate`, `berth_role`, `invitation`, and `team_device` are local projections.
They are rebuildable answers to a named analysis over locally held evidence, not the durable source of team truth.
A projection must therefore identify its analysis name and version, evidence frontier, local acceptance choices, and policy assumptions.
Digests of those inputs and of the resulting canonical state make the basis of a later decision reproducible and comparable.
An implementation may also expose an ephemeral local revision for cache invalidation or display, but that counter is not part of the durable basis and may reset when the cache or process is rebuilt.

## Seven Different Questions

Several concepts that look similar in a conventional centralized membership database are deliberately separate:

- **Authenticity** asks whether canonical bytes were signed by the claimed device key and bound to the correct team.
- **Causal authority** asks what evidence about that key and its author existed at the record's declared causal context.
- **Visibility** asks which record and declared causal closure a participant has attested to obtaining and structurally verifying.
- **Adoption** asks whether a clone has taken a record into its live Core database and thereby committed itself to retaining it.
- **Acknowledgment** is a signed statement that a participant accepts or ratifies a particular claim for a stated purpose.
- **Reliance** records that someone acted on a claim, such as integrating a publication or accepting a presented delegation.
- **Local effect** is what one participant's current analysis permits their Manager, Hub, and applications to do.

Authenticity is cryptographic and binary.
Causal authority is a replayable property of a branch and an analysis.
Visibility is evidence of possession, not agreement.
Adoption is a retention commitment, not agreement or effect.
Acknowledgment and reliance are additional evidence.
Local effect remains a participant's decision.
None of these facts forces every clone to produce the same current roster.

## Security Boundary

The Constitution cannot stop a real, authorized Alice from showing Bob plaintext that Alice can already read.
It must not claim a cryptographic confidentiality boundary against a malicious admitted endpoint.

That limitation does not excuse two different failures:

- Bob must not be able to poison accepted application or governance state merely because his bytes arrived.
- A network attacker, stolen invitation, compromised transport, or ambiguous merge must not be able to manufacture evidence that the real human Alice intended to admit Bob.

An admission ceremony must bind Alice's signed intent to the exact team, causal context, invitee identifier, invitee device keys, nonce, and proposed initial effects.
The exact user-presence and out-of-band identity-verification mechanisms remain open, but transport possession alone is never admission authority.
A stronger team policy may require independent acknowledgments before a claim has local effect; a higher-availability policy may accept one explicitly authorized signer and rely more heavily on detection and repair.

An authentic device can still be malicious, stolen, or controlled by malware.
Authenticity therefore creates neither an entitlement to automatic effect nor an unlimited right to consume every participant's storage and analysis budget.
A participant may locally suspend a suspicious device, stop integrating its publications, stop distributing future keys to it, and apply bounded intake while signed exclusion or revocation evidence propagates.
High-amplification actions may require recent independent relationship evidence, explicit user presence, or additional acknowledgments under the chosen policy.

No policy profile may weaken canonical signature verification, technical-origin binding, causal references, non-pruning of evidence that clone adopted, the rule that unauthenticated evidence is inert, or the fixed meaning of a typed record.

## The Shared Signed Envelope

Constitution objects and related signed operational records use one versioned signing convention.
The current implementation is migrating existing record types onto this shared shape.
The target envelope contains at least:

| Field | Meaning |
|---|---|
| `record_id` | Full SHA-256 digest of the version-specific canonical signed bytes |
| `record_type` | Versioned domain discriminator |
| `team_id` | Signed technical-origin binding and replay domain; not proof of one stable social team identity, and never an authorization input by itself |
| `author_teammate_id` | Team-local identity for whom the signing device speaks |
| `author_device_key_id` | Concrete device key that signed the record |
| `created_at` | Display and diagnostic time only; never authority or ordering |
| causal context | Signed reference to the minimum prior Constitution evidence the claim's meaning requires; required for governance claims and omitted for record domains that do not use constitutional standing |
| `schema_version` | Signed format marker whose version defines canonicalization and identifier derivation |
| `signature` | Signature over every signed envelope and type-specific field except `record_id` and `signature` |

`record_id` is derived independently of signature verification.
Binary values are hex-encoded and the remaining signed fields are serialized canonically with sorted keys and compact separators.
Every verifier pins the supported `(record_type, schema_version)` pair rather than reinterpreting unknown bytes.

The existing names `anchor_commit`, `constitution_digest`, and `anchor_frontier` are implementation and design-stage representations of causal context.
Git commit identity may remain useful diagnostic context, but Git authorship is never constitutional authority.
The authoritative context must be reconstructible from Constitution record references without checking out historical Git blobs.

For a governance claim, causal context names the minimum prior Constitution evidence the claim's meaning requires: the records identifying its subject and any evidence it explicitly builds on or answers.
It is not a compelled disclosure of everything the authoring device had observed.
Fuller frontier disclosure is deliberate, separate visibility evidence — witnessed receipts, visibility acknowledgments, and head gossip.
Those channels let post-hoc analyses surface patterns such as signing against an older basis after acknowledging newer relevant evidence, repeatedly selecting only favorable branches, or issuing incompatible claims.
Such a pattern is evidence for people and named analyses; it is not automatically invalid, and it does not by itself prove deception rather than mistake, partition, or software failure.
An outsider without a device key recognized by the receiving analysis cannot turn selective anchoring into authentic authority; a compromised or haywire recognized device can create authentic suspicious patterns, which a clone may suspend while people investigate.
No verifier could prove a completeness claim anyway; the minimal rule keeps retained metadata small and keeps honestly bounded intake from resembling concealment.

The earlier one-tip-per-table sketch is superseded.
Concurrent descendants are ordinary DAG evidence and a causal context must be able to name multiple heads without silently choosing one by identifier, timestamp, table order, or arrival order.
The exact frontier encoding remains open.

## Structural Ingestion and Branch-Local Failure

Ingesting evidence and granting it effect are separate operations.
A loader should classify records and dependencies before any local analysis treats them as authority.

- A well-formed, correctly signed record whose dependencies are available enters the verified evidence graph.
- A record with a missing dependency remains unresolved and cannot affect an analysis that requires that dependency.
- A malformed record or invalid signature remains quarantined input outside the verified graph for diagnostics and cannot create a head, suppress another record, or grant authority.
- A record from an unknown signer remains inert until independently verified evidence makes the signer interpretable.

Failure is branch-local whenever the data representation permits it.
An unreadable dependency must fail closed for every conclusion that depends on it, but one hostile or malformed leaf must not erase the usefulness of independent accepted evidence or create a whole-team denial of service.
Corruption of the SQLite database or another failure that prevents records from being separated safely may still make the containing candidate snapshot unusable.

Two analyzers given the same evidence, the same local acceptance inputs, and the same policy must reach the same answer.
Different inputs or different post-hoc analyses are allowed to reach different answers and should identify why.

Correctly signed input can also be abusive.
Resource policy may bound, park, summarize, or decline excessive independent input without treating it as malformed or granting it effect.
Authenticity alone does not entitle input to be adopted.
Local Core adoption is the retention decision: once a Constitution object and its declared causal closure enter a clone's live Core database, later states produced by that clone retain them.
Parking or summarizing authentic input should preserve a bounded, authenticated indication that competing input existed rather than silently presenting it as absent; the exact mechanism remains open.
The Constitution protocol imposes no replicated quota on local authorship; each device controls what it publishes, and each receiving clone controls what it fetches and adopts.
Resource bounds are protective plumbing for a small team, not scale machinery: crossing one should surface the situation for human judgment rather than silently enforcing policy.
They may pause or park any new input, including claimed repair input, but must leave an operator-visible path to inspect, resume, or explicitly override the limit.
A remote author cannot bypass intake policy merely by labeling an object as repair, and resource policy never permits pruning evidence the clone already adopted.

## Declared Visibility and Causal Closure

A visibility acknowledgment names a record and says what the author had obtained, not what they accepted.
A full visibility acknowledgment of record `X` means that the acknowledging participant obtained and structurally verified `X` plus every dependency transitively reachable from `X`'s declared causal frontier.
It therefore compactly covers the declared closure without turning every ancestor into an acceptance.
Like every other acknowledgment it is an accountable signed claim, not a cryptographic proof of possession.

If a dependency is missing, a participant may record that they observed unresolved `X`, but they cannot claim full visibility of its closure.
Nor does a visibility acknowledgment prove that `X` named everything its author actually knew or that no concurrent sibling head exists.
Eventual visibility still requires participants to gossip observed heads so that independently presented descendants can meet.

## Evidence Accumulates Through Duration

Small Sea does not treat identity, trust, or authority as facts established completely in one ceremony and then merely maintained.
Evidence can accumulate through duration and real interaction.
It can also lose persuasive force when relationships and activity are not refreshed.
Historical signatures do not expire as facts, and the protocol defines no dormancy threshold after which agency lapses.
Instead the system surfaces dormancy honestly — last observed activity, staleness observations, unrefreshed relationships — so people can judge whether a quiet teammate or continuation should still be treated as able to act now.
Apparent dormancy can also be manufactured by hiding someone's activity, so it always means "not observed here," never "not active anywhere."

Alice's signed admission of Bob says that Alice made a particular claim about a particular key at a particular causal context.
Carol may initially acknowledge Alice's choice while knowing nothing about Bob personally.
Later Carol may meet Bob, compare device-bound material through an in-person phone interaction, accept Bob's work repeatedly, or sign a narrower delegation.
Each event adds a small, distinct fact rather than retroactively turning the initial admission into a stronger fact than it was.

This model supports post-hoc analyses such as:

- which participants accepted Alice's admission of Bob, and when;
- which participants later accumulated direct interaction evidence about Bob;
- which devices and publications were relied on during a disputed interval;
- what evidence supported Bob's authority to perform a particular action;
- which participants accepted a later repudiation or ratified selected Bob-originated acts.

The evidence DAG should preserve only the minimum structural facts required for those analyses.
Those facts may still be indirect personal information.
Human-readable identity material remains separable payload under the privacy rules below.

## Typed Acknowledgments and Reliance

A bare acknowledgment is too ambiguous to be useful.
Every acknowledgment record must name its subject and its meaning.

Candidate meanings include:

- **acceptance** — the author chooses to treat a claim, such as Alice's admission of Bob, as effective in the author's local analysis;
- **ratification** — the author adopts a particular prior act even though the actor's standing is disputed or later repudiated;
- **interaction attestation** — the author records a bounded additional fact about direct interaction or key comparison;
- **delegation acknowledgment** — the author accepts a specific grant of authority with explicit scope;
- **witnessed receipt** — the author records that a named record was surfaced while they were actually present and they had a reasonable opportunity to look; it implies neither acceptance nor objection.

A witnessed receipt is meaningful only if it is not automated: a client must not emit one for changes fetched while its person was absent.
No signature can establish that a person was present and looking, so the receipt is the author's own software reporting on its own user.
A policy that consumes receipts is trusting the author's client to observe that restraint, and a modified or compromised client can emit receipts its person never earned.
This is the weakest rung of evidence the Constitution carries and the only one whose meaning no recipient can check.
Effect under a policy may precede witnessed receipt; "integrated, not yet reviewed" remains honest, visible state until the person actually looks.

Observation alone is not acceptance.
Fetching Bob's record, seeing an admission prompt, or integrating one of Bob's documents must not silently become constitutional endorsement.
Operational systems may retain signed reliance receipts or integration provenance, but those records do not acquire a governance meaning unless they explicitly say so.
The Constitution does not require Git commits or arbitrary application content to carry constitutional signatures.
Applications choose whether repair relies on user judgment, author-asserted provenance, or application-defined cryptographic provenance.

The exact acknowledgment record types and their minimal signed fields remain open.
They may be distinct record types rather than one generic table if that keeps their semantics harder to confuse.

## Admission Is Evidence, Not Global Finalization

Admission remains transcript-bound:

1. An inviter publishes signed intent and allocates the prospective teammate's team-local identifier.
2. The invitee signs acceptance with fresh device keys, binding those keys to the proposal and nonce.
3. The inviter publishes the completed transcript and their own acknowledgment.
4. Other participants may publish typed acknowledgments, objections, or later interaction attestations in their own clones.

No finalization record turns Bob on for every participant.
The existing implementation's `finalization` event is better interpreted as evidence that the inviter observed their configured ceremony complete, not as a globally authoritative state transition.
Each participant decides whether and when the accumulated evidence is sufficient for local effect.

Concurrent governance evidence does not automatically invalidate an admission proposal.
The proposal remains an authentic claim relative to its declared causal context.
A participant may decline to accept it because relevant concurrent evidence exists, may accept it provisionally, or may carry both branches until later analysis.

Participants may publish and acknowledge team-policy claims describing risk/availability profiles for automatic local behavior.
A policy claim is itself Constitution evidence rather than a centrally enforced setting, so unresolved disagreement about policy may also split the team.
A recoverable profile may give one explicit inviter acknowledgment immediate local effect and rely on visibility, detection, and whatever repair the affected applications actually support.
A guarded profile may wait for additional independent acknowledgments before activating new standing or distributing future key material.
A profile may also consume witnessed receipts — for example, giving a low-stakes change local effect after enough teammates had a witnessed opportunity and no objection is visible to that clone at a named evidence basis or explicit local cutoff.
That is a local availability choice, not proof that nobody objected; delayed or previously hidden evidence may change the projection and trigger repair.
Such a profile also inherits the receipt's weakness, because it acts on unverifiable client self-reports about human attention.
Policy chooses local effect; it never reinterprets the record: a witnessed receipt plus locally observed silence remains exactly that in the retained evidence and never becomes acceptance.
These profiles guide local effect; they do not create a server-enforced global result.
Which evidence a profile may consume, and on what thresholds, is configurable team governance — a domain expected to grow.
The fixed protocol layer preserves typed meanings, authenticity checks, causal references, inspectability, and adopted evidence.
Implementations should impose a non-configurable floor only where an action can cause unrecoverable damage, such as disclosing fresh key material; the exact floor remains open.

Admission, automatic Core integration, and authority to represent a team externally are separate claims.
Membership must not imply legal agency or unrestricted constitutional authority.
Initial berth integration expectations may accompany an invitation as signed proposals, but accepting Bob as a teammate does not silently authorize Bob to admit others, exclude teammates, alter team policy, or bind the team to an external obligation.

## Departure, Exclusion, Repudiation, and Ratification

At the immutable evidence layer, an ordinary departure and a mistaken admission both contain an admission followed by later signed claims.
Their social interpretations differ.

- A **departure** or prospective exclusion says that an analysis should stop recognizing Bob after a stated causal point while preserving the standing attributed to him before that point.
- A **repudiation** names the disputed admission and says that its author declines to treat that admission as having conferred legitimate standing.
- A **ratification** accepts a specific Bob-originated act even if Bob's general standing is disputed.

There is no protocol-wide instant at which Bob is “really repudiated.”
Dave publishes a repudiation in Dave's clone.
Alice and Carol may acknowledge it in theirs.
Bob or another participant may reject it.
If the disagreement cannot be reconciled, the team has split; preserving that fact is preferable to fabricating consensus.

A local analysis that accepts repudiation may remove Bob-derived standing from its current projection and ask affected applications what repair they can support for the disputed interval.
The Constitution does not by itself attribute Git or application changes to Bob.
It must not erase Bob's signatures, Alice's admission, prior acknowledgments, or evidence that people relied on them.
The precise default signer set and acknowledgment policy for repudiation remain open because any fixed answer would be only one analysis of the DAG.

## Authority and External Reliance

Authority is scoped evidence that can strengthen or weaken through time.
The fact that Bob appears as a teammate does not by itself prove that Bob may sign contracts, spend funds, admit other teammates, or exercise every application's sensitive capability on the team's behalf.

Where Small Sea represents such authority, it should use explicit signed delegations that name scope, causal context, and any required acknowledgments.
An external counterparty may retain a signed receipt showing what delegation it relied on.
A later repudiation cannot delete that receipt or decide the legal effect of an external contract.
The Constitution can provide evidence for later social or legal analysis; it is not a court or a universal agency oracle.

## Record Families

The target evidence graph needs concepts in the following families.
This catalog does not yet commit every concept to a particular SQL table.

- **Roots and device evidence:** team genesis, teammate keys, device links, device revocations, and prepared-recovery use.
- **Admission evidence:** inviter proposal, invitee acceptance, completed transcript, and participant acknowledgments.
- **Duration and relationship evidence:** typed acknowledgments, bounded interaction attestations, and scoped delegations.
- **Visibility evidence:** acknowledgments of a record and its declared causal closure, unresolved observations, and head-gossip testimony.
- **Integration evidence:** per-teammate, per-berth integration expectations and signed proposals or endorsements.
- **Policy evidence:** risk/availability profiles and acknowledgments of the local behavior they recommend.
- **Disagreement evidence:** objections, departures, exclusions, repudiations, reconciliation statements, and selective ratifications.
- **Operational testimony:** staleness observations and other claims that are evidence but do not themselves declare finality.
- **Identity claims:** display-name and teammate-unification claims whose personal content is separable.
- **Operational announcements:** teammate berth storage announcements, whose domain-specific selection rules are not constitutional standing.

Existing `key_certificate`, admission, `integration_mode_change`, and storage-announcement tables are implementation inputs to this target rather than proof that their current projections or lifecycle names are final.

## Direct Identity Payload Is Separable; Metadata Remains

The evidence DAG carries a governance skeleton: pseudonymous team identifiers, public keys, causal references, typed action identifiers, and hiding commitments needed to preserve signed meaning.
That skeleton is not free of personal information.
Stable pseudonyms, device counts, event timing, relationship edges, delegations, and the existence of disputes may identify people or reveal sensitive social structure even after direct payload is removed.
Display names, real-world identity material, free-text reasons, encounter details, and similar personal content must not become required inputs to structural verification or authority analysis.

A PII-bearing record therefore separates:

- a signed, hiding `*_commitment`; and
- a `*_payload` plus commitment opening that is never required to verify the signed record and may be encrypted to a window or dropped.

A bare hash of a low-entropy name is not hiding.
The commitment construction, opening format, and optional encryption-window scheme require separate cryptographic analysis.

Dropping a payload is intentionally irreversible.
The retained record may later prove that its signers committed to a presented payload and opening, but it cannot reconstruct content that has fallen out of the live-data window.
Every structural or authority analysis must produce the same answer whether the separable payload is present, encrypted, or absent.
Every record family must also justify the indirect metadata it adds to retained evidence and consider whether the fact can remain local, pairwise, less precise, or unpublished.

## Retained Evidence and the Live-Data Window

Every current Core database snapshot produced by a clone carries the complete set of Constitution objects that clone has adopted.
Adoption is a record-level transition into the live Core database, not merely fetching a Git commit or attempting a Git merge; its exact staging and atomicity mechanism remain open.
Adoption commits the clone to retention; it does not by itself mean that any analysis accepts the object or gives it local effect.
An adopted object and its declared causal closure are non-prunable in later states produced by that clone.
Fetched-but-unadopted authentic input remains subject to explicit resource policy; it does not gain durable replication merely by arriving.
Constitutional interpretation must never depend on checking out an old Git tree whose blobs may have dehydrated.

Non-pruning is an obligation on a well-behaved implementation, not a property any peer can check.
No participant can distinguish a clone that never adopted a record from one that adopted it and later dropped it, because both publish the same absence.
An analysis must therefore treat retention as something a clone asserts about itself, and treat a missing record as unavailable rather than as proof that it never existed.
Concluding anything from absence is reasoning about the analyzer's own evidence, never about another clone's history.

This is not physical immortality or a global availability guarantee.
If every device, cloud account, and backup holding an object disappears, the protocol cannot recover it.
Nor is there a globally complete Constitution set: honest participants use sync, gossip, and human coordination to move their independently held sets toward greater visibility and practical consensus.

Core is a SQLite database carried in Git, so a peer's records arrive inside a merged tree rather than as a separable offer.
Declining to adopt is therefore an active operation: the merge must produce a tree that deliberately omits rows a parent commit contained, which makes the result depend on local intake state at merge time rather than on the merged inputs alone.
Whatever a clone fetched, parked, or declined must also survive restart outside the live Core database, or the next merge silently re-decides it.
The staging, atomicity, and parked-state representation this requires remain open.

Git commit identities and parent relationships are also retained for bookkeeping and repair.
Non-constitutional application content and separable PII payloads may fall out of the shared rehydratable history after the configured live-data window.
That window is not an erasure guarantee because anyone who already received content may keep it.

The distinction is categorical:

- locally adopted constitutional objects needed to authenticate evidence or reconstruct its causal relationships remain available in later states of that clone;
- ordinary historical blobs and separable payloads may dehydrate;
- no compaction operation rebases, resets, or replaces the Git commit DAG.

## Versioned Projections and Post-Hoc Analysis

There is no single `constitution_projection` that deserves to be called the team state without qualifications.
Useful analyzers may answer different questions over the same DAG, including strict guarded standing, one-participant local acceptance, historical authority at a named frontier, repudiation-aware standing, interaction-weighted confidence, or forensic reliance during an incident.

An operational analyzer may still produce one canonical state for ordinary use.
Its result must be invariant to database row order and to reordering of causally independent compatible evidence.
Causally ordered records follow their type-specific transition rules.
Concurrent incompatible records produce a deterministic explicit conflict value or set unless the named policy supplies a resolution justified by the content of the evidence.
Representation-derived tie-breaks are never such a justification.
Identifier order, timestamps, table order, and arrival order remain representation only, and naming a policy does not launder them into authority.

The canonical state has a state digest.
An implementation may also maintain an ephemeral local revision that advances only for changes the named analyzer treats as state-changing, so evidence that folds to the same state does not create useless cache or display churn.
That revision may reset after restart or rebuild and must not appear in signed records or durable decision bases.
It is not a global clock, a cross-clone coordinate, or a way to order competing continuations.
The digests are the durable identifiers that can be compared across clones.

An analyzer must declare:

- its stable name and semantic version;
- the evidence subset or frontier it considered;
- the local acceptance and acknowledgment inputs it treated as effective;
- the policy or risk profile it applied;
- unresolved dependencies and quarantined inputs that limit the result.

A reproducible decision basis records at least:

- the analysis name and version;
- a digest of the evidence frontier and required causal closure;
- a digest of local acceptance and policy inputs; and
- a digest of the resulting canonical projection.

The projection digest alone is insufficient because different evidence closures may currently produce the same roster while supporting different later authority or repudiation analyses.

Cached roster and device tables may still be maintained for a named default local analysis.
They must be invalidated or rebuilt when relevant evidence or local acceptance choices change, and their provenance must be inspectable.

## Schema and Implementation Status

The current codebase implements pieces of the older single-lineage model, including mutable projections, an inviter-published admission `finalization`, default `quorum = 1`, and blanket proposal invalidation after governance changes.
Those behaviors are implementation state, not retained architectural commitments.

The project is pre-deployment research.
When the evidence-DAG schema is implemented, existing development databases may be deleted and recreated rather than migrated through compatibility shims.
Schema/version markers remain required so future deployed formats can evolve deliberately.

## Deliberately Left Open

- The exact multi-head causal-frontier encoding and predecessor representation.
- The exact technical origin reference, the demotion or renaming of `team_id`, and operational namespace changes after a durable split.
- The versioned projection-fingerprint format and analyzer-evolution rules.
- Visibility-acknowledgment records, declared-closure verification, and head-gossip protocol.
- Resource limits for authentic but abusive evidence at the publication and Core-adoption boundaries, including operator-visible override.
- The adoption mechanism itself: a policy-aware merge that can decline rows arriving inside a merged tree, and durable parked-state storage outside the live Core database so intake decisions survive restart.
- The concrete typed acknowledgment, interaction-attestation, repudiation, ratification, reconciliation, and delegation schemas.
- Named risk/availability profiles, how a team records non-binding policy expectations, and the minimal hard floor for unrecoverable actions.
- The signed app-to-Core basis-anchor record, retrieval path, and privacy-preserving commitment shape.
- Human-intent, user-presence, and in-person key-comparison ceremonies.
- The default local analyses exposed by Manager and Hub.
- How Bob-derived constitutional authority is identified after a repudiated admission without pretending one analysis is universal.
- The hiding commitment, optional PII encryption-window constructions, and indirect-metadata threat model.
- Recovery anti-replay and rollback mechanics.
- The generic Cod Sync forward-restoration mechanism and application-declared repair capability levels.
