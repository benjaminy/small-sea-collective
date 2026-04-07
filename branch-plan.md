# Exact-Snapshot Tag-Aware Git History Pruning

Elaborate on the local-only git-history-pruning experiment in
[Experiments/git_history_pruning/README.md](Experiments/git_history_pruning/README.md)
to answer a narrower question for branch `experiment-git-history-tags`:

> If an app can mark particular historical states for future reference, and
> those app-level tags map down to git/Cod Sync tags, how badly do those exact
> snapshot tags erode the storage savings of history pruning?

This branch is about measuring that tradeoff clearly enough that a skeptical
reader can see the useful middle ground between:

- no retained tags beyond the normal recent working window
- retaining every historical snapshot so aggressively that pruning becomes
  nearly pointless

## Branch Goal

This branch should leave us with:

- a reproducible extension of the existing pruning experiment
- one crisp retained-tag semantic: exact snapshot readability later
- concrete evidence showing how storage changes across a spectrum of tag
  density and tag placement scenarios
- a recommendation for what a future local pruning API should promise about
  retained snapshot tags

This branch should **not** change Cod Sync's wire format or production
behavior yet.

## Core Claim To Test

The branch is trying to prove or disprove this more specific claim:

> A repo can remain meaningfully blob-pruned while still preserving selected
> exact historical snapshots, and the storage cost depends more on blob
> overlap, blob churn, and tag placement than on raw tag count alone.

## Why This Matters

The current pruning experiment supports a local-only path where a repo keeps
recent boundary-to-HEAD blobs while dropping older blob data. That is a useful
starting point, but a real app may want simple historical affordances such as:

- "bookmark this state before a risky edit"
- "keep the last published version"
- "keep quarterly checkpoints"
- "keep named milestones that users may revisit later"

If those requests become git/Cod Sync tags, we need to know what they actually
cost. Otherwise we risk building a feature that sounds lightweight but quietly
disables most of pruning's benefit.

This branch matters because the eventual local pruning API needs an honest
contract. We should know whether exact snapshot tags behave like:

- a modest extra storage tax
- a steep cost once tags get old or binary-heavy
- something driven mostly by blob overlap rather than the raw number of tags

## Scope Of This Branch

### In scope

- Extend the local-only experiment in `Experiments/git_history_pruning/`
- Model app-retained states as ordinary git tags in deterministic fixture repos
- Compare exact-snapshot retention scenarios over the same repos
- Measure size, correctness, and operational behavior after pruning
- Produce a clear write-up answering what the "middle of the spectrum" looks
  like
- Derive guidance for a future local pruning API in `cod-sync`

### Out of scope

- Changing Cod Sync's wire format
- Shipping real long-lived tag support in the protocol
- Remote/cloud tag negotiation or teammate coordination
- UI for apps to create or manage retained states
- Mutable-tag conflict resolution across teammates
- Promising tagged-to-HEAD chain usability for old tags
- Any production internet behavior

Keeping this branch local-only is important. The question here is first about
storage geometry and local git behavior, not distributed protocol design.

## Definitions

- **Baseline window**: the current boundary-to-HEAD DAG closure already kept by
  the pruning experiment.
- **App state tag**: a user-meaningful name for a historical app snapshot.
  In this branch it is modeled as a git tag on a deterministic commit.
- **Retained snapshot tag**: an app state tag that the pruning policy must
  continue to support after pruning.
- **Exact snapshot retention**: preserving enough data that the tagged commit's
  snapshot itself remains materializable and readable later.
- **Protected blob set**: the union of blobs that must remain locally present
  because of the baseline window plus the retained exact snapshots.
- **Storage degradation curve**: how much pruning savings are lost as retained
  tags become denser, older, or more blob-heavy.

## Branch-Level Decisions

These are branch-scoped choices, not final product decisions:

- Use normal git tags as the stand-in for future app/Cod Sync retained tags.
- Treat retained tags as immutable references for the purpose of the
  experiment. Tag moves are a separate distributed-semantics problem.
- Retained tags mean **exact snapshot retention only** in this branch.
- Do **not** promise for retained tags:
  - `git bundle create <tag>..main`
  - full tagged-to-HEAD history materialization
  - merges whose needed bases fall outside the normal kept window
- Keep the current pruning baseline in place:
  - blobless local clone
  - checkout-based rehydration baseline
  - remove promisor remote
  - filtered repack
  - prune
  - do not include `git gc` (it bundles loose-object collection, reflog
    expiry, and repacking together, which would obscure which step actually
    freed space; this branch wants to attribute savings to pruning itself)
- Place experiment-retained tags on commits reachable from `main`, so the
  storage question stays legible before we add side-branch semantics.
- Fix the baseline kept window at **the most recent 20 commits on `main`**
  for all scenarios in this branch. Sweeping the boundary depth is a separate
  question; holding it constant here keeps the tag-cost curve interpretable.
  Fixtures should be sized so that 20 commits is a meaningful minority of
  history (roughly 15-25% of mainline length).

That exact-snapshot-only rule is the heart of the branch. The plan should stay
disciplined about what tagged states are promised to support and what they are
not.

## Questions This Branch Must Answer

### Storage behavior

1. With exact snapshot retention, how does storage savings fall as tag density
   rises from none to all?
2. At a fixed tag density, how much do results change when tags are:
   - recent-biased
   - evenly spaced
   - old-biased
   - placed on binary-heavy states
3. Is raw tag count a useful predictor, or is unique protected blob size the
   more honest metric?
4. How much does overlap with the baseline window reduce the marginal cost of
   recent tags?

### Operational behavior

5. What exact git behaviors remain available for retained snapshot tags?
6. Which operations still fail cleanly for unretained historical states?
7. Which history-oriented operations should the future API explicitly **not**
   promise for retained snapshot tags?

### Decision support

8. Is exact snapshot retention a good default semantic for a future local
   pruning API?
9. What warnings or cost-model hints should accompany that API?

## Deliverables

### 1. Tag-aware experiment extension

Extend `Experiments/git_history_pruning/run_experiment.py` so it can:

- generate deterministic retained-snapshot scenarios
- compute the protected set for exact snapshot retention
- run pruning under those scenarios
- record size and behavior results in structured output

### 2. Experimental write-up

Update [Experiments/git_history_pruning/README.md](Experiments/git_history_pruning/README.md)
so it answers this branch's question directly:

- what exact-snapshot semantics were tested
- what scenarios were tested
- how the storage curve behaved
- what operations remained available
- what operations are intentionally not promised
- what recommendation follows for future Cod Sync local pruning work

### 3. Small helper micro tests where appropriate

If tag-selection or protected-set logic is factored into reusable helpers, add
small local micro tests for the tricky logic. The main experiment can remain a
script.

### 4. Branch-plan archival update

If implementation findings materially refine the question or the answer, update
this plan as work proceeds. As the final step of the branch — in the same
commit that updates the experiment README with conclusions — move this file
to:

- `Archive/branch-plan-experiment-git-history-tags.md`

## Experimental Design

### Phase 0: Make The Guarantee Explicit

Before changing the script much, define the exact meaning of retained snapshot
tags.

For a retained tag `T`, the pruned repo must preserve:

- the tag ref itself
- the tagged commit/tree DAG objects
- enough blob data that `git checkout T` succeeds
- enough blob data that representative `git show T:path` operations succeed,
  where the representative path set per fixture is chosen deterministically
  as: the lexicographically first file, the lexicographically last file, and
  (for Repo A and Repo C) the largest blob at that tag. This set is fixed
  per fixture and recorded alongside the fixture definition.

This branch does **not** automatically promise that:

- `git bundle create T..main` works
- every intermediate historical commit between `T` and `HEAD` is materializable
- merges involving bases outside the normal kept window work

This policy models one simple promise:

- "remember this exact historical state"

If some stronger operation happens to work in a few scenarios, treat that as an
incidental observation, not a branch-level contract.

### Phase 1: Build Fixture Histories That Expose Snapshot Cost

The current fixtures are a good start, but this branch should make sure they
contain meaningful candidate states for retention analysis.

### Repo A: Typical app history

Keep Repo A as the main correctness fixture, but make the tag candidates more
intentional:

- recent states with high overlap
- older milestone states
- at least one binary-heavy milestone
- at least one release-like milestone after a merge

The goal is to let the same fixture show why "one old tag" and "one recent
tag" do not cost the same thing.

### Repo B / Repo C

Continue using the many-small-files and few-large-files repos to expose how the
same exact-snapshot policy behaves under different blob-churn shapes.

If the existing fixtures already make that visible, do not add more repos just
for variety.

### Phase 2: Define The Scenario Matrix

The experiment should test a manageable but meaningful spectrum rather than
only the two extremes. Density and placement are varied **independently** as
a small grid: every density level is tested under every placement scenario
(except 0% and 100%, where placement is meaningless).

### Tag density levels

Density is defined as **percentage of mainline commits on `main` that carry
a retained tag**. Test the following levels:

- 0% (no retained tags)
- ~10% (low)
- ~25% (moderate)
- ~50% (high)
- 100% (every mainline commit tagged)

Exact tag counts are derived deterministically from each fixture's mainline
length and recorded in the scenario output.

### Tag placement scenarios

At minimum, compare:

- **recent-biased**: tags clustered near the kept window
- **evenly spaced**: tags spread across the mainline
- **old-biased**: tags concentrated deep in history
- **binary-heavy milestones**: tags landing on commits that changed large blobs

This is important because a 10% tag density can be cheap or expensive depending
on where those tags land.

### Scenario comparison

For each scenario, compare at least:

- baseline window only
- baseline window + retained exact snapshots

The branch does not need an explosion of scenarios, but it does need enough
coverage to show where the shape of the curve changes.

### Phase 3: Implement Protected-Set Computation

The experiment should stop talking about "retaining tags" abstractly and
compute the exact retained set for the chosen snapshot scenarios.

### Baseline

Reuse the current kept-window calculation:

- boundary-to-HEAD full DAG closure

### Retained-snapshot protected set

Augment the baseline with:

- tagged commits that must remain materializable
- the blobs reachable from those tagged snapshots
- annotated tag objects where relevant

This is where the branch should measure overlap carefully. If two tags point at
states that mostly share content, the storage cost may be much lower than the
raw tag count suggests.

The implementation should avoid silently keeping broad extra history merely
because an old tag exists. This branch is about exact snapshot retention, not
history-anchor semantics.

### Phase 4: Validate Behavior, Not Just Size

The branch should continue the current discipline of proving operational
behavior, not just pack-size changes.

### Checks common to all scenarios

- commit hashes are preserved
- branches are preserved
- retained tags are preserved
- baseline kept-window access still works
- unretained old blob absence is still directly provable where expected

### Retained-snapshot validation

For each retained tag:

- `git checkout <tag>` succeeds
- representative `git show <tag>:path` succeeds
- file contents match the source repo

Where useful, also prove the exactness of the promise by checking that nearby
unretained history is still allowed to fail. For example, if an old tag is
kept, its parent or sibling commit may still be partially unavailable, and that
is acceptable.

### Negative checks

For unretained historical states, keep explicit proof that:

- representative old-blob access still fails when it should
- the repo is not silently over-rehydrated
- stronger history-oriented operations are not accidentally being treated as
  guarantees

This is critical. A scenario is not informative if it "succeeds" only because
it accidentally stopped pruning much of anything.

### Phase 5: Measure The Cost Curve Honestly

The branch should report more than final `.git` size.

At minimum, capture:

- source `.git` size
- pruned `.git` size
- size saved vs source
- savings retained vs the no-tag pruning baseline
- count of retained tags
- count of unique protected blobs
- total **inflated** size of unique protected blobs (sum of logical blob
  sizes, not on-disk packed size; packed size is reported separately as
  pruned `.git` size)
- overlap between tag-protected blobs and baseline-window blobs, reported
  as both blob count and inflated byte count
- marginal storage cost as tags get older or more blob-heavy

If one metric emerges as the best predictor of storage degradation, the README
should say so plainly.

### Phase 6: Produce A Recommendation For Future API Design

The branch should end with a recommendation that a future local pruning API can
actually use.

Possible outcomes include:

- **Exact snapshot tags are the right default**
  because they preserve named historical states without destroying most pruning
  savings.
- **Old or binary-heavy exact snapshots need warnings**
  because their storage cost is much higher than recent high-overlap tags.
- **Tag placement matters more than tag count**
  so the future API should expose cost estimates or warnings tied to age/blob
  overlap, not just the number of tags.
- **A retention budget may be useful**
  if the curve shows that apps should cap how many expensive snapshots they
  retain.

If the evidence supports a different conclusion, say that instead. The point is
to end with a policy recommendation, not just raw measurements.

## Validation Criteria

This branch is successful if all of the following are true:

- the 0%-tag scenario still reproduces the current pruning baseline
- the 100%-tag scenario is measured explicitly rather than inferred
- at least one meaningful middle-of-the-spectrum result is demonstrated
- retained snapshot guarantees are tested directly, not assumed
- unretained old-blob absence is still proven in representative scenarios
- the README can answer "what does the spectrum between none and all look
  like?" with concrete evidence
- the branch leaves a future local pruning API with a clearer exact-snapshot
  contract for retained tags

## Risks To Watch

- **Overfitting to tag count**
  The real cost may track unique protected blobs and overlap much more than tag
  count itself.
- **Accidental over-rehydration**
  A scenario can look "robust" merely because the implementation fetched far
  more history than intended.
- **Fixture-specific conclusions**
  Binary-heavy histories and text-heavy histories may curve differently. The
  README should say where conclusions seem robust vs fixture-shaped.
- **Semantic drift**
  If the branch starts quietly promising more than exact snapshot readability,
  the final recommendation will be muddy.
- **Whole-tree retention when the app only wants a few files**
  Exact snapshot retention keeps every blob reachable from the tagged tree,
  even if the app only cares about a handful of files at that state. This may
  overstate real-world cost, and is worth noting as a natural follow-up
  question (subtree-scoped retention) rather than solving here.
- **Scope creep into protocol design**
  This branch should sharpen future protocol questions, not solve them all at
  once.

## Expected Follow-On

If this branch produces a clear answer, the likely next step is not "ship
distributed retained tags immediately." The likely next step is to refine the
future local pruning API shape in `cod-sync`, potentially adding a concept
like:

- prune with a recent boundary
- optionally retain named refs as exact snapshots
- warn when a requested retained snapshot carries unusually high blob cost

That follow-on can stay local-only until we know whether real protocol support
is actually necessary.
