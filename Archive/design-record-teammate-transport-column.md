# Design record — remove the second teammate transport authority (#210)

## The manual announcement form was removed, not re-pointed at the Core berth

The old Teammates column carried a self-only form that published a caller-supplied `(protocol, url, bucket)` as a `teammate_transport_announcement`.
The obvious-looking move was to re-point it at `teammate_berth_storage_announcement` so the column kept a write path.
It was removed instead, and nothing was put in its place.

A generic locator form lets a participant assert any string as their route without the provider ever having materialized that object.
The berth-scoped announcement is supposed to record a locator the Hub actually materialized — that is why `prepare_team_route` re-reads the provider-issued locator before signing.
Re-pointing the form would have reintroduced, one table over, exactly the "status that does not correspond to a real route" defect this branch exists to remove.

The genuine gap this leaves is real and is tracked separately: a creator who adds storage after team creation, and an established teammate who changes providers, have no self-service path.
`create_team` covers only the creator's initial route, and `prepare_team_route` only serves an invitee holding an eligible acceptance artifact.
That flow needs to be designed through Manager and Hub, not restored as a form.

## An unresolvable Core berth fails loudly rather than degrading

`list_teammates` raises `MissingCoreBerthError`, and lets `AmbiguousCoreBerthError` propagate, rather than reporting every teammate as `missing`.
Both conditions mean the view cannot identify the coordinate it claims to report, which is not the same fact as "no teammate has announced a route" — and collapsing them would recreate a misleading status, the defect being fixed.

The cost is accepted deliberately: this reaches the browser as an unhandled 500 from the team view, its fragments, and the admission-events long poll, all of which call `get_team`.
The teams index does not, so it keeps working.
The team view is a developer debugging surface at this stage; failure-mode UX is deferred until it has real users.

## `TransportEndpoint.bucket` was deliberately not renamed

With the teammate-wide announcement gone, that field is populated only from `TeammateBerthStorageAnnouncement.location`, so the name now misdescribes every value it carries.
The Manager view projects it as `location` and exposes no `bucket` alias, but the field itself keeps its name here.
Renaming it spans wrasse-trust, Hub routing, Manager, and templates, and is load-bearing in Hub peer routing and the publish dedupe check.
Mixing that rename into this branch would have made the routing-authority change hard to review; it is tracked as follow-up.
