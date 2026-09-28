# Integrating Core: the central decisions (#226)

Four Codex agents (gpt-6-astra, medium) each examined one angle: content classes, evidence not verdicts, the convergence mechanism, and adversarial and partial failure.
Their raw reports were not kept; this record carries their conclusions.
They read the `voyage-2026-09-26` checkout at `1990e9d`, which predates the admission package on local `main`, and could not reach GitHub.
Their citations are otherwise checkable.

This note narrows the decisions; it is not the design.

## The one decision the rest hangs on

**What must teammates' Cores agree on: the same signed records, the same derived state, or the same bytes?**

All four agents arrived here independently.

- *Same bytes* adds SQLite serialization rules and buys no extra agreement. NoteToSelf already treats equal rows in different files as clean (`note_to_self_sync.py:198`).
- *Same derived state* (membership, roles, who holds which berth) needs every device to use the same anchor and policy; the evaluator takes the anchor as input (`berth_authority.py:270`), and architecture.md:90 already allows identical events with different active bases.
- *Same signed records* converges by union: if every device eventually holds every authentic record and never deletes one, their record sets match.

Recommendation: **the shared Core is a grow-only set of signed, immutable records; teammates converge on that set.**
Membership, roles and authority are views each device computes from the records it holds.
This is the "evidence, not verdicts" move, and it is relevant here: it makes convergence mechanical and leaves judgment local.
"Identical Core DBs" then means identical shared-record tables; derived tables are rebuilt from records or kept outside the shared file.

## Decisions that follow

1. **What gets in.** Store a record when it is well-formed and its signature checks, whether or not its author is currently authorized; authority is a view question.
   A record whose signing key is not yet known waits in a pending area and is re-checked when more records arrive.
   Same id with different content is refused (existing precedent: `provisioning.py:3384`).

2. **Conflicting authentic records.** Keep both; the view reports ambiguity and affected operations pause for a person.
   The evaluator already does this for competing mode records (`berth_authority.py:313`).
   No winner rule.

3. **Unsigned shared rows.** These break "grow-only signed set" today:
   `team_setting`, `app`, `team_app_berth`, `berth_role`, `teammate`, `team_device` (projections with receiver-local ids and clocks: `provisioning.py:1660`, `5034`, `5678`),
   `device_prekey_bundle` (replaced in place with an unsigned timestamp: `provisioning.py:791`),
   `admission_revocation` (says "local" but is written to the shared file: `core_other_team.sql:158`, `provisioning.py:7441`),
   unsigned proposal labels (`core_other_team.sql:66`).
   Each must become a signed record, move to device-local state, or be derived.
   This is an inventory with a concrete answer per table.

4. **Deletion.** Removing a teammate deletes certificates and can cascade away signed admission history (`provisioning.py:4785`, `core_other_team.sql:86`).
   Under a grow-only set, removal must become a signed record instead.

5. **Adoption boundary.** One SQLite transaction on the live database, then a git commit recording it, as NoteToSelf does (`note_to_self_sync.py:473`).
   Not a checkout or file replacement.

6. **How much one bad record stops.** Simplest: refuse the whole fetched batch from that source and pause that source only; the rest of the team is unaffected.
   Per-record partial import is deferred.
   A size limit before expensive processing is a later safeguard.

7. **"Finalized."** Not needed for synchronization.
   Admission finalization stays as a policy record (`provisioning.py:1413`); it does not have to mean team-wide agreement.
   Stale or removed signers are handled by the existing plan: current authority only, plus explicit human acceptance of exact past work (bootstrap-trust.md:277).

## Why this suits Small Sea

Small teams and modest constitution churn make a full rebuild of every view from the record set cheap, so no incremental projection machinery is needed now.
The admission package already imports records this way for key certificates and mode changes; integration generalizes it to every shared record from every parked teammate head.

## Owner refinement (2026-09-27)

The Constitution is the part that matters: a signed event DAG stored inside Core, whose old nodes are never deleted.
Other Core data may be less critical and need not match exactly for security; no concrete example is known yet.

This matches the documented destination.
`Documentation/team-constitution.md` already defines the Constitution as a content-addressed DAG of signed events that preserves concurrent heads, carried inside Core but independent of the Git history that transports it.
The DAG itself is not built yet: #173 (freeze the envelope) and #174 (DAG storage, verification, missing parents) are open, and `wrasse_trust/constitution.py:3` calls today's records a transitional shape.

So "grow-only set of signed records" above is really "union of Constitution DAG events", which the design already implies.
Integration of the Constitution is then mostly #174's job; #226 shrinks to the non-Constitution data and the adoption boundary.

Two new questions follow:

- **Sequencing.** Does #228 wait for #173/#174, or does it first integrate today's transitional records (key certificates, delegations, mode changes) by record union, as the admission package does, and migrate later?
- **Tension with the doc.** team-constitution.md says clones need not retain the same event set forever. "Teammates eventually converge" then means on events they both still retain, or the doc changes.

## Decision (2026-09-27): build a minimal Constitution DAG now

Research phase: any Small Sea data may be thrown away, so the byte format need not be frozen.
Build the parts the Files demo exercises; give every deferred choice one version marker so it can change later by discarding data.

**Build now**

1. **Event shape.** Type, payload, parent event ids, signer public key, signature.
   The id is the hash of the signed body.
   Encoding: sorted-key JSON with a `v0` version marker, explicitly not frozen.
2. **Storage.** One grow-only `constitution_event` table in team Core; no delete path.
   Removal becomes an event.
3. **Arrival check.** The id matches the hash; the signature checks with the named key; every parent is present.
   An event with a missing parent waits in a pending table and is re-checked when more events arrive.
   Whether the author was allowed to write it is a view question, decided by each device, not at arrival.
4. **Integration is union.** Read a teammate's parked Core, insert every event that passes, refuse an id that arrives with different content.
   One SQLite transaction on the live database, then a git commit.
   One bad event refuses that source's whole batch and pauses that source only.
   Conflicting authentic events are both kept; the view reports ambiguity and affected work pauses for a person.
5. **First record kinds on the DAG.** Key certificates, workhorse delegations, integration-mode changes: enough for Bob to learn Alice's delegation.
   Views such as berth authority are computed from events.

**Deferred**

- Freezing the byte format; choosing final digest and signature algorithms.
- Rules for a team's first (genesis) event and technical origin.
- Size limits on events, parent sets and payloads.
- Requesting missing parents from teammates directly (Core sync already carries whole histories).
- Active-head limits and parking excess heads.
- Retention and garbage collection; the team-constitution.md tension about clones not keeping the same events forever.
- Moving the remaining record kinds (admission records, berth storage announcements, others; #165).
- Non-Constitution Core data and its convergence rule (#226's remainder).
- The admission package sending events instead of records.

**Effect on issues:** #174 is built as this slice, with its deferred parts listed.
#173's format freeze waits.
#228 becomes "integrate Constitution events by union".

## Earlier questions for the owner (answered by the decision above)

- Is "same signed-record set" what you mean by identical Core DBs, with derived tables per device?
- Is pausing on conflicting authentic records acceptable as the converged outcome?
- Refuse a whole batch on one bad record, for now?

## Plan for tasks 66–69 (draft for review)

**Wrap, don't reshape.** Each existing signed record (key certificate, workhorse delegation, integration-mode change) keeps its current format and signature.
Its author also appends a Constitution event whose payload is the whole signed record (hex for binary fields), and whose event signer is the same key that signed the record.
Collapsing the double signature into one is deferred with the byte format.

**Existing tables become projections.** `key_certificate`, `workhorse_delegation` and `integration_mode_change` stay, so their readers (Manager, `berth_authority`, the Hub's `backend.py:1887`) do not change.
A projection function per event type writes the row for each newly stored event: `apply_event(conn, event)` in a new `small_sea_manager/constitution_projection.py`.
`add_event` must report every newly stored event, including pending events it promoted, so each one is projected exactly once.

**Parents.** A new event's parents are the current heads: stored events that no stored event names as a parent.

**Only the author can create the event.** Where a device stores a record someone else signed (acceptance storing the inviter's membership certificate; admission-package import), it cannot sign a wrapping event.
Transitionally those paths keep writing projection rows directly; the author's event reaches them later through integration (task 69).
Duplicate projection writes must be idempotent (the row already exists with the same content).

**Removal.** Removing a teammate appends a `teammate_removed` event; its projection deletes that teammate's certificate rows, as today.
The events themselves stay.

**Integration (69).** For each source from `TeamManager.list_core_source_heads`: read `core.db` at the parked head via git into a temporary file, list its stored events, and add each to the live Core in one `BEGIN IMMEDIATE` transaction, projecting newly stored events; then commit `core.db` to git.
Any invalid event or id conflict rolls back that source's whole batch and reports a typed refusal for that source; other sources continue.
Persisting a pause is deferred; the refusal is returned and shown.
Non-Constitution data is not integrated.

### Corrections after review (gpt-6-astra, 2026-09-27; report in scratch, summarized here)

1. **Acceptance authors a certificate too** (`provisioning.py:6346`, `6368`, `6467`): wrap there. Also wrap in `delegate_workhorse_key` (`5384`), not only `ensure_signing_is_set_up`.
   Before projecting, check the inner record: its own signature verifies, and its signer key equals the event signer.
2. **Foreign keys block projection.** `key_certificate` and `integration_mode_change` require teammate and berth rows that no event in this slice supplies (`core_other_team.sql:190`, `228`).
   Drop those foreign keys; signed records stand on their own, as `workhorse_delegation` already does.
3. **Removal is deferred from this slice.** A delete-on-arrival projection would let any signer delete, and arrival order could restore a removed teammate's certificate.
   Removal stays a local projection delete as today; integrating removal (evidence kept, authorized removal computed in the view) is later work.
   Known gap: integration can re-project a removed teammate's certificates.
4. **Git capture after the SQLite commit** must take a fresh writer reservation while git reads `core.db` (`note_to_self_sync.py:159`, `421`).
   A git failure after the rows committed is reported separately from a refused batch, and recording is retried without new events.

Also: the parked `core.db` is read at the captured SHA with `Repo.blob_at` (`cod_sync/repo.py:677`), opened read-only; malformed databases or payloads become a refusal for that source.
