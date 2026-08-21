# Notes

Branch for GitHub issue #191, follow-up from #187.
Cod Sync now refuses to replace a stored chain with unrelated or older Git history.
This branch defines the smallest safe caller-level response to a refused or uncertain publication.
Cod Sync gets a fixed envelope of at most one head write and at most two validated observation passes, and every terminal result follows from what those passes observe about stored Git state and about whether this invocation's write can still take effect.
It does not add automatic publication retries, exact transport-provenance tracking, or a generic conflict-resolution framework.

## Layer boundary

Publication failure crosses five distinct layers.
They must not be collapsed into one generic conflict policy.

| Layer | Responsibility |
| --- | --- |
| Hub storage adapter | Classify a cloud write as applied, refused, or unknown. |
| Store client | Report what happened to a particular read or write. |
| Cod Sync | Make at most one head write, observe the stored head at most twice, then stop at the application boundary. |
| Application | Decide whether and how divergent application state is integrated, spliced, superseded, or deliberately discarded. |
| User surface | Explain the terminal state and offer an action that actually exists for that repository and store. |

The Hub does not decide publication policy, and neither does the store client.
Applications do not independently reconstruct whether the attempted Git state is present in the stored chain.
Cod Sync does not decide the meaning or relative value of application data.

## Settled decisions

### Settlement proves state containment, not exact link provenance

`publish()` captures the exact local `main` commit once, before its first store operation.
That `attempted_head`, rather than a mutable branch name, is used for the bundle, ancestry checks, result, and diagnostics produced by the invocation.
The invocation also records `predecessor_head`, the validated stored head its candidate is built on, together with `predecessor_etag`.
An observation pass produces `observed_head` and `observed_etag`, confirms exact absence, or fails without establishing the head state.
Git heads determine containment and divergence.
The write's own condition — `predecessor_etag` for a later-chain write, exact absence for an initial one — determines whether that write can still take effect.

Cod Sync makes at most one head write per invocation.
It does not retry an immutable-object collision, repeat a failed head write, build a successor candidate, or loop after contention.
A later user or caller invocation may retry a failure that Cod Sync proves is over.

An *observation pass* is one validated read of the stored head.
An invocation performs one before uploading and at most one more after a failed head write.
Two passes and one head write is the entire budget.

The initial pass can end the invocation before any upload.
An equal or covering head is `already_present`.
A divergent head is parked and reported as `integration_required` without a second pass.

Callers care that `attempted_head` is contained in the validated stored history, not whether this invocation's exact link landed.
Equality and coverage therefore share one ordinary disposition, `already_present`, and the result still reports `observed_head` so a caller can tell them apart when that matters.

This rule removes the proposed link-ID reachability walk.
It also removes the need for an attempt counter, `retry_exhausted`, successor-candidate construction, and a new traversal budget in this branch.
#189 still owns finite budgets for the existing fetch walk, which remains a separate deferred concern.
The tradeoff is less unattended liveness under contention.
For a research project with no deployed caller, an explicit safe stop is preferable to machinery whose need has not been demonstrated.

### An observation pass can cost a fetch

Comparing an observed head to `attempted_head` requires the observed commit in the local object database.
So does computing a merge base, and so does parking a ref.
`_verify_stored_bundle` checks that a stored bundle's prerequisites are present and runs `git bundle verify`, but it imports nothing (`protocol.py:561-579`).
An observation pass therefore imports through the existing fetch path whenever the observed head is not already local.
"One pass" bounds how many times the stored head is read and settled, not how many store round trips that costs.

This applies to the initial pass, not only to settlement after a failed write.
`_publish_onto` currently raises `PublicationIntegrationRequiredError` as soon as the stored head is absent locally (`protocol.py:291-297`), which labels a covering descendant as divergence.
Telling the two apart is the entire point of `already_present`, so the ordinary "a sibling device published ahead of me" case now imports inside `publish()`.
That moves this branch's exposure to the unbudgeted fetch walk (#189) and to unverified link authorship (#190) from the failure path onto the normal path.
Neither is a blocker here and neither is fixed here, but the follow-up edits below must say so.

Importing leaves validated objects in the local object database.
It never moves `main`, changes a work tree, constructs a merge, or chooses a resulting tree.

### The store's etag contract is a precondition, not an assumption

The Cod Sync format specification requires an etag on every `latest-link.yaml` download and upload (`format-spec.md:126-131`), and nothing enforces it.
`get_latest_link` is typed `Tuple[bytes, Optional[str]]` (`store.py:117-121`), `SmallSeaStore._download` reads `body.get("etag")` (`store.py:342`), and the Google Drive and Dropbox adapters substitute `""` when the provider returns no `ETag` or `rev` (`gdrive.py:97`, `dropbox.py:62`).
The specification already calls Google Drive non-conforming, and `backend.py:1309` still builds its adapter for a berth cloud.

Call an etag *comparable* when the store returned a non-empty string for it.
`None` and `""` are both incomparable, and an implementation that tests only `is not None` reintroduces the gap.

Until this branch the contract was self-enforcing.
`_publish_onto` forwards its observed etag as `expected_etag` (`protocol.py:316`) and `put_latest_link` converts `None` to `CREATE_ONLY` (`store.py:422`), so an incomparable etag produced a create-only write that a non-empty chain always refuses.
That is a doomed write, not a wrong answer.
This branch is the first to read an etag as evidence about the store's state, so a missing one now has to be caught rather than forwarded.

The initial pass therefore refuses to build on a non-empty chain whose head arrives without a comparable etag.
It raises before any upload, in the same shape as the existing version-regression and archived-copy checks (`protocol.py:259-279`), but positioned after containment and divergence have had their chance to end the invocation.
A stored head that already covers `attempted_head` needs no successor, so its etag never matters and its absence is not worth a failure.

This is a broken store rather than a publication outcome, and none of the five terminal results below describes it.
A store that cannot express a conditional head write cannot publish onto an existing chain at all, and saying so once is more honest than issuing a doomed write and then classifying its failure.
An empty chain needs no such check, because a create-only write supplies no input etag.

Catching it here also keeps `predecessor_etag` comparable everywhere below, so the settlement rules never have to reason about an unusable condition.

### Git state and the write's condition settle different questions

After a failed head write Cod Sync answers two questions, and neither answer constrains the other.

- What does the stored Git history now hold?
   That decides whether an application has to integrate anything.
- Can this invocation's write still take effect?
   That decides whether this invocation's own outcome is settled.

**Git state.** Let `O` be the validated head a settlement pass produced.
The pass reports *contains* when `O` equals `attempted_head` or descends from it, *diverged* when `O` and `attempted_head` are unrelated, and *behind* when `O` is a strict ancestor of `attempted_head`, which includes `O == predecessor_head`.
A pass that establishes no head reports *empty* on confirmed exact absence and *unknown* on failure.
Divergence is parked when it is observed, on either pass and regardless of the disposition that follows.

**The write.** The write is *closed* when it is proven it cannot take effect later, and *open* otherwise.
A conclusive failure closes it on its own.
At the branch point only `CasConflictError` is conclusive.
This branch also makes a returned `LocalFolderStore` head-write failure conclusive after repairing its atomic-visibility gap, because a synchronous local call has no request that can take effect later.
The follow-up issue below asks the Hub for a second transport-reported conclusive class.
Otherwise the observation has to prove the write's original condition spent, and that condition differs by write mode.

- A later-chain write is conditional on `predecessor_etag`.
   A comparable `observed_etag` that differs from `predecessor_etag` spends it.
   An observation with no comparable etag proves nothing and leaves the write open.
- An initial write is create-only, so its condition is exact absence.
   Any observed head spends it, whatever that head's etag is, and only confirmed continued absence leaves it open.

Etag identity is load-bearing for later-chain writes alone.
A create-only write's liveness turns on presence, which is the second reason an empty chain needs no etag contract check.

**The disposition.** Combining the two answers takes three rules.

1. An open write is `outcome_unresolved`, whatever Git state the pass observed.
2. Otherwise *contains* is `already_present`.
3. Otherwise a closed write is `integration_required` when *diverged*, and `retryable` for *behind*, *empty*, and *unknown*.

The open-write rule goes first, and it is worth being exact about how little that ordering decides.
Rule 1 preempts rule 2 only where *contains* coexists with an open write.
A create-only write cannot reach that pair: its condition is exact absence, and *contains* requires an observed head, which spends it.
A later-chain write reaches it only when containment shows the head moved while the settlement etag cannot prove closure, being either incomparable or comparable and equal to `predecessor_etag`.
Both are store contradictions already recorded above, so under a conforming store the two rules never compete.
The ordering decides what to report when the store is known to be lying.
An initial pass that finds containment still returns `already_present`, because no write has been attempted and therefore none is open.

The cost is real and the gain is narrow, and both belong in review.
The ordering adds a qualifying clause to two of the five terminal meanings and splits the containment validation property in two.
An earlier draft ordered *contains* first, arguing that claiming uncertainty about a state the store just showed to be present is less useful and no safer.
Half of that stands: if the open write does land it installs `attempted_head`, which still contains `attempted_head`, so a late landing would not falsify `already_present`.
It would destroy the covering descendant's extra commits, and no disposition prevents that, because Cod Sync has already returned.
What the ordering buys is that a caller holding a durable success marker never records an outgoing obligation as discharged on evidence from a store that has just broken its contract.

Rule 1 covers *contains* and *diverged*, so an open write reports `outcome_unresolved` even when either state is visible.
The claim being made is about this invocation's write, which is not settled.
For divergence, the parked ref and merge base ride along so no evidence is lost, and a new invocation observes the same divergence on its initial pass and reports `integration_required` then.
For containment, the observed head and etag ride along, and a new invocation re-observes before it decides whether the outgoing state is settled.

The rules assume three things from the store: a truthful current-head read, an atomic conditional write, and an etag that is never reissued once the head has left it.
The third is what makes "spent" mean permanently spent rather than momentarily unmatched.
It holds because etags derive from stored object bytes (`store.py:173` for `LocalFolderStore`, the provider ETag elsewhere) and every link carries a freshly random `link_id` (`format.py:73-75`), so a head's etag cannot return to a value it has already left.
None of this requires Cod Sync to observe its own write's effect before returning.

Two observations contradict that contract, and both are recorded rather than given a disposition of their own.

- The head moved but its etag did not, meaning `O != predecessor_head` alongside a comparable `observed_etag == predecessor_etag`.
   Content-derived etags make this impossible.
- A later-chain write's head is confirmed absent, though this invocation read that head and chain heads are not deleted.
   The condition is genuinely spent, so the disposition is unaffected, but a store that loses a head has broken more than this write.

A settlement pass that returns a head with no comparable etag is the same contract violation the initial pass refuses, arriving too late to refuse it.
It is recorded the same way and it leaves the write open, which is the conservative reading.

The `retryable` and `outcome_unresolved` split describes this invocation, not every concurrent writer.
CAS keeps a fresh invocation safe while another writer is in flight, because that invocation starts from a new observation and a new condition.
An open write, though, may move the head after Cod Sync has returned.
That late landing may or may not bump `signals.yaml`: if the Hub finishes the request and only its client response is lost the signal is bumped, and if a provider applies the write after the adapter has failed or timed out the Hub raises before reaching `_bump_signal` (`server.py:962-972`).
The latter path can move the head with no update hint.

### A divergent head is parked before Cod Sync stops

A competing head is parked at `refs/cod-sync/parked/{observed_link_uid}` on whichever pass observes it, whether or not its commit was already present locally.
Parking follows the observation rather than the disposition, so an `outcome_unresolved` that saw divergence preserves exactly the evidence an `integration_required` would.
Each observed link has an immutable recovery ref, so one conflict never replaces the only ref for an earlier conflict.

The command that publishes and the command that integrates are separate process invocations for `ssc-files`, and will be separate for Manager Core (#185) and NoteToSelf (#48).
A head that exists only in an exception message would force the recovery path to refetch and re-decide.
The parked ref preserves the exact validated input to the later application operation.

This is the existing peer pattern applied to one's own chain.
`_cod_fetch` (`files.py:683`) parks a peer head at `refs/peers/{teammate_id}/{branch}` (`files.py:618-619`), and `_cod_merge_ref` integrates it later.
Parking is mechanical, so it stays inside Cod Sync.

This is a deliberate break with the existing convention that `pin_to_ref` is caller-supplied and is the only durable ref moved by `fetch()` (`protocol.py:342-346`).
That docstring and the format specification change with the new Cod Sync-owned namespace.
Automatic cleanup of parked refs is out of scope.

A parked ref is the device-local counterpart of the pending contribution pointer sketched in #196.
The mechanisms do not conflict: the parked ref preserves evidence for the losing device, while #196 asks whether a contribution should also be advertised for another device to absorb.

### The Hub reports only one conclusive head-write failure

`SmallSeaStore.put_latest_link` converts transport loss and malformed responses into `PublicationOutcomeUnknownError` (`store.py:414-430`), but a Hub 500 arrives as `StoreProviderError`.
The Hub's S3 adapter catches only botocore `ClientError` (`adapters/s3.py:95`), so a read timeout on the PUT can escape as HTTP 500 and be mapped to a provider error (`store.py:323`).

Among Hub-backed stores, only an explicit `cas_conflict` is conclusive today, and every other head-write failure is inconclusive, including provider errors.
That scoping is load-bearing, because this branch also treats a returned `LocalFolderStore` head-write failure as conclusive, and that store reports failures as `StoreProviderError`.
Conclusiveness is therefore a property of the store rather than of the exception class, while Cod Sync reasons against the `WritableBundleStore` protocol (`store.py:130-147`) and must not inspect which concrete store it holds.
It needs a typed carrier, and a `never_applied` marker on the store exception is the obvious one, since that is the marker the Hub/store issue below would later populate from the Hub side.
Local conclusiveness is optional scope: it unblocks nothing, because bootstrap handles `outcome_unresolved` at the typed boundary regardless, and it changes only which branch some local micro tests take.

All head-write failures take the same observation pass, but the proof a failure carries still matters.
A conclusive failure closes the write on its own, so the disposition follows stored Git state alone.
An inconclusive failure leaves the write open until an observation spends its condition, and an observation that cannot do that yields `outcome_unresolved`.
Reading a provider error as proof that nothing was written would be unsafe, and nothing here does that.

Failures before the head write end the invocation without a settlement pass because no head write was attempted.
Even if an uncertain write-once object upload landed, it remains unreferenced, and a new invocation with new object IDs cannot overwrite stored history.
Those failures are `retryable` with their write phase because the invocation is proven not to have moved the shared head.

Fixing the Hub's outcome classification is out of scope here.
The conservative rule remains correct if the Hub later reports more precise outcomes.

### Cod Sync labels its own write phases

The store exception contract does not need object kind, write phase, write mode, or expected etag added to it.
`_upload` performs the bundle, archived-link, and head writes in a fixed order (`protocol.py:334-336`), so Cod Sync knows those facts at the call site.

Cod Sync wraps failures with its own phase data, and callers never parse store messages.
A known immutable-object collision is `retryable` because no head write occurred and a fresh invocation generates a new candidate.
It is not retried inside the current invocation.

### Applications own semantic integration

The repository merge shapes already differ by application.

- Team Core repos use `core.db merge=splice-sqlite`, installed at team creation and acceptance (`provisioning.py:4711`, `provisioning.py:5089`).
- The `ssc-files` registry uses `*.json merge=binary` (`files.py:758`).
- NoteToSelf installs no merge driver (`provisioning.py:2177-2180`).

This branch introduces no generic merge callback inside Cod Sync.
Each application keeps its own integration operation above the parked ref.

A configured driver is not a runnable driver, and any future automatic merge has to check both.
`.gitattributes` is tracked, but the driver command lives in `.git/config`, which is device-local and untracked, so a device that never ran `_install_sqlite_merge_driver` (`provisioning.py:3014`) has the attribute and no command.
When `shutil.which("splice-sqlite-merge")` misses, that function still configures the bare executable name (`provisioning.py:3027`), so the presence of the Git config key does not prove the command can run.
This branch merges nothing automatically, so the hazard is inert here; the follow-up edits below give it a home in #185 and #48.

Every application-supplied resolution must advance from the observed published head.
Cod Sync never resets shared history or publishes a resolution that does not descend from that head.
#135 still owns the choice between merge and rebase for unpublished local work.

### All current publications are protected

Niches, the niche registry, team Core, NoteToSelf, and bootstrap NoteToSelf are all protected.
No production path in this branch silently discards any of them.

This branch defines no generic drop authorization or executor.
That design waits for a concrete disposable publication with a known transaction boundary and resolution rule.

### `ssc-files` needs a self-store integration path

`push_via_hub` writes the current participant's chain.
Its current advice to pull from a teammate neither identifies the competing sibling-device publication nor invokes the same store (`sync.py:481-488`).

Cod Sync fetches and parks a divergent self-store head on whichever pass observes it.
`ssc-files` exposes an explicit operation that merges that parked ref in the application's work tree and then lets the person publish again.
The operation reports a missing, stale, dirty, or deliberately absent checkout before attempting a merge.
Cod Sync never changes the work tree or selects an application tree.

No end-to-end `ssc-files` flow currently creates a sibling-device conflict.
Micro tests must construct the competing stored chain directly.
CACHED niches may have no checkout, so the integration operation must not assume that every fetched niche is mergeable on this device.

### Manager parks and reports now; integration follows in #185 and #48

`TeamManager.push_team` and `TeamManager.push_note_to_self` preserve typed `retryable`, `integration_required`, and `outcome_unresolved` failures.
They change success markers only when Cod Sync returns `published` or `already_present`.
They do not advertise an integration action that Manager does not yet implement.

Manager's NoteToSelf path can exercise real divergence without new production machinery.
`bootstrap_existing_identity` (`manager.py:35-61`) fetches the participant's NoteToSelf chain into a second installation, and both installations can mutate and publish without one refreshing first.
The branch should use that existing two-installation flow as the end-to-end Manager witness.

Team Core has no equivalent production-quality second-device fixture yet.
`test_linked_device_bootstrap.py` copies the team directory with `shutil.copytree`, so #185 remains the owner of real Core integration and restart-safe conflict bookkeeping.

#48 owns the separate NoteToSelf integration operation, including the current unguarded `refresh_note_to_self` merge, clean-work-tree preflight, merge abort, and the decision whether NoteToSelf gets the SQLite merge driver.

`get_team_sync_status` continues to report outgoing state only.
After `integration_required` its coarse answer may remain `needs_push`, but this branch does not present another push as recovery.
#185 adds concrete Core state, and #35 later turns concrete Core and NoteToSelf states into user-visible actions.

### Niche and registry publication remain one composite operation

Publishing a niche and then its registry can partially succeed.
If the niche is already present and the registry still needs publication, the current early `NothingToPushError` prevents repair (`sync.py:491`).

`push_via_hub` attempts the registry after both `published` and `already_present` niche results.
It reports nothing to push only when both publications are already present.
If one succeeds and the other needs attention, the result identifies both outcomes.

### The policy is durable documentation

The Cod Sync format specification (`format-spec.md:117-124`) and `Documentation/open-architecture-questions.md:136` currently say that Cod Sync does not fetch, merge, or retry on its own, and that the caller rereads instead.
Two thirds of that stays true: Cod Sync still does not merge and still does not retry.
Both documents must say that it now fetches and rereads within a bounded one-write, two-pass envelope, and that the caller no longer rereads on its behalf.

The specification's ETag Semantics section (`format-spec.md:126-131`) describes the etag purely as a conditional-write input.
It must also say that the etag is the evidence a settlement pass uses to decide whether a lost head write can still take effect.
That is what makes its "Google Drive does not satisfy this contract" line load-bearing for correctness rather than only for writes.

## Required result vocabulary

The public publication boundary has five terminal meanings:

- `published`: this invocation received a successful response for its head write;
- `already_present`: the validated stored head equals or descends from `attempted_head`, and no head write remains open;
- `retryable`: this invocation's head write was never issued or is closed, and nothing observed requires application integration before a new invocation;
- `integration_required`: the validated stored head and `attempted_head` diverge, and no head write remains open;
- `outcome_unresolved`: this invocation's head write is still open, so it may yet move the head regardless of the Git state the pass observed.

`published` and `already_present` are ordinary results.
The other three are typed `CodSyncError` subclasses because the requested publication invocation did not finish unattended.

A non-empty chain whose head arrives without a comparable etag is a separate failure and not a sixth meaning.
It says the store cannot support a conditional head write, so it raises like the existing chain checks rather than describing the outcome of a publication.

Three head names appear in results, and they must not be collapsed.

- `attempted_head` is the local `main` commit frozen at the start of the invocation.
- `predecessor_head` is the validated stored head this invocation built its candidate on, absent whenever no candidate was built: an empty chain, or an initial pass that ended in `already_present` or `integration_required`.
- `observed_head` is the validated stored head the result reports on.

Two etags preserve the matching transport facts.

- `predecessor_etag` is the etag of `predecessor_head`, present exactly when `predecessor_head` is, and it is the `If-Match` condition of a later-chain head write.
- `observed_etag` is the etag of `observed_head`, absent when the pass confirmed absence or failed.

Defining `predecessor_etag` by its head rather than by the write keeps it available in dispositions that never wrote.

For `published`, `observed_head` is `attempted_head` and `predecessor_head` is what the head write replaced.
For every other disposition, `observed_head` is what the last observation pass produced.
`observed_link_uid` follows `observed_head`.
Without the middle name, `observed_head` would mean "what the store held before this invocation" in one disposition and "what the store holds now" in the others.

Every result carries `disposition` and whichever of the three heads and two etags apply.
It may carry `attempted_link_uid` for diagnostics, but no result claims that the attempted link is reachable unless it is itself the successfully written head.
The write phase, the failure's proof, and the etags are data on the result rather than distinctions encoded in a message.

`retryable` carries the write phase, the underlying failure, and the observed head, link, and etag when the pass produced them.
When the pass produced no head it carries either confirmed absence or the observation failure.
It is reached two ways, and the write phase says which.
Before a head write it means no head write was issued, and `observed_head` is still `predecessor_head`.
After a failed head write it means the write is closed, either because the failure was conclusive or because the observation spent its condition.
Both closing routes occur in practice: a conclusive failure reaches `retryable` with confirmed absence or a failed observation, and an inconclusive failure reaches it with `observed_head == predecessor_head` when their etags differ.

The post-write form carries a weaker claim than the pre-write one, and callers must not read more into it.
`retryable` after a closed write whose settlement pass failed means no divergence was observed, not that none exists.
The write phase and the observation outcome are on the result so a caller can tell the two apart, and a new invocation re-observes before writing either way.

`integration_required` carries whichever heads and etags apply, `observed_link_uid`, the merge base when one exists, the immutable parked ref, and whether the pass had to import the observed commit.
An initial pass that diverges has no predecessor to report; a settlement pass that diverges does.

`outcome_unresolved` carries whichever heads and etags apply, the open write's link UID, the write phase, and either the observation failure or the observation that left the write open.
When that observation was divergent it also carries the merge base and the parked ref, so choosing the honest disposition never costs the caller evidence.
When it contained `attempted_head`, it carries the covering observed head and its etag, which is the evidence a new invocation's initial pass re-derives.

## Caller dispositions

| Caller | `retryable` | `integration_required` | `outcome_unresolved` |
| --- | --- | --- | --- |
| `ssc_files.files._cod_push` | Preserve the typed write phase and underlying failure for the higher layer. | Preserve the reported heads and the parked ref. | Preserve the open attempt, its observation details, and the parked ref when the pass saw divergence. |
| `ssc_files.sync.push_via_hub` | Say that a new push may be attempted; do not retry internally. | Name the self-store integration operation, not teammate pull. | Say that cloud state could not be proven and retain diagnostics. |
| `TeamManager.push_team` | Leave the success marker unchanged and preserve the typed error. | Leave the marker unchanged and preserve the reported heads and the parked ref. | Leave the marker unchanged and preserve the open attempt. |
| `TeamManager.push_note_to_self` | Leave the adopted signal count unchanged and preserve the typed error. | Preserve the reported NoteToSelf heads and the parked ref. | Leave the adopted count unchanged and preserve the open attempt. |
| `provisioning._push_note_to_self_to_local_remote` | Stop bootstrap with structured retry information. | Stop bootstrap with the parked competing state. | Stop bootstrap with the typed unresolved outcome; do not retry internally. |

Bootstrap publishes through `LocalFolderStore`, whose later-chain replacement is atomic but whose create-only path opens the final pathname before writing its bytes (`store.py:185-200`).
A synchronous I/O failure can therefore leave a partial initial head behind, and a concurrent reader could see one.
Bootstrap is single-process, so residue rather than concurrency is what makes an open outcome reachable here, and either way it contradicts the store's documented atomic-head contract.

This branch repairs `LocalFolderStore` before using it as the contract witness.
The initial head is written and flushed under a temporary name and then installed with an atomic no-replace operation, so exact absence, a complete head, and a CAS refusal are the only visible outcomes.
`LocalFolderStore` also reports a returned head-write failure as conclusive about future liveness: the synchronous call may or may not have installed the head, but it has no request that can take effect later.
The settlement pass still determines which state was installed.
Bootstrap handles `outcome_unresolved` at the typed boundary anyway, rather than depending on the concrete store to make a public result variant unreachable.

`already_present` on a covering descendant proves that the attempted state is stored and that local `main` is behind the stored head.
Both callers that keep durable markers treat the outgoing obligation as discharged and say nothing about the incoming gap.
`push_team` updates its success marker, because this device's Core state is published.
`push_note_to_self` does not advance the adopted incoming-notification count, because the NoteToSelf signal counter is a best-effort hint rather than part of the head transaction, and the existing refresh path adopts a pre-fetch counter snapshot after incorporating fetched state.
Reporting the incoming gap belongs to `get_team_sync_status` and its successors in #185 and #35, not to a publication result.

## Selected application interface

### `ssc-files` merges its parked self-store ref with `--from-self`

The branch gives `ssc-files` a self-store integration operation.
Manager integration and UX follow in #185, #48, and #35.
`ssc-files` has no equivalent follow-up issue, so this branch chooses a concrete interface before implementation.

The existing `merge` command already carries the whole preflight this operation needs — `NoCheckoutError`, `StaleCheckoutError`, `DirtyCheckoutError`, and `PullConflictError` (`sync.py:591-631`) — and differs in where the ref comes from.
The command therefore keeps the `merge` verb and makes the current required `--from-teammate` and the new `--from-self` mutually exclusive sources (`cli.py:176-182`).
The web action keeps the existing `Merge Changes` label inside the self-store conflict panel.
This is the same operation over a different parked ref, reuses the existing preflight, and keeps the new commitment to one narrow source flag that is cheap to rename later.
A separate `reconcile` verb is not introduced without evidence that the shared merge surface is confusing.

The verb is settled; naming the ref is not.
`merge --from-teammate` needs no stored state, because `merge_registry` and `merge_niche` derive the ref from the argument the user typed (`files.py:963`, `files.py:1062`).
`--from-self` has no such derivation, which is the open decision below.

## Settled: how the self-store integration operation finds its parked ref

Parking solves the evidence problem and leaves the discovery problem open.
Settled for step 3: scan the parked namespace.

A peer ref name is derivable from what the user types: `_peer_ref_name(teammate_id)` is `refs/peers/{teammate_id}/main` (`files.py:618-619`).
A parked ref is `refs/cod-sync/parked/{observed_link_uid}`, and that UID exists only on a result object in a process that has already exited, which is the argument for parking in the first place.
`merge --from-self TEAM NICHE` has nowhere to get it.

Two decisions already taken make this harder rather than easier.
Parked refs are deliberately immutable and accumulate, so by the second conflict "the parked ref" is ambiguous.
Automatic cleanup is out of scope, so the namespace is never swept.
The table that would hold a pointer, `peer_sync`, is keyed `(team_id, repo_kind, niche_name, teammate_id)` with columns only for fetched and merged SHAs (`files.py:435-446`), so it carries neither a self identity nor a link UID.

Separate git dirs for the registry and each niche mean the ref namespaces do not collide, so "which chain" is not the problem.
"Which conflict, and is it still outstanding" is.

Three candidates.

- Scan `refs/cod-sync/parked/*` in the target repo and integrate every ref that is not already an ancestor of `HEAD`.
   Needs no schema change and no new durable state, and the ancestor test already exists for exactly this purpose: `merge_registry` and `merge_niche` short-circuit an already-integrated peer ref with `_is_ancestor(git_dir, parked_sha, "HEAD")` (`files.py:967`, `files.py:1066`).
   Costs a scan that grows until #11 and #12 give cleanup an owner, and integrates every outstanding ref rather than a chosen one.
- Record the outstanding parked UID when publication returns `integration_required`, giving `peer_sync` a self sentinel and a parked-link column.
   Makes the operation a precise lookup and reuses the table that already records merges, at the cost of a schema change and a second source of truth that can disagree with the refs.
- Name parked refs by chain and recency rather than by link UID, keeping the UID ref as a second archival ref.
   Makes the name derivable, at the cost of the property that one conflict never replaces the only ref for an earlier one.

The scan is what step 3 implements.
It introduces no state that can drift from the refs, it reuses an ancestry test the peer path already trusts, and its weakness is a research-phase non-issue: parked refs accumulate slowly and the scan is local.
The second candidate is what to reach for if the operation later needs to record a decision that a merge test cannot re-derive.

`_merge_parked_self_refs` first omits parked heads that are ancestors of another outstanding parked head, so random link-UID order cannot force an unnecessary intermediate merge or conflict.
It still retests ancestry before each merge, because an earlier merge in the same run can already have absorbed another incomparable ref.

## Implementation hazards

1. **A failed attempt can leave write-once residue, and not all of it is dead.**
   A bundle and archived link uploaded before a closed head write remain unreferenced, and this branch creates at most one such set per invocation.
   After `outcome_unresolved` they are not provably dead: an open head write that later lands makes exactly those objects the referenced ones.
   Collection stays with #192, which already has to defer sweeping for #196's sake.
2. **An applied write can lose Hub-side completion work.**
   `prepare_encrypted_upload` and `commit_encrypted_upload` straddle the Hub storage adapter (`backend.py:1387-1394`).
   The signal bump also follows the adapter call (`server.py:957-972`).
   If the provider applies a write but the adapter fails or times out, the Hub can skip both sender-key advancement and the update hint.
   Cod Sync-level tests can prove settlement semantics but cannot prove ratchet continuity or notification behavior across that window.
   That witness remains a separate Hub follow-up.
3. **A parked ref is immutable evidence for one observed link.**
   `refs/cod-sync/parked/{observed_link_uid}` is created at the observed head and never advanced to another link's head.
   Re-observing the same link at a different commit is a chain validation failure.
4. **The budget needs an instrument, not just an assertion.**
   "At most one head write and at most two observation passes" is the central claim of the branch, and no existing test can see it.
   Existing publication tests hand-roll wrapper stores (`test_publish.py:314`, `test_publish.py:384`), which is the right shape: one local wrapper that can fail before or after applying the head write, and that counts `put_latest_link` calls and `get_latest_link` passes.
   Settlement micro tests assert exact counts rather than asserting that a loop terminated.
   A counting wrapper is not a general fault-scripting framework and should not grow into one.
5. **Caller contracts change shape.**
   `PublicationOutcomeUnknownError` currently escapes `ssc-files` handlers as a traceback or 500.
   `push_team` already uses the strings `published` and `already_published`, while `push_note_to_self` changes the adopted signal count after a push.
   Call sites and tests must use the new typed meanings rather than matching messages.
6. **Publication now enters the fetch path on its ordinary route.**
   Fetch has no `SyncBudget` yet (#189) and imports objects without verifying authorship (#190).
   Any observation pass may invoke that path when the observed head is not local, and the initial pass is part of every publication, so this is not confined to failure handling.
   This branch adds no second traversal and no validation shortcut, but it does make an ordinary `publish()` reach code that two open issues describe as unbounded and unauthenticated.
7. **The etag contract check changes which failure a Google Drive berth sees.**
   A Google Drive berth cannot publish onto a non-empty chain today either: `_publish_onto` forwards the adapter's `""` etag, which becomes an `If-Match: ""` the provider rejects.
   The check moves that failure earlier and names it, and it breaks no configuration that works now, since `LocalFolderStore` derives an etag from content and Dropbox returns a `rev`.
   Cod Sync tests use `LocalFolderStore` exclusively, so the check adds no test churn.
   Fixing the berth itself belongs to the new issue below, not here.
8. **`LocalFolderStore` does not yet make initial-head visibility atomic, and it is not only a test store.**
   `_create_only` opens `latest-link.yaml` at its final name before it writes and flushes the bytes, so a synchronous failure leaves ambiguous residue.
   `provisioning._push_note_to_self_to_local_remote` builds this store directly and refuses every other protocol (`provisioning.py:1950-1959`), so the repair is a production fix on the identity-bootstrap path rather than test-fixture hygiene.
   It clears the deferral rule because a partial NoteToSelf head endangers developer data, not because the store is convenient for tests.
   The repair is scoped to the head: `put_bundle` and `put_link` share `_create_only` and keep its non-atomic path, because a partial write-once object stays unreachable until the head naming it lands, and the head is written last.
   That scoping leaves the class docstring (`store.py:157-161`) claiming an atomic contract for all three writers, so it must be narrowed to match.
   The branch injects failures around the head's atomic installation before relying on the settlement micro tests.

## Plan

1. Implement the one-write, two-pass result boundary in Cod Sync.
   → verify: frozen `attempted_head`; a micro test for each cell of the settlement matrix, covering a descendant that is not yet local, `observed_head == predecessor_head` with the etag both unchanged and changed, a strict ancestor under a conclusive and an inconclusive failure, create-only absence and create-only contention, confirmed absence after a later-chain attempt, an unreadable observation under both failure classes, an observation whose etag is `None` and one whose etag is `""`, containment under both a closed and an open write, divergence under both a closed and an open write, and an applied-but-unacknowledged head write; a non-empty chain whose head carries no comparable etag raises before any upload; the counting wrapper shows at most one head write and at most two passes on every path.
   Repair `LocalFolderStore` initial-head publication first.
   → verify: readers see exact absence or a complete head, never the staging bytes; a no-replace race yields `CasConflictError`; every returned local head-write failure is closed and carries a typed conclusiveness marker rather than a store identity; the class docstring claims atomicity only for the head; bootstrap preserves a typed `outcome_unresolved` if a wrapper injects one.
2. Implement caller dispositions and the niche/registry composite behavior.
   → verify: no caller parses a store message; user-facing handlers preserve every typed terminal state; Manager's real two-installation NoteToSelf flow witnesses divergence; partial niche success does not prevent registry repair.
3. Implement the explicit `ssc-files` self-store integration operation over the parked ref.
   → verify: the ref survives process exit; the operation finds it without the publishing process's diagnostics; a second, already-integrated parked ref in the same repo is not re-merged; Cod Sync never changes a work tree; dirty, stale, missing, and CACHED checkouts have explicit results.
4. Update user surfaces and durable documentation.
   → verify: every displayed action exists; self-store and teammate-store operations are named accurately; Manager does not advertise unavailable integration; no document claims that Cod Sync never rereads, and none claims that it retries; the format spec's ETag Semantics section says the etag is settlement evidence and not only a write condition.
5. Run targeted and full validation.
   → verify: `uv run pytest packages/cod-sync packages/ssc-files packages/small-sea-manager packages/small-sea-hub`, then `uv run pytest`, then `git diff --check`.

## Validation

The branch is complete when these properties have direct witnesses.

| Property | Witness |
| --- | --- |
| One invocation means one attempted state | Moving local `main` during publication does not change `attempted_head`, bundle contents, ancestry checks, or diagnostics. |
| Settlement never becomes a retry loop | The counting wrapper store records at most one `put_latest_link` and at most two settled `get_latest_link` passes on every path in the settlement matrix. |
| Stored state, not transport provenance, defines ordinary success | On an initial pass, equality and a covering descendant both return `already_present` without walking link IDs, including when the covering commit has to be imported first; after a head-write attempt, the same result also requires a closed write. |
| An ordinary publication may import, and only import | A covering or divergent head that is absent locally is fetched and validated inside `publish()`, and `main`, the work tree, and every application ref are unchanged afterward. |
| A closed write is actionable | A conclusive failure returns `retryable` whether the pass finds a strict ancestor, confirms absence, or fails; an inconclusive failure returns `retryable` once a comparable `observed_etag` differs from `predecessor_etag`, including when `observed_head == predecessor_head`. |
| An open write is never called safe | `observed_etag == predecessor_etag`, an observed etag of `None`, an observed etag of `""`, and confirmed continued absence after a create-only attempt each return `outcome_unresolved`; containment and divergence preserve their evidence but do not override the open write. |
| A broken etag contract is refused, not absorbed | A non-empty chain whose head arrives without a comparable etag raises before any upload, and the counting wrapper records zero `put_latest_link` calls. |
| The local witness obeys the head contract | Fault injection around initial-head staging and installation never exposes partial bytes, and a returned local failure cannot take effect later. |
| Divergence is parked whenever it is seen | A divergent observation parks its ref and reports its merge base under `outcome_unresolved` as well as under `integration_required`. |
| Semantic divergence preserves both histories | `main` does not move, and the observed head remains reachable through its immutable parked ref in a later process. |
| The integration operation finds the right parked ref | With two parked refs in one repo, one already integrated, the self-store operation integrates only the outstanding one and is a no-op on a second run. |
| Current data is never silently dropped | Every production caller treats every current publication as protected. |
| `ssc-files` recovery addresses the right store | A sibling-device conflict uses the participant's `SmallSeaStore`, not a teammate's `PeerSmallSeaStore`. |
| Composite push repairs partial success | An already-present niche does not prevent a registry publication. |
| Manager markers follow observed truth | Success markers change only after `published` or `already_present`; all other outcomes preserve typed details; NoteToSelf push does not adopt its own signal. |
| Checkout assumptions are explicit | Attached-clean, attached-dirty, stale, absent, and CACHED niche states exercise the selected application policy. |
| Documentation matches behavior | Durable docs describe the one-write, two-pass boundary, no internal retry, the Cod Sync-owned parked namespace, and the etag's settlement role. |

# Follow-up

The proposals below are drafts only.
No GitHub issue has been edited or filed from this branch yet.

## Edit issue #191 before implementation

Clarify that "finite mechanical settlement" means at most one head write and at most two validated observation passes, and that a pass may import through the existing fetch path.
State that containment of `attempted_head` in the validated observed Git history is sufficient for ordinary success only when no head write remains open; initial-pass containment meets that condition because no write was attempted.
Remove any implication that #191 retries a known race, proves an attempted link reachable, or depends on #189.
Add `retryable` as the terminal result when the invocation's head write was never issued or is closed, and nothing observed requires application integration before a new invocation.
State that stored Git state decides containment versus integration while the failure's proof and the observed etag decide whether this invocation's write is closed or open.
The open-write answer is applied first: containment becomes `already_present` and divergence becomes `integration_required` only when no write remains open.
Say that this ordering is only reachable when the store has already contradicted its own etag contract, so it decides reporting under a broken store rather than ordinary behavior.
Include the create-only empty-chain condition, whose liveness turns on presence rather than on any etag.
Note that #191 refuses to build on a non-empty chain whose head arrives without a comparable etag, and that this is a store-contract failure rather than a sixth disposition.
Include the `LocalFolderStore` initial-head atomicity repair because that store is both the settlement contract witness and the store identity bootstrap actually publishes through.
Note that the repair covers the head only, and that local head-write conclusiveness needs a typed carrier on the store exception rather than knowledge of which concrete store is in use.

## Proposed edits to existing issues

- **#189 — Bound unattended Cod Sync work with SyncBudget.**
   Keep it deferred and independent of #191.
   Record that `publish()` now calls the existing fetch walk whenever an observed head is not local, on its ordinary route and not only after a failure, so that call inherits `SyncBudget` when #189 lands.
   #191 adds no separate reachability traversal and needs no special budget disposition.
- **#185 — Manager Core peer fetch and integration primitives.**
   Extend its parked-source vocabulary to include a sibling-device conflict on the participant's own Core chain.
   Add validation that the self-publication conflict survives a fresh `TeamManager`, integrates through the Core merge driver, clears only its matching record, and can then be published.
   Record that integrating through the Core merge driver must confirm the driver is runnable, not merely configured: `.gitattributes` is tracked while the driver command is device-local, and `_install_sqlite_merge_driver` configures a bare executable name when `shutil.which` misses it (`provisioning.py:3014-3036`).
- **#35 — Notification-driven parked-update UX.**
   Add #185 and #48 as the sources of concrete parked state.
   Include self-conflict wording that does not invent a teammate source, and offer an integration action only when the corresponding operation exists.
- **#48 — Manager multi-device NoteToSelf sync.**
   Add explicit integration of #191's self-store parked ref, clean-work-tree preflight, conflict rollback with retained ref, and restart-safe state.
   Bring the existing unguarded `refresh_note_to_self` merge under the same contract.
   Decide there whether NoteToSelf gets `splice-sqlite`, and if it does, inherit #185's requirement that the driver be checked for runnability rather than configuration.
   Keep signal adoption tied to successfully incorporated state.
- **#135 — Rebase vs merge for unpublished local changes.**
   Record that #191 preserves the observed published head and the unpublished attempted head without choosing the integration method.
- **#196 — Pending contribution pointers.**
   Record that #191's parked ref is the losing device's local evidence and does not preclude a team-visible pending contribution pointer.
- **#190 — Verify Cod Sync link signatures on the fetch path.**
   Widen its problem statement to include publication, because an ordinary `publish()` now enters the existing fetch/import path whenever the stored head is not local.
   The unverified-authorship gap is therefore reachable without any failure occurring.
- **#193 — Cache validated chain state.**
   No behavior in #191 depends on it, and #191 introduces no validation shortcut keyed on a link UID alone.
   Record only that its value grew: an ordinary `publish()` already revalidates the stored head's bundle and may now also import and validate a chain it did not previously touch.
- **#192 — Collect orphaned Cod Sync objects.**
   No issue edit is required: its existing concurrent-publication validation already owns the behavior.
   Record here why that constraint is now load-bearing for correctness rather than only for #196: after `outcome_unresolved`, an open head write can reference the very bundle and link the sweep would call orphaned.

## New issues to file

### Add bounded automatic retry for proven Cod Sync publication races

File only when tests or real use show that asking for a new publication invocation is materially painful.

Scope:

- depend on #189 and use `SyncBudget` rather than adding a publication-only counter;
- retry only invocations proven not to have moved the shared head;
- rebuild a successor only from a validated current head and etag;
- stop immediately on divergence, uncertainty, or budget exhaustion;
- account for write-once residue and repeated validation cost;
- add exact attempted-link reachability only if a concrete consumer needs transport provenance rather than Git-state containment.

Validation must inject repeated known races, prove termination, prove that an open write is never retried, and keep all network behavior local.

### Distinguish never-applied and unknown conditional writes at the Hub/store boundary

The S3 adapter catches only `ClientError`, so a provider timeout after a conditional PUT can become a generic HTTP 500 and `StoreProviderError`.
Give the Hub/store boundary an explicit uncertain-write outcome for timeouts and other failures that do not prove refusal, and a `never_applied` outcome for failures that prove the request never reached the provider.
For #191, `never_applied` carries the same useful proof as a CAS refusal: it closes this invocation's write.
It does not claim that another writer left the precondition intact.
Keep Cod Sync's rule as it stands so correctness does not depend on perfect provider classification.

State the payoff honestly, because it is narrow.
A `never_applied` classification closes the write by itself, which converts `outcome_unresolved` to `retryable` in exactly the cases where the settlement observation could not spend the condition: an unchanged etag, an incomparable etag, or a failed pass.
The later invocation still performs its own initial observation before writing.
That is worth doing before deployment and is not worth doing to unblock #191.

### Enforce the `latest-link.yaml` etag contract at the Hub storage boundary

The format specification requires an etag on every `latest-link.yaml` download and upload and already declares Google Drive non-conforming (`format-spec.md:126-131`).
Nothing enforces that.
`get_latest_link` is typed `Optional[str]`, `SmallSeaStore._download` reads `body.get("etag")`, and the Google Drive and Dropbox adapters substitute `""` for a missing `ETag` or `rev`.
Dropbox returns a `rev` in practice, so Google Drive is the live gap: `backend.py:1309` still constructs its adapter for a berth cloud, and such a berth cannot publish onto a non-empty chain.

Either make the adapter satisfy the contract or refuse to materialize a berth cloud that cannot, so the failure surfaces at berth setup rather than at a participant's second publication.
#191 does not depend on this.
It refuses to build on an incomparable etag and says why, which is the honest behavior whether or not the berth layer is ever fixed.

### Add Hub-layer response-loss fault injection across encryption and notification

`prepare_encrypted_upload` and `commit_encrypted_upload` straddle the storage adapter, while Cod Sync-level fakes sit above both.
The signal bump occurs later still, after the adapter reports success.
Add injection points that can apply an encrypted write and then lose the provider response, lose the Hub response after sender state commits, or fail the signal bump.
Prove that settlement can read the applied ciphertext, that a later publication remains decryptable without sender/receiver state loss, and that each window's notification behavior is explicit.
In particular, do not claim that a provider-side late landing emits an update hint when the Hub never observed success.

## Still unowned

- Automatic cleanup of parked refs, which pin history against local pruning (#11, #12).
   The open decision above makes this more than a pruning concern: whichever discovery mechanism is chosen has to stay correct while the namespace accumulates unswept.
- Generalizing application integration after `ssc-files`, Core, and NoteToSelf provide concrete operations to compare.
- Drop-on-conflict, revisited only when a concrete disposable publication has an exact transaction boundary and reviewed descendant-resolution rule.
