# What each actor may conclude about a berth disagreement

Branch: `issue-238-shared-berth-changes`.
Status: Move 10 step 2 under [plan.md](plan.md#next-work).
These are obligations a representation must meet, not a representation.
No wire format, merge-node definition or runtime change is proposed or accepted here.

Every claim below is stated against *ancestry the actor holds*.
A candidate in step 3 supplies that from whatever it publishes or retains, and must then satisfy these obligations on the schedules preserved in [models/model_human_resolution.py](models/model_human_resolution.py).
The executable form is the second half of that file.

## Four conclusions, kept apart

The reviewed contract ran two of these together, and [the controls](notes.md#move-10-step-1-2026-09-07-the-counterexamples-are-executable) are what that cost.

| Conclusion | Held evidence | What the actor may claim |
| --- | --- | --- |
| **Supersession** | One selection reaches the other through links the actor holds, every link present | The later one replaced the earlier; no human decision is implied |
| **Incompatible choices** | Neither reaches the other, both have complete held ancestry, route content differs | Two choices were made that cannot both stand |
| **Incomplete ancestry** | Neither reaches the other, and some named parent is not held | Nothing about the pair; the missing evidence could still establish supersession |
| **Contradictory payload** | Two announcements name one selection identity with different claims | One identity carries irreconcilable assertions, independent of any pair |

Incomplete ancestry is a refusal to claim, not a weaker conflict.
It is the correction the second and third counterexamples demand.

A held common ancestor does not license an incompatibility claim.
The third counterexample holds paths from P to both tips and still turns out to be succession once the missing second parent arrives, so the classification deliberately does not consult common ancestors.

Not knowing a selection's ancestry is not the same as knowing it succeeds nothing.
An announcement that publishes no links leaves ancestry unknown; a selection that names no parents is a root with complete ancestry.
Collapsing the two lets an actor claim incompatibility from an absence of information.

## Per-actor evidence and claims

| Actor | Holds | Cannot hold | Consequence |
| --- | --- | --- | --- |
| Selecting device | Its own selection records with links, plus any sibling history adopted over NoteToSelf | Anything a sibling has not sent | May claim supersession over its own chain; may claim incompatibility only after adopting the sibling's records |
| Sibling | The same, mirrored | The other device's unsent records | Before delivery it is uninformed, not in disagreement, and has no cause to pause |
| Receiving teammate | Signed announcements only | Private selection history, by the [#224 decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) | Can conclude only what announcements publish |

The teammate row carries the sharpest consequence, and the model checks it.
With no ancestry published — today's runtime — every pair a teammate holds classifies as incomplete ancestry.
It cannot separate an ordinary rotation from a disagreement, so it may claim neither.
Published ancestry is what buys the supersession claim, at the disclosure cost the contract's succession section already names.
That is a statement of what each representation must pay for, not a decision to publish links.

## Tips, retained history and identity

Only tips — held selections that nothing else held supersedes — are candidates for the current route.
Retained branches are evidence.
Separating the two is what keeps preserving evidence from leaving a resolved disagreement permanently active; a pair that was an incompatible choice and is no longer both current is a historical fork, and does not pause anything.

Two selections with identical route content remain two selections.
Matching content is not agreement, and the resolution vocabulary must still be able to name each of them, since the first counterexample is exactly a resolution hidden behind content equality.

## Pausing and resuming

A pause cause is a fact about held evidence: an incompatible pair among the tips, an incomplete pair among the tips, or a contradicted identity.
Because a cause is a fact rather than a flag, it disappears exactly when the evidence that produced it changes.

Candidate resume rule: an actor may resume when no cause remains and no person is holding it.
This promises no progress.
Missing history may never arrive, and a person may keep their own device paused indefinitely; the rule only says that when the cause is gone, further waiting is not required.
It is recorded as a candidate, not adopted — [Human-Scale Coordination](../../architecture.md#human-scale-coordination) permits it and does not mandate it.

## Inspection is not integration

Producing a report reads evidence and changes nothing: no live state, no publication.
A paused actor publishes nothing at all.
The distinction is load-bearing because the resolution operation's binding depends on reviewing evidence that has not yet been applied, and because a report must be producible while the pause is in force.

## Limits

Signing is modeled as a claim with a signer, and a merge-shaped claim asserts supersession of several branches; neither is evidence that a person reviewed anything.
Nothing here records human involvement, and step 3 must keep that gap visible for every candidate.

The classification is pairwise and the schedules are small.
Delivery only adds, so nothing is forgotten, and no restart, replay or restore is modeled.
The resume rule is checked only on the preserved schedules; competing human resolutions and multiple disputed berths are later work.
