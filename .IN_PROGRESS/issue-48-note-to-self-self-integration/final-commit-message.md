Integrate stored NoteToSelf history into the live database

One identity's devices can each create teams, and either device can now combine
both histories. Adoption is a row-level merge applied to the live `core.db`
inside one SQLite transaction, never a file replacement: the Hub is a second
long-lived process writing the same database, so SQLite's cross-process write
lock is the only serialization both sides already honour. Because Git reads
that file without honouring SQLite's locks or journal, every Git read of the
live database — publication, merge recording, and the three direct provisioning
commits — now runs under a short writer reservation.

`refresh_note_to_self` runs on the same path as the new explicit integration, so
no Manager operation can leave a conflicted `core.db` behind, and the fetched
head is parked before any live state changes, including an initial bootstrap checkout.
Nothing is precomputed: the
computation is always (base, source, live-now), so an adoption interrupted
anywhere is corrected by running it again, and the branch adds no watermark,
lease, or recovery record. Every refusal — an incompatible source tree or schema,
a same-row conflict, ambiguous row identity, a constraint violation — rolls back whole
and is reported with enough detail to act on. The Manager claims only that a source is a
structurally valid stored history whose tree and schema it could read; Cod Sync
does not establish which device wrote it, and nothing here says otherwise.

`splice-merge` gains a borrowed-connection mode and returns its conflicts instead
of printing them. Cod Sync gains parked-head discovery over its own ref namespace
and five index-and-object plumbing operations. Neither gains any policy.
