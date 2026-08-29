# Design record

## Git refs are the only durable record of what was fetched

Fetched, superseded, and contained-in-`main` state is derived from live refs and Git ancestry at read time.
There is no sync sidecar database, watermark, latest-fetched SHA column, or persisted status label.
A fresh `TeamManager` rediscovers everything from disk.

The alternative — a status table alongside the refs — creates a second truth that can disagree with Git after any partial failure, and every disagreement then needs a repair path.
Deriving instead means a failed fetch can leave imported objects behind without leaving a lie behind.

## Divergence is an observation, not an error state

Two competing heads for one teammate are both kept.
The forward-only convenience ref is never retargeted, and the competing head gets its own immutable ref named by the link that published it.
Nothing picks a canonical head, and a repeated fetch of the same link verifies the ref it already wrote instead of failing.

Choosing a winner here would be an integration decision, and this layer has no basis for one: it cannot verify publication authorship (#190) and has no admissibility contract to apply.

## Supersession is scoped to one logical source

One teammate is one source; this participant's own publications are another.
Within a source, a ref is superseded only by a strict descendant, so two refs at the same SHA both stay maximal.
Across sources nothing is hidden: a teammate whose head happens to be contained in another teammate's history is still reported.

Cross-source containment would otherwise silently drop a source that a later authorship or admissibility check might treat differently.

## Retryable prerequisites must survive both the Hub and the store boundary

An unknown peer storage route and an undelivered peer sender key are things a caller can wait out.
Collapsing either into a provider failure tells the caller the peer's storage is broken when nothing is.
Both now have a dedicated Hub exception, a stable HTTP 409 error code, and a peer-store error class, and the classification stays specific to `PeerSmallSeaStore` — an unexpected non-CAS 409 reaching `SmallSeaStore` is still a provider failure.

## Inviter-reads-invitee is blocked until Core integration exists

An invitee holds the inviter's sender key from the invitation token, so the invitee can read the inviter's chain immediately.
The reverse requires the invitee's sender key, which arrives only through runtime redistribution, which requires the invitee's membership certificate to travel back over the Core chain.
That is the integration this issue deliberately does not perform, so within one team the peer read currently works in one direction only.
This orders the follow-up work: authorship verification (#190) and Core integration (#228, blocked on the contract in #226) have to land before a symmetric read is reachable.

## A local Git failure over fetched peer bytes is a structural chain error

`CodSync.fetch` converts `RepoError` from bundle header parsing and bundle import into `ChainError`.
Those Git calls run over bytes a peer published, so a failure there says the peer's payload is unreadable, not that this device's repository is broken.

This is what lets any caller read a `RepoError` surviving a fetch as a local ref failure and nothing else.
Manager depends on it: without the conversion, `CoreRefPersistenceError` would also absorb a corrupt peer bundle and report a remote problem as a local one.
The narrowing applies to every `fetch` caller, not only to Manager.
