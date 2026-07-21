# Constitution Core Simplification Design Record

## Context

This branch began as an envelope migration for `key_certificate` and `teammate_berth_storage_announcement`.
Design review then expanded the target into a complete governance architecture: typed acknowledgments, admission and repudiation semantics, named analyzers, projection fingerprints, adoption states, retention duties, risk profiles, visibility receipts, living-continuation rules, and application repair links.

Those questions are real, but treating them as one core protocol would freeze policy before the project has enough operational evidence to justify it.
It would also make independent implementations agree on concepts that do not affect cryptographic interoperability.

## Decision

The Team Constitution core is a decentralized, content-addressed DAG of signed event envelopes.
The core verifies canonical bytes, event IDs, signatures, technical-origin binding, and parent links.
It reports missing ancestry without deciding what an event means or whether it should have local effect.

Event payload semantics are versioned extensions.
Membership, admission thresholds, roles, key distribution, local heads, merge decisions, projections, retention, quarantine, recovery, repair, and UI behavior live outside the core.

The core makes narrow security claims: integrity, authorship by a key, replay separation, and declared ancestry.
It does not prove human intent, key authority, complete disclosure, consensus, membership, availability, or the safety of an extension's policy.

## Decisions retained from review

- Full content digests are preferable to truncated identifiers.
- Canonical signed bytes and exact version/type pinning are required.
- Technical-origin binding prevents cross-team replay but grants no authority.
- Parent references, not timestamps or database order, create causal structure.
- Unknown extension payloads must remain structurally verifiable and relayable.
- A bad digest, signature, encoding, or origin is invalid.
- A valid event with missing parents is incomplete rather than forged.
- Git authorship is not Constitution-event authorship.
- Hostile-input-safe parsing and bounded event objects are core concerns.

## Decisions withdrawn from the core

The core does not define an accepted committee, admission quorum, finalization, current roster, automatic Core integrator, canonical head, typed acknowledgment taxonomy, witnessed receipt, repudiation, ratification, analyzer, policy profile, projection digest, adoption transition, non-pruning promise, dormancy rule, split namespace, or repair manifest.

Any of these may return as an extension when a concrete use case and threat model justify it.
The branch's Git history preserves the discarded exploration; it is not active architectural guidance.

## Consequences

The shared envelope can evolve independently of Manager's current admission policy.
The current proposal, endorsement, quorum, and `finalization` behavior remains explainable as one application extension without becoming the definition of team membership.

Before freezing the first core format, the project must still decide exact canonical encoding, algorithms, signer-key representation, origin derivation, event-ID coverage, object bounds, and missing-parent retrieval.
Those are the questions on which independent core implementations must agree.

## Application-facing basis bookmark

A later pass defines the smallest application-facing layer in `architecture.md` rather than in the core event spec.
It lets an application cite a local Constitution view without interpreting the graph or receiving a policy projection through the application API.

- **Basis object.**
  An unsigned canonical object containing a basis-format version, one technical origin, and the minimal set of active tip event IDs.
  It names the structurally verified event view selected locally by Manager without asserting membership, authority, acceptance, or completeness of disclosure.
- **Basis ID.**
  A full content digest over a basis-specific domain separator and the canonical basis object.
  It provides stable identity, comparison, indexing, and optional deduplication without serving as a lookup key for a separate object.
- **Application contract.**
  Manager computes the basis from its active Core view, and the Hub returns the canonical object as opaque bytes within the application's team-scoped session.
  The application embeds those exact bytes in the data that cites the view.
  Apps do not interpret the tip set or ask the generic basis operation for a roster or policy result.
- **Availability.**
  The bookmark travels with its citation, so resolving a separate basis ID is unnecessary.
  A holder may still lack events named by the basis; that makes the view incomplete locally rather than false.
  No basis registry, merge rule, retention promise, or cache-coordination protocol is part of the design.
- **Bounded views.**
  A large number of concurrent Constitution tips is a partition oddity or an attack, so the bound lives in Manager's integration policy rather than in the basis operation.
  Beyond the policy's tip bound, excess branches are parked — visible, reversible, never reported as verification failure — and any descendant with parked ancestry remains parked.
  Manager assigns each newly core-verified event a handling state after its parents, with concurrent ready-event ordering left local, rather than activating a received batch atomically from its final tips.
  A backlog wider than the bound therefore parks a branch before a descendant merge is considered.
  No received event unparks that branch: a flooding device can immediately publish its own merge, so automatic reactivation would hand the lever back to the attacker.
  Unparking is a local acceptance decision — a merge naming parked tips integrates as an ordinary collapse on devices that never parked those branches and is surfaced through the Hub as a proposed reconciliation on devices that did.
  Compromise recovery usually needs no unparking at all: the team removes the device on a surviving branch and continues, leaving the flood parked.
  Manager makes parking observable to the Hub so the Hub can notify the user without adding incident state to the basis or application contract.
  The basis operation never refuses or truncates the remaining active view.
  The policy bound limits locally produced bases, while the basis format fixes a maximum tip count — over fixed-size tip IDs this also determines a maximum encoded size — for untrusted bookmarks.
  Recipients enforce the byte bound before canonical decoding and the tip-count bound during decoding before allocating or consuming the entries.
  The policy may use a lower active-tip bound but must not exceed the format's tip limit.
- **Metadata disclosure.**
  The canonical basis has no issuer, device, application, or timestamp field.
  Its origin and tips are visible to every holder of the application data, and the application event may name the author who selected that view.
  Holders may correlate the tips with the Constitution DAG to infer roster, recovery, or other personal events in the view.
  Unlike a replicated registry, the bookmark discloses a view only along the application path that cites it.

The bookmark does not impose retention of every named event.
Event availability remains an explicit storage or extension concern and can be strengthened later without changing the basis encoding or app API.

Two refinements from review:
a basis is computed over the *active* view, so devices with identical stored events but different parking choices compute different bases, deliberately;
and a basis names a view rather than a moment of use, so freshness between issuance and use is the application's responsibility, with optional Hub freshness checking recorded as an open question.
The resource-safety wording was also narrowed:
branch neutrality under deliberate flooding is unachievable by any bounded intake, so the enforceable requirement is that parking is never reported as verification failure and stays visible and reversible.

### Alternatives considered and rejected

- **Returning an event ID for a single-tip view.**
  Rejected because one untagged value would have two object types and callers would need special-case resolution.
  The uniform basis object is simpler and leaves internal optimization open.
- **Embedding only a basis ID.**
  Rejected because a fixed-size ID would require a second mechanism to deliver and retain the object it names.
  A replicated registry introduces merge, resource-limit, retention, and disclosure rules unrelated to naming a view, while attach-once delivery introduces cache and peer-state coordination.
  Carrying the small bounded object with each citation keeps the reference self-contained; applications may deduplicate it locally if repetition matters.
- **Returning the frontier to applications.**
  Rejected because it makes every app understand Constitution graph representation and variable-length multi-head views.
  The canonical basis keeps the tip representation opaque to the application.
- **Returning a roster or policy-projection digest.**
  Rejected because equal policy outputs can arise from different evidence, and the generic application interface must not freeze one team policy.
- **Forcing a single head before producing a basis.**
  Rejected because it couples application liveness to Constitution convergence and cannot faithfully name a legitimate multi-tip local view.
- **Requiring a join event above a tip threshold.**
  Rejected because it recreates the convergence coupling exactly in the stressed case, and under flooding the attacker controls whether the threshold is crossed, turning the cap into a denial-of-service lever on all basis-citing application activity.
  A real partition may also isolate everyone with standing to sign the join.
  An active multi-parent event collapses its active parents, but a descendant with parked ancestry stays parked so it cannot reactivate a contained branch.
  Convergence involving parked ancestry is a per-device acceptance decision, while richer join or explanation events remain deferred extension machinery.
  The synchronous bound is integration-policy parking, as stated above.
- **Standard evaluator, join, and time-attestation machinery.**
  Deferred until concrete extensions require it.
  Those mechanisms can consume a basis later without changing the generic application contract.
- **Naming the object.**
  Considered `cut` (too jargon-y), `constitution state` (collides with the "one globally accepted state" the docs say does not exist), and `constitution snapshot` (collides with Cod Sync's materialized-repo "snapshot" and with the Git snapshot layer, precisely where the two DAGs must stay distinct).
  Chose **basis**: plural-natural, cannot be misread as authoritative, and reads for the role ("a decision's constitutional basis").
