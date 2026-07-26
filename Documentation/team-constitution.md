# The Team Constitution

Status: architectural direction, not a frozen wire format.
The current code implements parts of an older admission design.

## Purpose

The Team Constitution is a decentralized, cryptographically linked DAG of signed events.
Its job is to preserve inspectable evidence about a team without requiring a team server or one globally accepted state.

The Constitution protocol answers narrow mechanical questions:

- Are these the exact bytes identified by this event ID?
- Did the key named by the event sign those bytes?
- Which earlier events does this event name as parents?
- Is the event bound to the intended technical origin?
- Is the referenced ancestry present, or is some of it missing?

It does not answer whether the signer was entitled to act, whether Carol has joined the team, whether Alice should merge Carol's work, or which concurrent head is canonical.
Those are extension and local-policy questions.

This is the protocol's narrow waist.
The design should resist adding a field or rule merely because one admission, membership, recovery, or repair policy currently wants it.

## The Core Object

The smallest plausible Constitution event contains:

| Field | Core meaning |
|---|---|
| protocol version | Selects the envelope encoding and cryptographic rules |
| technical origin | Prevents an event from being replayed as an event of an unrelated team history |
| parent event IDs | Hash links to the earlier events on which this event claims to build |
| extension type and version | Selects semantics that the core does not interpret |
| signer key | Identifies the key used to verify the signature |
| payload | Opaque, bounded bytes interpreted by the extension |
| signature | Authenticates the canonical envelope and payload |
| event ID | Content digest of the version-defined event bytes |

The wire format still needs a small threat-model review before it is frozen.
In particular, the design must decide whether the signer key is inline or content-addressed, whether the origin is a genesis-event digest or a signed random value, and exactly which bytes the event ID covers.
Those are core questions because independent implementations must agree on them.

A creation time may be useful metadata, but it is not causal order or authority.
If it appears in the signed envelope, the core verifies only that it was signed; it does not trust the clock.

Parent IDs form a set whose canonical encoding is deterministic.
An event may have multiple parents.
That represents convergence in the graph without implying social agreement with every ancestor.
Parents belong to the same technical origin; an extension may place cross-origin references in its opaque payload instead of merging two team DAGs at the core layer.
No timestamp, UUID order, database row order, Git commit order, or arrival order selects a winning parent or head.

## Core Verification

A core verifier performs only version-defined structural and cryptographic checks:

1. Decode the canonical envelope and enforce its per-event bounds.
2. Recompute the event ID.
3. Verify the signature with the named signer key.
4. Verify technical-origin binding and the canonical parent set.
5. Report which parents are present and which are missing.
6. Reject cycles or other impossible graph structure when assembling a closure.

An event with a bad digest, bad signature, invalid encoding, wrong technical origin, or impossible graph structure is not a Constitution event under that protocol version.
An otherwise valid event with unavailable parents is incomplete, not forged.
It may be stored and revisited if those parents arrive later.

The core does not consult a roster to verify the signature.
It proves that a particular key signed an event.
An extension may then decide whether that key is relevant, delegated, revoked, compromised, or unknown for its purpose.
Keeping those questions separate avoids making one membership policy a prerequisite for parsing history.

## Local Handling and Resource Safety

Core verification and local effect are separated by observable handling states.
The state names are implementation vocabulary rather than fields in the signed envelope:

1. **Invalid input** fails canonical decoding, content-ID, signature, origin, parent-set, or graph checks.
   It cannot become a Constitution head or acquire extension effect.
2. **Ancestry-incomplete input** has a valid envelope but lacks one or more named parents.
   It may be held for later completion, but no conclusion requiring the missing closure may use it.
3. **Core-verified event** has a valid envelope and an available, valid same-origin parent closure.
   This says nothing about the signer's authority or the payload's effect.
4. **Locally active or parked event** records whether this device selected a core-verified event into its active Constitution view.
   Parking is a local intake or operator decision, not a declaration that an authentic event is malformed.
5. **Extension effect** is a separate decision made by an extension and local policy over an explicit active view.

An active view is ancestry-closed.
A local integration policy assigns each newly core-verified event a handling state only after assigning states to its parents; ordering among concurrently ready events remains a local choice.
It does not activate a received batch atomically from the batch's final tips.
A core-verified event with any parked ancestry therefore remains parked until an explicit reconciliation selects a new active view.
No property of a received event — including naming parked tips as parents — reactivates a parked branch; unparking is a local acceptance decision.

A receiver must bound work before an event earns any local effect.
The core format bounds individual event and parent-set sizes; implementations must additionally bound unresolved input, closure traversal, storage, and extension execution.
Exact budgets are local implementation or transport-profile choices rather than team policy encoded in every event.

Crossing a resource limit may decline or park new input.
Under deliberate flooding no bounded intake can be perfectly neutral between branches, so branch neutrality under exhaustion is a design intent rather than a guarantee.
The enforceable requirement is narrower:
resource-based parking must never be reported or treated as verification failure, and it must remain visible and reversible rather than silently promoting whichever branch fit the budget to authoritative status.
A valid signature never creates an entitlement to storage, ancestry retrieval, forwarding, or effect.
Unknown extensions remain mechanically verifiable, but preservation and relay are always subject to these local resource limits.

## Local Heads and Partial Views

Every participant may hold a different subset of the DAG and different local refs or heads into it.
Refs are local choices, like Git branches.
They are not consensus state.

If Alice starts fetching and merging events or application changes from Carol, that is a fact about Alice's local behavior.
The core protocol has neither the knowledge nor the authority to declare that Carol has thereby joined the team.
Alice may publish a later event that names Carol's event as a parent, making the causal relationship inspectable, but even that linkage is not a built-in endorsement.

The protocol must preserve concurrency rather than manufacture a winner.
Two events that name the same parent are ordinary sibling events.
A later event may name both siblings as parents.
Participants may also continue on only one sibling indefinitely.

No peer can prove that another peer disclosed every event it knew about.
The core therefore treats a parent set as the dependencies the author declared, not as a complete statement of the author's knowledge.
Gossip, transparency checks, or receipt schemes may be useful extensions, but they are not prerequisites for a valid core event.

## Extension Boundary

Extensions give payloads meaning.
Examples may include admission, device linkage, key rotation, storage announcements, merge requests, delegation, exclusion, or recovery.
The core protocol does not reserve those as mandatory constitutional concepts.

An extension may define:

- its payload schema and semantic validation;
- which signer keys it recognizes and why;
- how it interprets ancestry and concurrency;
- thresholds, roles, ceremonies, and user-presence requirements;
- which local projections or caches it maintains;
- when a Hub or application integrates data or distributes keys;
- retention, quarantine, resource, and repair behavior beyond the core object bounds.

Policy may be local configuration, signed events understood by an extension, or both.
Publishing a policy event does not make it globally binding.

Unknown extension types remain structurally verifiable because their payload bytes are opaque to the core.
A store or relay may preserve and forward an unknown event, subject to local resource policy, without granting it any local effect.
That property is central to long-term adaptability.

Extensions must not weaken core verification or reinterpret core fields.
They also must not describe their own authorization decision as a guarantee made by the Constitution protocol.

## Security Claims

The core DAG can provide:

- content integrity;
- authorship by the holder of a specific signing key;
- technical-origin replay separation;
- inspectable declared ancestry;
- visible concurrency when the relevant events are available;
- deterministic handling of the same envelope bytes across implementations.

The core DAG alone cannot provide:

- proof of human intent or identity;
- proof that a signing key was authorized for a purpose;
- proof that an author disclosed every event they knew;
- prevention of equivocation by an authentic key;
- agreement on membership, authority, policy, or a canonical head;
- confidentiality from an admitted endpoint that chooses to disclose plaintext;
- availability of events that every holder deletes or withholds;
- unlimited protection from storage, bandwidth, or computation exhaustion;
- safe automatic action by an extension with a bad policy.

These are not gaps to fill by default in the core.
They are boundaries that let each extension state and test the security properties its actions require.
An extension that releases fresh key material, for example, needs a stronger and more explicit authorization policy than an extension that merely displays an unfamiliar signed event.

## Relationship to Git and Core

Git carries database snapshots and application histories, while Constitution events carry domain signatures.
Git commit authorship is not Constitution-event authorship.
Synthesized Git merges must not alter an event's bytes, ID, signature, or parent links.

`{Team}/SmallSeaCollectiveCore` is the berth and database in which the current implementation stores Constitution events and related projections.
The event DAG is the protocol artifact.
The SQL layout, Git merge driver, local refs, indexes, projections, and UI are implementations around it.

The protocol does not require every clone to retain the same event set forever.
Implementations should be explicit about what they advertise, retain, park, or garbage-collect, and nobody may infer that an absent event never existed.
Stronger retention promises can be specified by a storage or governance extension after their cost is understood.

The application-facing basis described in [`architecture.md`](../architecture.md#constitution-bases) is an unsigned bookmark carried with the application data that cites it.
It is not Constitution event history and does not require a replicated Core registry or a separate retention promise.
Carrying the basis guarantees that the cited origin and tip set remain identifiable with the application record.
It does not guarantee that every event in the named closure remains available forever.

## Current Implementation

The shared signed envelope exists only in partial form.
Several current record families look up signer standing through mutable membership tables, and the current admission implementation contains proposal anchors, endorsement thresholds, and an inviter-published `finalization` record.
Those are current application-policy choices, not settled core protocol rules.

The envelope migration should proceed only on properties needed by the narrow core: canonical bytes, full content digests, exact version and type pinning, origin binding, signature verification, parent references where the core format adopts them, and hostile-input-safe parsing.
It should not add admission thresholds, effective-membership rules, one accepted frontier, analyzer taxonomies, receipt families, or projection-provenance schemes to the core envelope.

## Open Core Questions

Only questions that independent core implementations must answer belong here:

- What exact canonical encoding and domain separator are used?
- Which digest and signature algorithms does the first version require?
- Is the signer public key inline or referenced by a content ID?
- Is technical origin derived from a genesis event or carried as a signed random value?
- Does an origin have exactly one parentless genesis event, and how is that rule verified?
- What are the maximum envelope, parent-set, extension-type, and payload sizes?
- Does the event ID cover the signature, or only the canonical signed body?
- What minimal negotiation or rejection behavior is required for an unknown protocol version?
- How are events and missing parents requested without confusing transport with acceptance?

Admission policy, committee thresholds, roles, repudiation, ratification, dormancy, living team identity, external agency, and application repair are deliberately absent from this list.
They may become extensions when a concrete use case carries enough weight to justify them.
