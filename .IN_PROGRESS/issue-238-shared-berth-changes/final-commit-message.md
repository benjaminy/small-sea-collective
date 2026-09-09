Make a sibling berth disagreement a visible pause a human can resolve

Two of a participant's devices can rotate one berth's cloud allocation while
disconnected. A unique index used to refuse the merged rows, which named a
commit rather than a disagreement, rejected every unrelated row travelling with
it, stopped the whole NoteToSelf channel in both directions, and left the
device publishing to a location its sibling would never learn about. The only
effective repair was a raw row delete.

The index is gone and the invariant it carried is now a projection plus an
enforcement point. More than one live allocation for a berth is an unresolved
question: the Manager records it as a durable device-local pause inside the
same transaction as the rows that opened it, and the Hub refuses every
own-berth provider operation at its one allocation lookup. New evidence that
explains the disagreement away does not release the pause, because none of it
is a decision. A Manager-only Hub path reads either candidate while the berth
is stopped, bound to that candidate for the whole chain walk, so a person can
look at both locations without integrating either. Resolution compares the
evidence digest the person actually read, deletes the losing rows, and keeps
the report the decision was made over; either existing location can be chosen,
including one a sibling already deleted.

Validated by nine connected scenarios in
`.IN_PROGRESS/issue-238-shared-berth-changes/probes/probe_source_resolution.py`
against a real Manager, Hub and MinIO, and eleven micro tests in
`test_note_to_self_integration.py` that replace the unique-index test.
Fourteen additional inspection micro tests cover account-route binding, serialized observation publication, interrupted report reconstruction and preservation of divergent heads.
The implementer handoff still lists interrupted adoption, allocation/locator writers racing resolution, interrupted post-resolution publication and restoration of older local state as open verification work.
