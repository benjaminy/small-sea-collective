# Constitution Envelope Migration Design Record

## Why implementation paused

Issue #165 began as a representation migration for `key_certificate` and `teammate_berth_storage_announcement`.
The initial branch plan paired fail-closed full-table governance loading with pre-adoption candidate validation and assumed that later work would construct one accepted constitutional frontier and one normative standing resolver.

Before code changes, design review exposed a more fundamental model.
The Constitution is retained signed evidence that participants may interpret differently, not a replicated state that must eventually converge.
Typed acknowledgments, interaction evidence, local repudiation, selective ratification, duration, and forward application repair are load-bearing parts of that model.
One malformed independent leaf must remain inert without becoming an easy whole-team denial of service, while every conclusion that depends on malformed or missing evidence still fails closed.

The branch therefore stopped before implementing the earlier plan and rewrote canonical documentation first.
No compatibility constraint justified preserving the old single-lineage assumptions in a research project.

## Envelope decisions that survived

The redesign retained the full content-derived SHA-256 `record_id`, signed format version, signed and stored `team_id`, explicit expected-team verification, exact record-type/version pinning, independent identifier verification, and display-only `created_at` semantics.
It also retained the decision not to add interim single-lineage anchor columns to the two migrating record types.

Later review narrowed the meaning of `team_id`.
It remains the current signed technical-origin and replay-domain field, but it is not stable social team identity.
Living identity is refreshed through signed activity and relationships, and durable splits may produce multiple operational continuations with shared ancestry.
The long-term origin representation and post-split namespace design remain open.

The same review required versioned, reordering-resilient analyzers whose reproducible decision basis includes evidence-closure, local-input, and canonical-result digests.
It distinguished visibility of a record's declared causal closure from acceptance, made retention load-bearing rather than arrival-triggered so authentic device abuse can be bounded, acknowledged durable indirect personal metadata, and left Git/application attribution to the application rather than the Constitution or Cod Sync.

A further review round made any numeric projection revision ephemeral cache or display state whose digests are the durable identifiers, reframed dormancy as honestly surfaced information rather than a protocol agency-expiration threshold, restated visibility acknowledgments as accountable claims rather than possession proofs, and aligned the opening retention promise with the then-current load-bearing boundary.
It left the completeness of governance causal-context claims and the exact non-forgettable closure under active discussion; see the NOTES open review threads.

Follow-on discussion confirmed `team_id` as a convenience with no authorization weight, adopted receiver-controlled intake policy with visible local suspension and override, and opened a per-node acknowledgment direction plus a candidate app-to-Core basis anchor.

The frontier question was then resolved: governance causal contexts name the minimum dependencies their meaning requires, and fuller frontier disclosure is deliberate, separate visibility evidence.
Omission is not automatically invalid or proof of equivocation; receipts, head gossip, and basis anchors let post-hoc analyses surface selective, stale, or incompatible patterns for human interpretation.
Retention was restated as clone-relative Core adoption: adopted Constitution objects and their declared causal closures are non-prunable in later states produced by that clone.
This is not physical immortality, universal availability, or a globally complete evidence set, and fetching or attempting a Git merge is not itself adoption.
The app-to-Core direction was kept by narrowing its claim: an app publication may reference a signed Core basis-anchor record, giving analyzers inspectable linkage without proving possession, completeness, or freshness.
Configurable team governance may consume witnessed receipts and locally observed silence for local effect, while the fixed protocol preserves record meanings and imposes hard floors only for unrecoverable actions.
The Constitution DAG was confirmed to be a hash-linked record DAG carried by Git commits, not literal Git commits.

The final review round removed the remaining unverifiable promises rather than restating them.
Non-pruning became an obligation on a well-behaved implementation instead of a protocol property, because never adopting a record and dropping an adopted one produce the same observable absence; no analysis may read absence as proof of non-existence.
Adoption inside a Git-carried SQLite database was recognized as an active merge operation, so declining a row means emitting a tree that omits rows a parent commit contained, and parked or declined state must persist outside the live Core database.
The conflict-resolution escape hatch was closed: a named policy may resolve concurrent incompatible evidence only on grounds justified by evidence content, never by identifier, timestamp, table, or arrival order.
Witnessed receipt was marked the weakest rung and the only evidence no recipient can check, since it is the author's client reporting on its own user.
Adoption joined the question taxonomy, which is now seven questions rather than six.
The `permanent` vocabulary was deleted from retention-sense prose rather than defended by a definition footnote; unrelated senses were left alone.

The `teammate_berth_storage_announcement` migration still preserves its current UUIDv7 selection behavior as an envelope-only slice.
Causal routing and repair remain a separate protocol change.

## Decisions explicitly withdrawn

The branch no longer treats a single accepted Core lineage, one-tip-per-table frontier, globally effective admission finalization, blanket proposal invalidation after concurrent governance changes, whole-view failure for every bad independent row, or one normative authority resolver as target architecture.
Implementation must be re-planned against a multi-head verified evidence graph and small named analyzers with explicit local inputs.
