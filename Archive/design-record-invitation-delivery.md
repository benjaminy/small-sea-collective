# Design record: first-contact route delivery (#183)

## A storage announcement is a route selection, not an availability promise

A `teammate_berth_storage_announcement` says the teammate selected one locator
for one berth.
It does not promise the provider object stays reachable or peer-readable.
`EffectiveTransportSelection.status == "announced"` reports that the reader
holds a selected valid announcement, nothing more.
Every reader must handle every provider error whenever it attempts I/O.

The durable publisher-side rule is locator finality: sign only a locator the
provider will not rewrite.
Materialization is how a publisher learns that today, not an independent
requirement — which leaves room for a provider whose locator is final from the
moment it is chosen, while preserving the publish-after-materialization
ordering established by #134 and #137.

## The courier carries the route beside the acceptance, not inside it

Admission is an immutable membership fact; storage announcements are replaceable
routing claims.
Keeping them separate leaves the acceptance's canonical bytes and `record_id`
settled by #164 untouched, and lets #165 re-canonicalize announcements without
rewriting admission records.

The cost is accepted deliberately: a courier can strip the sidecar, and one
holding several valid announcements from that key could replay an older
invitee-authored route.
An optional signed manifest would add nothing, because a courier can strip the
manifest and sidecar together; only a mandatory association would prove whether
the invitee sent a route, and that would make route presence part of the
acceptance protocol.
Stripping and stale valid replay are surfaced denial-of-service risks for now.

## Sidecar verification is acceptance-scoped on purpose

The acceptable signer is the key the self-certifying acceptance has just proved
the invitee holds — not a caller-supplied key and not the local trust view.
A future general route importer must derive acceptable keys from the current
trust view instead.
Keeping the two operations separate is what stops this from becoming an
accidental routing trust path.

## Derive the state; persist only the artifact

Join, admission, and route state are all derived from the clone, the NoteToSelf
`team` row, the membership-cert view, and the announcement query the Hub already
runs. No status column was added.

The one thing not stably derivable is the signed acceptance itself: `created_at`
is inside the signature, so re-signing would mint a different `record_id` on
every export and could put two valid acceptances for one proposal into
circulation.
That artifact is persisted once in the device-local DB, is eligible only under
the exact current join, and may never be replaced by a differently signed
acceptance after its first export.

## The export gate is what makes deferring repair defensible

Withholding the acceptance token until a route is ready — with no exception —
removes every cause of route-less admission that the invitee controls.
What remains unrepairable is only a sidecar stripped or corrupted in transit,
which is why post-admission route repair could be left as follow-up work rather
than built here.

Membership with no storage of one's own was treated as a separate design
question rather than a degraded join: such a member could not push at all, so
read-only membership deserves its own answer instead of a warning-gated half one.
