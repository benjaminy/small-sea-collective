# Candidate contract: sibling repair from durable selection evidence

Historical terminology: the singular selection and public ordering requirements below are being reassessed under the [multiple-location candidate](contract-multiple-locations.md).
Frozen content and signing/selection separation remain relevant; the old representation is not a runtime requirement.

Branch: `issue-238-shared-berth-changes`.
Status: historical Move 2 model contract, not an implementation handoff.
The preceding decision ledger and open questions are preserved in [the historical plan](plan-single-route.md).
Move 2 chose separate signing identities; Move 3 supports public counter ordering; advisory public predecessor links remain preferred but undecided.
Move 7 found no durable completion marker in the current allocation or local route report.
Item 5 below remains an unvalidated candidate assumption; the observed runtime repairs by repeating materialization, and the design has not decided whether to retain or replace that assumption.

## Terms

These names are for this contract.
They do not describe current tables, and current tables do not constrain them.

- **Allocation.**
  A grant of storage under one provider account.
  Choosing a different account or a different location creates a different allocation.
- **Selection.**
  A participant's choice of the route for one berth, identified by a selection ID and naming its predecessor selection.
- **Route content.**
  Everything a recipient needs to reach the storage: protocol, endpoint URL, final location, and any account-derived value that would otherwise be read from separately changing state.
- **Attestation.**
  A signed statement by one currently trusted device that a selection, with exact route content, is the participant's selection.
- **Delivery.**
  Arrival of an attestation at a recipient, at an unrelated time and in an unrelated order.

## Durable evidence

Repair rests on one shared record per berth, adopted through NoteToSelf, holding:

1. the selection ID,
2. the predecessor selection ID,
3. the allocation ID the selection uses,
4. the exact frozen route content, and
5. a marker that provider materialization finished for that allocation.

The frozen content is the evidence, not a pointer to state that can change underneath it.
An account-only change that alters a reachable value is a new selection, because item 4 differs.

## What a sibling may do

A currently trusted device that has adopted the evidence may sign an attestation over items 1, 2 and 4.
It needs nothing from the interrupted device and no communication with it.
Signing is not a selection: it creates no successor, consumes no allocation, and does not require reallocating storage.
A device that has not adopted the evidence may not attest to it.

## What a recipient may conclude

- **From one attestation.**
  That some trusted device of that participant asserted this selection with this content.
  The recipient may route there provisionally.
  It may not conclude that the storage is reachable, that siblings agree, or that no successor exists.
- **From several naming the same selection with identical route content and predecessor.**
  Equivalent duplicates.
  No contradiction, and no added confidence about reachability.
  Which one is retained does not matter, because the routed content is the same.
- **From several naming the same selection with differing route content or predecessor.**
  A contradiction that must stay visible after both arrive, in either delivery order.
  It may not be resolved by ranking attestation identities, signer identities or signing times.
- **From attestations naming different selections.**
  Order by the succession information inside the evidence.
  Signing and delivery must not decide this, per the ledger entry earned by the Move 1 witness.

## The comparison this contract sets up

Both candidates can express the evidence above.
Compare the coordination each requires and the recipient rules needed to preserve and interpret evidence.

Separate attestation identities let the signer mint its own identity, so repair needs only adoption of the evidence.
Ordering must then come entirely from the selection and succession fields in the payload, never from the identity.
Duplicate attestations are an expected, benign outcome.

An identity reserved at selection time makes the two siblings' attestations share one identity.
That does not inherently erase contradictions: a recipient can retain multiple signed payloads under that identity and compare them.
The model must give this candidate that behavior and permit multiple signers, rather than assuming collisions overwrite or the reservation fixes a signer.
The Move 2 model found that this retention behavior is a requirement rather than a detail:
when the recipient keys retention by attestation identity alone, the reserved candidate loses one of two contradictory payloads and the survivor is decided by delivery order.
Independent identities avoid this collision during ordinary sibling repair when each signing act receives a fresh ID.
They do not by themselves preserve contradictory payloads when a signer reuses an attestation ID.
The reused-ID control preserved in `models/model_sibling_repair.py` demonstrates that limit; detection of conflicting payloads under one reused identity remains an obligation.
Fixing a signer would reintroduce dependence on one specific device, but it is not required by reservation itself.
Both candidates need payload comparison to distinguish equivalent attestations from contradictory claims.

Move 2 chose separate attestation identities because they represent signing acts independently.
Both candidates can satisfy the modeled properties with suitable retention; reservation was not rejected as impossible.

## Assumptions to validate later

These are obligations, not established facts.
Model success would not establish any of them.

- NoteToSelf publishes and adopts the evidence record as a unit, so a sibling never adopts partial evidence.
- A sibling can tell "I adopted this evidence" apart from "this route was reachable".
- Predecessor evidence survives a selection change.
  Today it does not: `resolve_berth_cloud_allocation_intent` deletes the berth's allocation row before inserting the replacement (`packages/small-sea-manager/small_sea_manager/provisioning.py`), so nothing retains the superseded selection.
- Current announcements carry no selection or succession field, and `select_effective_teammate_berth_storage` ranks solely on `announcement_id`.
  This contract requires evidence that does not exist in the runtime yet.

## What this contract does not do

This historical contract proposes no runtime schema.
Its unit-adoption and completion assumptions still require design and runtime validation; later decisions and their reopening conditions belong to the plan.
