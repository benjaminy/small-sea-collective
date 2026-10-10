# Owner answers, 2026-10-10

Answers to the questions raised by the proposal and critique round.

1. **Purpose.** The demo organizes development of the framework, so it is mostly research. It should not be throw-away code.
2. **Code home.** Open. The owner suggested a new package, or replacing the existing constitution code outright ("we have git so we don't really lose anything").
   The orchestrator recommended writing the spine inside the Manager package, alongside the old modules, then cutting over in a later slice.
   Today's constitution code is about 580 lines (`constitution_store.py`, `constitution_projection.py`, `wrasse_trust/events.py`), with about 10 call sites in `provisioning.py` and 18 Manager test files.
3. **Outsider sessions.** Yes, the owner will recruit participants.
4. **Receipt means acceptance.** Provisionally yes: a device that receives a valid, non-conflicting event accepts it.
   "This is local-first, so we fundamentally can't control what all devices do. But we have to start somewhere."
5. **Policy.** One fixed policy for now. Policy flexibility is medium- to long-term work.

## Where the round landed

All three critics recommend starting from proposal C: the remove-Bob versus add-device conflict, a reconciliation that two members approve and every device checks, and a fresh process that verifies it from an exported evidence bundle, with mutation tests.
Opus adds a no-code slice 0 that shows static screens to outsiders (proposal A's prediction questions).
D's history query and A's sessions come next; B's Hub, Manager, and MinIO integration comes later.

Verified gaps in `spikes/signed-probe/probe.py` on `constitution-party`:
- `receive` returns "accepted" without storing the event or checking authority.
- `receive_reconciliation` never calls `valid_approvals`.
- `state_at` raises on a reconciliation event.
- The probe can remove a device but not a member.

## Later the same day

- Code home: inside the Manager package, cut over later.
- The slice 1 plan ([slice1-plan.md](slice1-plan.md)) and its five decisions are approved; T1-T3 may start.
- "Pause" means roughly "resolve the issue before adding to the Constitution." In at least some cases apps should keep operating normally.
