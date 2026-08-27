# Design record — Core route reconciliation (#208)

The rules themselves landed in `packages/small-sea-manager/spec.md` (berth cloud allocations, teammate berth storage announcements, "Reconcile Core storage route") and `packages/small-sea-hub/spec.md` (provider materialization).
What follows is the reasoning that the specs do not carry.

## Two route paths that deliberately disagree about selection

`prepare_team_route` keeps `_auto_allocate_berth_cloud_if_available`, which takes the first registered account by rowid without asking.
That silence is acceptable while an invitee is racing to publish a first route and any storage beats none.
It is not acceptable when an established teammate is deciding where their Core data lives, so `reconcile_team_route` reports `storage_choice_required` instead.
The invitation path was left alone rather than aligned, and the auto-allocate helper is deliberately not reused.

## Why a generation token rather than comparing locations

The Hub's conditional locator writeback originally proved only "same allocation row, same expected location".
Once an allocation can be replaced, that is not identity: two providers can issue the same location string, and a freshly generated `ss-`/`pending-` name can collide with a provider-issued one.
Making replacement mint a fresh allocation ID turns the row into a generation token that both the Hub and the publisher can check cheaply, with no new columns and no query the record did not already carry.

## The accepted gap, and the intent token that was not built

Replacing an allocation is durable before materialization, so a failure in between leaves this device unable to do its own Hub cloud I/O (`announcement_missing`) until a retry converges, while peers keep reading the old location.
Making the exact failed command replay-idempotent would need a persisted operation intent — workflow state that answers no question this branch is asking.
The chosen contract is narrower and cheaper to reason about: the change is one-shot, the resulting allocation is retryable, and the surfaces say so (the web retry posts no intent; the CLI names `reconcile-route TEAM`).

## Ordering fixed at the publisher, not in the selector

Selection orders by descending `announcement_id`, and UUIDv7 is random within a millisecond, so a replacement could be published, committed, and then ignored by every peer.
Changing selection to consider `announced_at` or insertion order would weaken a rule other readers depend on.
Instead the publisher mints an ID strictly greater than the effective selected row — and only where the ID is minted immediately before signing, since `announcement_id` is inside the signed bytes and an imported peer or sidecar row must never be renumbered.
Rows the selector rejects cannot pin publication ordering.
This settles serial publication on one device; two devices publishing concurrently remains open.
