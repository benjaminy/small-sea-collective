# Possible work after the discussion

No implementation handoff is ready.
This document retains the tentative implementation split, runtime validation guidance and eventual issue changes.
The current design questions and witness schedules live in [plan.md](plan.md); the reasoning lives in [notes.md](notes.md).
Revise it from the accepted design before assigning implementation work.
These are local notes; no issues or messages have been posted by this document update.

## Tentative implementation split

The earlier scope decision placed design investigation on this branch, one central Core operation in a first follow-up, and remaining integration and user-facing work in a second follow-up.
That is a possible way to bound the work, not a reason to retain the current operation or helper structure.

The first follow-up would implement the chosen protocol in one established-team Core use case and produce runtime evidence for its provisional decisions, retrospective checks, concurrency, conflict handling and repair semantics.
Its output would include a concrete contract for the later integration work and an honest account of which paths remain outside that contract.
A successful central use case would not establish system-wide safety.

The second follow-up would apply that contract across creation, invitations, ordinary changes, repair, integration, generic publication and courier delivery, and align reports and documentation.
It would establish how arbitrary app berths in #139 and Core/Files use in #235 share the same semantics.
The full app provisioning UX and Files capstone remain separate work.

Current function names, table boundaries, UUID ordering, publication result types, signed fields and tests do not constrain either follow-up.
Retain or replace them according to the accepted design.
There is no production compatibility or rollout requirement.

## Runtime evidence to preserve

Turn the schedules in [plan.md](plan.md) into deterministic runtime witnesses once their intended semantics are decided.
Use two installations with distinct trusted device keys and a local provider service where appropriate.
Pause at real operation boundaries; do not replace the decision checks, publication, integration or selector being examined with a success stub.
Use local mocks or MinIO for provider networking.

Use the schedule catalog in [plan.md](plan.md), including conflict discovery and possible persistent disagreement.
Expected results must follow the chosen protocol rather than today's row counts, identifiers, return types or helper calls.
Validate safety properties separately from progress under explicit favorable conditions; do not require universal convergence or promise detection before sufficient evidence arrives.

An eventual integration audit must include creator setup, established-team changes, invitation preparation/export/import, ready-route shortcuts, generic NoteToSelf and Core publication, and the public management interface.
Treat this as a list of behaviors to account for, not a requirement to preserve those APIs.
Include the conditions for local/offline creation and linked-device bootstrap, and justify invitation-specific signer requirements separately from established-team repair.
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
