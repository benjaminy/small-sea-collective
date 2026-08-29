# Design record — Manager cloud UX (issue #139)

## The Hub's two storage preconditions stay separate all the way to the user

`cloud_location_missing` and `cloud_credentials_missing` used to collapse into one
Manager route reason, `storage_not_configured`, and one repair: "Add cloud storage."
They are now `location_missing` and `credentials_missing`.

The split is not cosmetic.
The two states have disjoint repairs.
A missing location means an account is registered but the Core berth has no allocation, which route reconciliation creates on its own.
Missing credentials mean the account exists and is selected but stores nothing usable, which reconciliation cannot fix.
Because the current account surface does not update credentials in place, the user must register and select a replacement account.
Offering a no-change "Retry" for a credentials failure produces a loop that always fails the same way.

`storage_not_configured` keeps its narrower meaning: no account is registered at all.

## Session authorization is not current Core-route state

A valid Hub session and an unusable Core berth coexist routinely, and the previous UI let the
green session dot read as team health.
The per-team session indicator now speaks only for the session, and current Core-route state is
its own persistent signal in the Core Storage section rather than something the user only sees
after an operation fails.
That state is local to the current allocation and does not claim that peers cannot still select
an earlier valid announcement during replacement.

## Push reports route reasons, not Hub responses

The Hub's `cloud_storage_required` body carries no `detail`, so the client's exception message
is the raw JSON envelope.
Push now translates the typed reason through the same cloud-to-route mapping route preparation
uses, and hands the repair to the existing reconciliation operation instead of introducing a
second one.
That mapping (`ROUTE_REASON_BY_CLOUD_REASON`) became public for exactly this reason: it is the
one place where a Hub precondition acquires a Manager meaning.

## Deferred

Linked-device allocation ownership and signer identity were left untouched.
Deciding whether a linked device shares the member's berth allocation and announcement or can
own a distinct location has to happen before another route publication path or
`_bucket_name_for_protocol` as routing authority is revived.
