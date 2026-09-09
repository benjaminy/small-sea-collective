Make a sibling berth disagreement a visible pause a human can resolve

Competing cloud allocations from a participant's devices now merge into a visible, device-local berth pause instead of rejecting unrelated NoteToSelf work through a unique index.
The Manager can inspect and preserve either location through the Hub, compare a human choice against the reviewed evidence, and resolve to either existing allocation without raw database edits.
The Hub enforces the pause, investigation stays bound to the retained route, and shared deletion does not release another device's held pause.

The connected scenarios now live in the sandbox and cover resolution and interrupted publication at either destination.
Micro tests cover atomic adoption, competing writers, retained inspection evidence and restoration of older snapshots; restoring an old snapshot can still lose the only recorded pause or decision, a limit documented in the Manager spec.
Final validation passed 998 repository micro tests with 3 skipped and all 15 explicitly invoked sandbox scenarios.
