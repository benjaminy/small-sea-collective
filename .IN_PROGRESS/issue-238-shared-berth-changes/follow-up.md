# Possible work after the discussion

No implementation handoff is ready.
This document retains the earlier two-stage outline and useful validation questions without committing to a protocol, state representation, API or orchestration sequence.
Revise it from the accepted design before assigning implementation work.
These are local notes; no issues or messages have been posted by this document update.

A 2026-09-06 review round recommends narrowing this file to a stub until a design is accepted.
Its "Questions to carry into an eventual handoff" largely restate open questions that already appear in [plan.md](plan.md) and [notes.md](notes.md),
and that duplication grows with every round.
The narrowing is not done here, because deciding what an eventual handoff should ask is a committee call rather than an editing one.

## Tentative implementation split

The earlier scope decision placed design investigation on this branch, one central Core operation in a first follow-up, and remaining integration and user-facing work in a second follow-up.
That is a possible way to bound the work, not a reason to retain the current operation or helper structure.

The first follow-up would implement the chosen protocol in one established-team Core use case and produce runtime evidence for its state, authority, concurrency, conflict-resolution and repair semantics.
Its output would include a concrete contract for the later integration work and an honest account of which paths remain outside that contract.
A successful central use case would not establish system-wide safety.

The second follow-up would apply that contract across creation, invitations, ordinary changes, repair, integration, generic publication and courier delivery, and align reports and documentation.
It would establish how arbitrary app berths in #139 and Core/Files use in #235 share the same semantics.
The full app provisioning UX and Files capstone remain separate work.

Current function names, table boundaries, UUID ordering, publication result types, signed fields and tests do not constrain either follow-up.
Retain or replace them according to the accepted design.
There is no production compatibility or rollout requirement.

## Questions to carry into an eventual handoff

- What exact durable state authorizes each effect, and what can a sibling infer after adoption?
- How are refusal, uncertainty, historical inclusion and current selection distinguished?
- Which provider effects must precede shared evidence, and which failures are retryable without another allocation?
- How does successor authority survive explicit resolution, restoration and delayed actions?
- How are replay, sibling attestation and intentional new selection distinguished?
- What can peers verify, and how do they handle equivalent or contradictory attestations?
- Where do invitation-specific signer requirements belong?
- How do creation and linked-device bootstrap preserve local/offline use without silently selecting new storage?
- Which integration or publication paths could otherwise bypass the authority rules?
- Which user-visible claims require local evidence, shared settlement, actual delivery or provider reachability?

## Runtime evidence to preserve

Turn the schedules in [plan.md](plan.md) into deterministic runtime witnesses once their intended semantics are decided.
Use two installations with distinct trusted device keys and a local provider service where appropriate.
Pause at real operation boundaries; do not replace the authority, publication, integration or selector being examined with a success stub.
Use local mocks or MinIO for provider networking.

The witnesses should cover competing selections; definite and uncertain publication outcomes; historical inclusion with related and unrelated descendants; delayed signing and both delivery orders; clock skew; interrupted work and sibling repair; unavailable or untrusted original signers; differing provider locators; separately changing account endpoints; resolution choosing a refused candidate; return to a previously used location; equivalent and contradictory attestations; generic publication after merge or restoration; and local success followed by delivery failure.
Expected results must follow the chosen protocol rather than today's row counts, identifiers, return types or helper calls.

An eventual integration audit must include creator setup, established-team changes, invitation preparation/export/import, ready-route shortcuts, generic NoteToSelf and Core publication, and the public management interface.
Treat this as a list of behaviors to account for, not a requirement to preserve those APIs.
Reports must distinguish what happened at each relevant boundary without equating a valid signature with reachable storage.

Existing evidence can save investigation when its assumptions still apply.
Examples include the unique-index and update/update conflict coverage in `test_note_to_self_integration.py`, divergent-publication coverage in `test_note_to_self_refresh.py`, and signature/import and Hub routing micro tests.
Existing reconciliation micro tests establish local generation checks and serial UUID ordering, not sibling coordination.
No test's existence is an argument for retaining the behavior it asserts.

Choose focused micro tests for the components actually affected by the implementation.
Run the repository suite with `uv run pytest` when the implementation reaches the corresponding integration checkpoint.
Record actual commands, results and blocked checks in that branch's notes.
Trace provider I/O through Hub and management decisions through Manager, and check that generic sync has not acquired allocation policy.
Check conceptual consistency across Core and app berths independently of happy-path results.

## Documentation and issue disposition after a decision

Once a protocol is accepted, decide how its semantics should enter the Manager/Hub specs and whether any architectural principle needs updating.
Clearly distinguish designed behavior from implemented behavior.
Remove unsupported safety claims rather than carrying them into the new account.
The present discussion does not authorize presenting a candidate as accepted design.

Update #238 with the eventual decision, evidence, limitations and agreed implementation split.
Keep it open until its runtime correctness requirements are met; a model or design discussion alone cannot close it.
Update #139/#235 with actual dependencies and interfaces when known, leaving their independent UX and capstone scope open.
Preserve #237's distinction between shared accounts and device-local credential repair when discussing overlap.
If the design revises a deliberate #224 choice, explain the substantive change and reasoning rather than labeling every wire change a reversal.

Prepare focused issue proposals for demonstrated independent defects outside the agreed work.
The selector investigation found one shared policy implementation, so there is no demonstrated duplicated-selection-policy issue to file.
Provider migration, old-location cleanup and speculative operational recovery remain outside this work.
