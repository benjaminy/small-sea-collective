# Notes

Branch for [issue #187](https://github.com/benjaminy/small-sea-collective/issues/187):
replace Cod Sync's synthetic Git remotes with direct bundle plumbing.
Blocks #185, which needs a clean-work-tree preflight that today's generated `.codsync-bundle-tmp/` directory would break.

The intended result is small to state.
Cod Sync should exchange bundle and link files through a store while `Repo` performs the corresponding Git plumbing directly, without synthetic remotes, remote-tracking refs, temporary tags, or work-tree scratch paths.

### Departure from the issue text

#187's scope says "fall back to a full main bundle when the prerequisite is absent."
This branch does not implement that bullet.
A full bundle proves that the objects it carries are complete; it proves nothing about whether the new head descends from the head the store already advertises.
Today's fallback therefore replaces a chain containing a teammate's commits with one that does not, and reports success.
The forward-only publication rule below replaces it.
Follow-up records the issue edit.

## What exists today

All of the machinery under discussion lives in `packages/cod-sync/cod_sync/protocol.py` (934 lines), class `CodSync`.

`CodSync.__init__(remote_name, bundle_tmp_dir=None, repo_dir=None)`.
`remote_name` names two Git remotes and a scratch subdirectory.
`bundle_tmp()` returns the pair `("{remote_name}-codsync-bundle-tmp", "{base}/{remote_name}")`,
where `base` defaults to `{repo_dir}/.codsync-bundle-tmp` inside the work tree.

Synthetic remotes:
- `add_remote` registers `{remote_name} -> codsync:{url}` and `{remote_name}-codsync-bundle-tmp -> {tmp}/fetch.bundle`.
  The first is URL storage for the CLI rather than a URL Git can fetch.
- `_ensure_bundle_remote` re-adds the bundle remote lazily during fetch.
- `fetch_chain` downloads each bundle to the fixed path `{tmp}/fetch.bundle`, then runs `git fetch {tmp_remote}`.
  That creates `refs/remotes/{tmp_remote}/*`.
- `merge_from_remote` and `clone_from_remote` consume `{tmp_remote}/main`.

Temporary tag:
`push_to_remote` creates `codsync_temp_tag_main` at the stored head so it can name `tag..main`, then deletes the tag.
The tag-creation failure path is the unsafe fallback described above.

Head validation:
`fetch_from_remote` returns the SHA claimed by link metadata without checking that the bundle advertises that SHA.

Scratch paths:
`clone_from_remote` already uses `tempfile.TemporaryDirectory()` for its initial bundle.
`push_to_remote` and `fetch_chain` do not.
`ssc-files` works around the work-tree pollution with `_bundle_tmp_dir(git_dir)` (files.py:314), pointing inside the Git directory.

Quadratic clone:
`clone_from_remote` calls `fetch_chain(link, ["main"], doing_clone=True)` once per link in `chain[1:]`,
and `doing_clone=True` forces `follow_chain = True` unconditionally.
Each call therefore recurses back to the initial snapshot, so an n-link chain performs O(n^2) downloads through a single reused `fetch.bundle` path.
The rewrite removes this incidentally, but it is worth a witness rather than an assumption.

Link contents:
`build_link_blob` writes a `[branch, sha]` entry for every local branch returned by `get_branches()`, while only `main` is ever bundled.
`get_branch_head_sha` returns the literal string `"0xdeadbeef"` when a branch does not resolve, so a link can carry a value that is not a SHA at all.

Vestigial parameters:
`push_to_remote(branches, ...)` uses its `branches` argument only in a log line, then overwrites it when unpacking `latest_link`.
`fetch_chain(link, branches, doing_clone)` shadows `branches` on its first line and only ever passes the shadowed value down its own recursion.
Cod Sync is single-branch in practice: publication asserts `branch[0] == "main"`, and every caller passes `["main"]`.

Dependency direction:
`gitCmd` and `GitCmdFailed` are imported from `protocol.py` by `repo.py:13`, `ssc_files/files.py:28`, and `ssc-files/tests/test_files.py:828`.
Making `protocol.py` use `Repo` without first moving those primitives would create a circular dependency.

The unchanged-head signal:
`git bundle create` exits 128 with `fatal: Refusing to create empty bundle.` when the requested range is empty, which is what happens today when local `main` already equals the stored head.
`ssc_files/sync.py:486` string-matches that message to raise `NothingToPushError`, and `sync.py:500` string-matches it again to swallow the same condition for the registry.
Nothing else in the repository depends on the message, and no test asserts `NothingToPushError`.

Methods that survive the deletions below and still need a disposition:
`merge_from_ref` (a bare `git merge` wrapper that `Repo.merge` duplicates; sole caller is `test_roundtrip.py:75`),
`change_to_root_git_dir` (sole caller is `cli.py`, which this branch deletes),
`get_branches`, and `get_branch_head_sha`.

## Git plumbing that replaces the remotes

- `git bundle create <path> ^<prereq-sha> main` creates an incremental bundle without a temporary tag.
- `git bundle list-heads <path>` inspects advertised refs without importing objects.
- The bundle header names the bundle's actual prerequisite commits, but no Git command reports them without also requiring them to be present, so reading them means parsing the header.
- `git bundle verify <path>` checks that the bundle is valid and its actual prerequisites are present.
- `git bundle unbundle <path>` imports objects and prints advertised refs without creating refs or writing `FETCH_HEAD`.
- `git cat-file -e <sha>^{commit}` checks for a prerequisite commit.
- `git merge-base --is-ancestor <stored-head> <local-head>` proves that publication advances the stored Git history.
- `git update-ref <name> <new-sha> <old-sha>` compare-and-swaps the one explicit durable ref requested by a caller.

### The bundle header

Only `bundle_prerequisites` reads the file format directly; everything else above is a Git subprocess.
Verified against git 2.50.1:

```
# v2 git bundle              <- or "# v3 git bundle"
@object-format=sha1          <- v3 only; zero or more @capability=value lines
-<sha> <commit subject>      <- zero or more prerequisite lines
<sha> refs/heads/main        <- one or more advertised ref lines
                             <- blank line terminates the header
PACK...
```

A prerequisite line carries an ignorable comment after the SHA, so parsing splits on the first space and retains only the object ID.
The parser accepts the generic v3 capability grammar instead of maintaining its own allow-list.
Git remains authoritative for capability support and rejects a capability it cannot process.
The parser rejects an unknown signature, malformed ordering, or malformed header line rather than skipping it.

## Callers that must move

Production:
- `small_sea_manager/manager.py` — `refresh_note_to_self` uses `fetch_from_remote` plus `merge_from_remote`;
  `bootstrap_existing_identity` (manager.py:43-63) uses `fetch_from_remote` plus `Repo.checkout_branch`;
  `TeamManager` publishes at manager.py:213 and manager.py:731.
  `push_note_to_self` advances its adopted signal count unconditionally after publication, so it must distinguish a real publication from an unchanged no-op.
- `small_sea_manager/provisioning.py` — publication at provisioning.py:1961, fetch at provisioning.py:2468,
  and the invitation-acceptance fetch at provisioning.py:5082, which changes the process working directory because it constructs `CodSync("inviter")` without `repo_dir`.
- `ssc_files/files.py` — `_cod_push`, `_cod_pull`, and `_cod_fetch`.
  `_cod_pull` names `"cloud-codsync-bundle-tmp"` directly, and `_bundle_tmp_dir` exists only to relocate Cod Sync scratch.
  `push_niche` (files.py:998) and `push_registry` (files.py:945) discard `_cod_push`'s result.
- `ssc_files/sync.py` — `push_via_hub` (sync.py:460) translates Cod Sync failures into the app-facing error vocabulary,
  including the two `"Refusing to create empty bundle"` string matches noted above.
- `scripts/setup_dropbox_workspace.py:97` — constructs `CodSync("cloud")` with no `repo_dir`, so it depends on the process working directory.
  This is the second cwd-dependent construction site, not a variant of the first.
- `cod_sync/cli.py` — built on `add_remote`, `remove_remote`, and `initialize_existing_remote`.
  It cannot run: `main` reads `args.remote` from module scope, where `args` is bound only under `__main__`,
  and `[project.scripts]` (pyproject.toml:12-13) binds `cod_sync.cli:main`, a three-argument function, as a console script.

Out of scope:
`ssc_files/files.py` runs its own Git commands through `gitCmd` for work-tree operations Cod Sync never performed
(`_cod_merge_ref`, `_has_commits`, `_conflict_paths`).
Those keep working against `cod_sync/git.py` after unit 1.
Moving `ssc-files` onto `Repo` is a separate change.

Tests:
`packages/cod-sync/tests/`, `packages/small-sea-manager/tests/`, `packages/ssc-files/tests/`,
`packages/small-sea-hub/tests/`, and the top-level smoke and roundtrip tests.
Most call only `push_to_remote` or `fetch_from_remote`.
`test_roundtrip.py`, `test_merge_conflict.py`, `test_device_link.py`, and `tests/test_sync_roundtrip.py` use `add_remote` or `merge_from_remote`.

Documentation:
`Documentation/formats-etc.md` is the only document that names the machinery being deleted,
both as a method list (lines 39-50) and inside a mermaid class diagram (lines 88-112).
`packages/cod-sync/README.md` describes the chain-of-deltas format and compaction policy and never mentions remotes, scratch paths, or tracking refs.
`packages/cod-sync/Documentation/design.md` contains one line: `some design`.

Store boundary:
`CodSyncRemote.read_link_blob` parses YAML inside the storage abstraction, and each Hub-backed `_download` maps every non-200 response to `(None, None)`.
An absent object is therefore indistinguishable from expired authorization, a provider outage, or a Hub failure.
`LocalFolderRemote.upload_latest_link` writes `latest-link.yaml` before its archived link and performs its compare and write as separate filesystem operations,
so it neither models the documented write order nor provides an atomic CAS witness.

## What the existing tests actually cover

Unit 2 rewrites the least-covered code in the package, so "the existing tests still pass" is close to meaningless there.
Establishing this now, because two earlier drafts of this plan leaned on tests that do not test what their names claim.

- `packages/cod-sync/tests/test_roundtrip.py` says it exercises "push_to_remote (incremental)" and "fetch_chain (following prerequisite links)".
  It does neither: Bob publishes to `bob_pub`, an empty publication directory, so that push takes the initial-snapshot path and Alice then fetches a one-link chain.
- Every `clone_from_remote` call in the suite clones a **one-link** chain
  (`test_clone_from_local_bundle.py:86`, `test_roundtrip.py:46`, `test_device_link.py:105`, `test_merge_conflict.py:72`).
  The `for link in chain[1:]` loop in `clone_from_remote`, and therefore the quadratic recursion above, is never executed by a test.
- Genuine incremental publication is covered only by `test_cas_behavior.py:35-49`.
- Genuine incremental fetch is covered only at chain length two, by `test_device_link.py:114-118` and `test_merge_conflict.py:84-96`.
  In both, the prerequisite is already present locally, so `fetch_chain`'s backward recursion never runs.
- `NothingToPushError` has no test, so the new typed no-op path could make it unreachable without turning the suite red.

No test in the repository walks a chain backward across more than one missing link, and none builds a chain longer than two links.

## Trust and failure boundary

Cod Sync validates transport structure.
It proves that a bundle advertises the head a link declares, that the bundle's actual Git prerequisites match the link's declared prerequisites,
and that the resulting commit graph has the promised ancestry.
It does not prove authorship, membership, complete disclosure, social acceptance, or that a fetched history should have local effect.
Fetching remains separate from merging or otherwise adopting a head.

### Signatures are produced but not checked

`build_link_blob` signs a link when given a signing key, and `verify_link_signature` exists,
but its only callers are `test_device_link.py:143` and `test_signed_bundles.py:199`.
No production code path verifies a link signature before or after fetching.
This branch does not change that, and tightening structural validation must not be read as closing the authorship gap.
Follow-up carries the issue.

Signature scope is worth stating because main-only publication touches it:
`canonical_link_bytes` covers the `branches` list, so narrowing a link to declare only `main` narrows what a signature attests to.
Verification recomputes canonical bytes from the link's own contents, and the new codec preserves unknown extension fields in those bytes.
This cryptographic property does not create a version-1 compatibility obligation.

### Store placement

Production stores continue to send every Small Sea network request through the Hub.
The local-folder store performs local filesystem I/O, and the direct-S3 implementations remain testing infrastructure rather than production exceptions to the gateway rule.

### Bounded unattended work is deferred

This branch does not add resource-policy limits to otherwise valid publication or fetch work.
That policy and its intervention vocabulary are tracked in #189 so the format, store, coordinator, and caller transition can land without a second orthogonal test matrix.
Cod Sync still rejects malformed or contradictory data and terminates cyclic traversal in this branch; it does not yet pause a valid operation because its link count or byte volume is unexpectedly large.

Issue #189 will add finite named defaults, progress diagnostics, and explicit larger-budget continuation without weakening structural, ancestry, or bundle validation.
Streaming transport and a hard pre-download byte ceiling remain distinct because the current Hub JSON/base64 path may materialize an object before Cod Sync can inspect its size.

### Divergence

Publishing when the stored head equals local `main` is a successful no-op.
Publishing when the stored head is missing locally or is not an ancestor of local `main` requires integration that Cod Sync cannot choose,
especially because Cod Sync may be operating on a repository with no work tree.
`publish` raises `PublicationIntegrationRequiredError` before uploading anything.
The error reports the stored head, local head, link UID, and the merge base when both commits are present.
It does not automatically download, merge, or retry.

State the consequence plainly.
With no server serializing writes, divergence is the ordinary outcome whenever a second device or teammate publishes between this device's last fetch and its next publish.
After this branch that case raises instead of silently discarding the other party's commits, and no caller resolves it automatically,
so the user-visible result is a publication that fails and stays failed until someone pulls.
That is the intended trade: loud and stuck beats quiet and wrong.
Resolution needs a fetch, a merge, a work tree, and a retry across a CAS window, and it needs a per-caller answer to how much of that should happen without asking.
Cod Sync is the wrong layer to decide it, so it stays above Cod Sync and out of this branch.
Follow-up carries the caller-level policy, including the eventual case for cheaper handling of updates that are low-stakes enough to drop on conflict.

### Rejected store input

Ordinary store UIDs are random locators rather than content addresses.
This branch makes archived links and bundles write-once in every writable store, but a UID still does not authenticate its bytes.
A rejected fetch therefore reports the link UID, bundle UID, declared head, advertised head, and declared versus actual prerequisites,
and the store preserves the received archived objects against later overwrite.
The operation retains no automatic quarantine and always cleans its temporary directory.
Retaining arbitrary rejected plaintext before there is a storage and resource policy would create a different security problem.

### Orphan bundles

Both `LocalFolderRemote.upload_latest_link` (protocol.py:576) and `SmallSeaRemote.upload_latest_link` (protocol.py:694) write the bundle before the CAS check on `latest-link`.
A conflicted or rejected publication therefore leaves an unreferenced bundle in the store, and nothing collects it.
Forward-only publication and create-only first publication make rejections more frequent by design, and the store is the user's own cloud account.
This branch preserves the required bundle, archived-link, conditional-head upload order because the final head write is the serialization point.
Follow-up carries collection, which will likely need more than one tactic.
Collection should not treat "unreferenced" as "collectible now."
A bundle that lost a head-link race is the same artifact a pending-contribution pointer would later reference, so immediate deletion would foreclose that option.

### Initial-publication race

The format specifies a create-only write for the first `latest-link.yaml`, but the current Hub and local-folder paths treat `expected_etag=None` as an unconditional overwrite.
The `"*"` create-only conditional-write marker already works at the adapter layer (`adapters/s3.py:66`, `adapters/dropbox.py:62`),
but current Cod Sync does not use it and gives every first archived link the fixed ID `initial-snapshot`.

This branch closes the race.
Every real link, including the first, receives a random self-ID.
Version 2 represents the first link with `previous: null`; it has no sentinel ID and its full bundle has no prerequisite.
The store writes the random bundle and archived link with create-only semantics, then creates `latest-link.yaml` conditionally.
One racing first publisher wins; the other receives `CasConflictError` and leaves only write-once orphan artifacts.

The old positional link shape carries branch and bundle lists that Cod Sync does not support semantically, and old readers identify the first link by a fixed self-ID.
Version 2 replaces it with a named mapping for the one supported head, bundle, predecessor, and extension point rather than preserving those vestiges.
`COD_SYNC_VERSION` therefore becomes `2.0.0`.
There is no version-1 compatibility reader or migration shim.
Nothing is deployed, so compatibility work would add machinery without preserving real data; the version marker remains only as a clean protocol boundary for future evolution.

### Version-2 link shape

Version 2 replaces the positional list with a named YAML mapping:

```yaml
version: 2.0.0
link_id: 0123456789abcdef
head: <main-commit-object-id>
bundle_id: fedcba9876543210
previous:
  link_id: 1111111111111111
  head: <previous-main-commit-object-id>
extensions: {}
```

The first link has `previous: null`.
Its bundle is full and has no Git prerequisite.
An incremental bundle must include `previous.head` among its actual Git prerequisites.
Any extra prerequisite must be an ancestor of `previous.head`, and the archived link named by `previous.link_id` must declare that head.
`head` always means `main`; version 2 has no branch or bundle collection whose extra entries would be ignored.

The canonical signed value is this mapping with only `extensions.signatures` removed.
Every other extension key remains covered and survives decode/encode round trips.
Minor and patch evolution may add ignorable data inside `extensions`; a change that affects traversal, validation, or adoption semantics requires a new major.

## Settled for this branch

1. **One-way modules with one owner per concern.**
   `gitCmd` and `GitCmdFailed` move to `cod_sync/git.py`.
   `repo.py` owns repository operations, `format.py` owns the current link model and wire codec, `store.py` owns opaque storage transport,
   and `protocol.py` coordinates publication and fetch without executing Git or parsing YAML itself.
   `ssc_files/files.py` imports the low-level Git helper directly for its existing work-tree operations.
   There are no compatibility re-exports.
2. **Explicit repository and store ownership.**
   The constructor becomes `CodSync(repo: Repo, store: ReadableBundleStore)`.
   Publication requires a `WritableBundleStore`.
   There is no path inference, cwd fallback, mutable `cod.remote` assignment, `remote_name`, or `bundle_tmp_dir` parameter.
3. **No clone convenience operation.**
   The caller performs `Repo.init`, constructs `CodSync`, calls `fetch`, and checks out `FetchResult.observed_head`.
   Every `clone_from_remote` caller is a test or the CLI this branch deletes; production invitation acceptance already uses this flow.
4. **Names and typed results describe the surviving operations.**
   `push_to_remote` becomes `publish` and `fetch_from_remote` becomes `fetch`.
   Cod Sync publishes and fetches only `main`, so both vestigial `branches` parameters disappear.
   `PublishResult(head, changed, link_uid)` distinguishes a new publication from an unchanged success.
   `FetchResult` reports the observed validated head, the resulting pinned head when requested, pin disposition, and operation resource counts.
   An empty store raises `NoPublishedHeadError`; ordinary success is never represented by `None` or an integer exit code.
5. **Stores are dumb, truthful byte transports.**
   A store reads and writes opaque link bytes, bundle files, and ETags; it does not parse YAML, normalize prerequisites, or know Git semantics.
   `CodSyncRemote` becomes `ReadableBundleStore` and `WritableBundleStore`, with `LocalFolderStore`, `SmallSeaStore`, `PeerSmallSeaStore`,
   `ExplicitProxyStore`, `BootstrapProxyStore`, and the testing-only `S3Store` and `PublicS3Store` implementing the applicable protocol.
   Exact absence is the only result interpreted as an empty store or missing predecessor.
   Authorization, transport, provider, malformed-response, CAS-conflict, and outcome-unknown failures remain distinct typed errors.
   The Hub and provider adapters preserve that distinction end to end instead of translating every failed provider read into HTTP 404.
6. **First publication is create-only and archived objects are write-once.**
   Every real link has a random self-ID; the first link has `previous=None` and its full bundle has no prerequisite.
   Writable stores create the bundle and archived link without replacement, then create or compare-and-swap `latest-link.yaml` atomically.
   Before a later publication names the current link as its predecessor, the current archived link must exist and byte-match `latest-link.yaml`.
   `LocalFolderStore` obeys the same ordering and CAS semantics as Hub-backed storage rather than serving as a weaker approximation.

   *Amended after review.*
   Google Drive file names are not unique, so the current lookup-then-create adapter cannot make first publication atomic.
   Keep the adapter functional for testing, but classify it as non-conforming and unsafe for real writable Cod Sync data.
   S3, Dropbox, and `LocalFolderStore` remain the conforming writable implementations.
   A provider-ID-keyed Google Drive design is follow-up work; this branch adds no runtime gate or capability abstraction.
7. **Fetching moves no implicit ref.**
   No remote, remote-tracking ref, `FETCH_HEAD`, or temporary tag is created.
   `fetch` returns a `FetchResult` and moves only an explicitly requested pin, after the requested publication validates.
8. **Explicit pins only move forward.**
   Pin creation and advancement use `git update-ref` compare-and-swap.
   An absent pin is created, an ancestor pin advances, an equal pin is unchanged, and an out-of-order fetch whose observed head is an ancestor of the pin retains the newer pin and reports `stale`.
   `Repo` reports divergent refs as `RefDivergedError`; `fetch` adds publication context and raises `PinIntegrationRequiredError` without moving the ref.
   Repeated CAS contention stops after a small fixed number of rereads with `RefAdvanceContendedError`; it never spins until the competing writer becomes quiet.
9. **Link metadata must match both parts of the bundle header.**
   The advertised `refs/heads/main` must equal the link's declared `main` head.
   An initial bundle must have no actual Git prerequisites.
   An incremental bundle must build on the prerequisite its link declares, and may need no others outside that history.

   *Amended during unit 2.*
   The original rule said an incremental bundle's actual prerequisite set must **exactly equal** the declared one.
   That is unimplementable.
   `git bundle create ^<stored-head> main` records a prerequisite for every boundary commit,
   so merging a branch rooted before the stored head yields two or more prerequisites for a perfectly ordinary publication.
   Merging a teammate's parked ref and then publishing is a core Small Sea workflow, and exact equality forbids it outright.
   The implemented rule keeps the property the strict version was reaching for:
   the declared predecessor must be among the actual prerequisites, and every extra prerequisite must be an ancestor of it,
   so anyone who can satisfy the declared prerequisite can satisfy the whole bundle.
   A prerequisite outside that history is a hidden dependency and the chain is rejected before any ref moves.
   The ancestry check runs wherever the objects are present, and a bundle that reaches import unsatisfiable
   is reported as a typed chain error rather than a bare `git bundle verify` failure.
10. **Every newly observed store head is validated as a publication.**
    Local possession of the declared commit is not proof that this store's bundle advertises it.
    Fetch always downloads and inspects the latest link's bundle before returning or moving a pin, even when the declared head already exists locally.
    Publication does the same before reporting an unchanged success or extending an existing chain.
    When the head and prerequisite already exist, that validation avoids predecessor traversal and import, but not the latest bundle read.
    Caching exact previously validated link and bundle content is a later performance optimization after the write-once store invariant exists.
11. **Every operation uses one OS temporary directory.**
    It is outside every work tree and is cleaned on ordinary success and failure.
    This overrides `ssc_files._bundle_tmp_dir`, which deliberately put scratch under `$GIT_DIR`.
    Both locations satisfy #185's clean-work-tree preflight, but the OS temporary directory is outside folder-syncing applications and is eligible for platform cleanup after a process crash.
    The accepted cost is that `TMPDIR` may be size-limited; insufficient temporary storage fails the operation rather than falling back to repository scratch.
12. **A non-empty store may only move forward.**
    Before ordinary publication the stored `main` head must exist locally and be an ancestor of local `refs/heads/main`.
    Otherwise publication stops without uploading a bundle or link or moving `latest-link`.
    A full snapshot is valid only for an empty store or for explicit future compaction that preserves forward Git ancestry.
13. **Publishing an unchanged head is an explicit no-op.**
    It uploads nothing, leaves `latest-link` and its ETag unchanged, and returns `PublishResult(changed=False)`.
    A new head returns `PublishResult(changed=True)` regardless of the writable store method's internal return value.
    The two `"Refusing to create empty bundle"` string matches move onto the typed result.
14. **Publication integration remains caller policy.**
    Missing-prerequisite and non-ancestor publication failures use `PublicationIntegrationRequiredError` with structured diagnostics.
    Current callers propagate it, or translate it into their existing domain error, without automatically fetching, merging, or retrying.
    A final-head-write response that may have been lost uses `PublicationOutcomeUnknownError`; callers reread rather than blindly retry.
15. **The current wire format is strict at its core and open at its declared extension point.**
    `COD_SYNC_VERSION` becomes `2.0.0` because the initial-link semantics and wire representation change.
    The supported major is a YAML mapping with exactly `version`, `link_id`, `head`, `bundle_id`, `previous`, and `extensions`.
    `head` is implicitly `main`.
    `previous` is either `null` for a full initial bundle or exactly `{link_id, head}` for an incremental bundle whose Git prerequisite is that head.
    Unknown keys inside `extensions` are accepted, preserved, and covered by canonical signing; unknown top-level keys are rejected.
    A chain's versions remain monotonically non-decreasing.
    An unsupported major raises a typed upgrade/intervention error rather than being mislabeled corrupt.
    No speculative multiple-parent model or compatibility decoder is built now; a later semantic change gets a new major and decoder when its requirements exist.
16. **Cod Sync transports only `main`.**
    Version 2 has one `head` field whose meaning is `main`; it has no branch list.
    Link construction no longer enumerates local branches and never writes the `"0xdeadbeef"` placeholder.
17. **Chain traversal rejects malformed structure.**
    It keeps a visited-link set and rejects cycles, missing predecessors, archived links whose self-ID does not match the requested UID,
    malformed `previous` values, inconsistent predecessor heads, version regression, head mismatch, and prerequisite mismatch.
18. **Caller adoption moves atomically with fetch validation.**
    The old fetch returns an unvalidated link-declared SHA, so callers do not switch to that return value in a separately green intermediate unit.
    Fetch validation, caller migration to `Repo.merge` or `Repo.checkout_branch`, and deletion of tracking-ref consumers land together.
19. **Delete the broken CLI.**
    `cod_sync/cli.py` and its `[project.scripts]` entry have no working or non-CLI contract worth retaining in this research project.

## Behavior a caller can observe change

Everything else in this branch is internal.
These are the differences a caller or a person can see, and each one has a row in Validation.

- Publishing onto a store whose head is not an ancestor of local `main` raises instead of replacing the stored chain and reporting success.
- Publishing returns `PublishResult`; an unchanged head has `changed=False` instead of raising `GitCmdFailed`.
- First publication uses a random link ID and a create-only head write; a racing first publisher receives a CAS conflict instead of overwriting the winner.
- Version-1 stored links are rejected; the breaking initial-link semantics make new publications version 2 and this branch adds no compatibility path.
- `fetch` returns `FetchResult` and moves nothing except a requested forward-only pin; callers that relied on `{remote}/main` existing afterward use the observed or pinned SHA explicitly.
- An already-current fetch downloads and validates the latest bundle, but does not walk predecessors or import objects.
- A stale out-of-order fetch leaves a newer pin in place, while a divergent pin pauses for integration.
- An exact missing store object remains distinct from authorization, transport, provider, and malformed-response failures.
- A version-2 link has one implicit `main` head instead of declaring every local branch.
- `ssc-files` no longer creates `$GIT_DIR/codsync-bundle-tmp`, and no operation creates `.codsync-bundle-tmp/` in a work tree.
- Invitation acceptance no longer changes the process working directory.
- The `cod-sync` console script is gone.

# Plan

The repository is green after each unit.
The format, store, constructor, publication, fetch, caller-adoption, and obsolete-method changes form one coordinator unit.
Splitting that transition into separately green compatibility stages would either keep two storage/format models alive or make callers merge the old fetch's unvalidated link-declared SHA.
Neither intermediate is worth creating.
The Hub/adapter absence vocabulary and broken CLI deletion also remain in unit 2.
Both could be independently green, but separate prerequisite branches would add sequencing and bookkeeping without reducing the coordinator transition's core risk.
Resource budgeting is different: it adds an orthogonal policy and test matrix, so #189 carries it after this branch.
Where a micro test exposes a current defect, add the test and the fix in the same unit, confirming first that the test fails against the old behavior.

1. **Establish the one-way Git dependency and complete the Repo plumbing.**
   Move `gitCmd` and `GitCmdFailed` into `cod_sync/git.py` and update all four importers, including `ssc_files/files.py:28`.
   Add `Repo.create_bundle(path, rev_args)`, `Repo.verify_bundle(path)`, `Repo.bundle_heads(path) -> {ref: sha}`,
   `Repo.bundle_prerequisites(path) -> set[str]`, `Repo.import_bundle(path)`, `Repo.has_commit(sha)`,
   `Repo.merge_base(left, right) -> Optional[str]`, and the forward-only compare-and-swap pin operation described below.

   `bundle_prerequisites` is the one addition that is not a Git subprocess.
   It parses the header described above, because `list-heads` omits prerequisites and `verify` fails when they are absent,
   which is exactly the state a backward walk is in when it needs to read them.
   Keep it to header bytes: stop at the blank line, never read the pack.
   Parse generic v3 capability records syntactically and let Git decide whether it supports their semantics.

   Forward-publication checks use the existing `Repo.is_ancestor`, passing `refs/heads/main` explicitly as the descendant,
   because its default of `HEAD` is wrong for the `ssc-files` no-work-tree case.

   Add `Repo.advance_ref(name, new_sha) -> RefAdvanceResult`.
   It resolves the current value, compares ancestry, and uses `git update-ref <name> <new> <old>` so a concurrent writer cannot win between inspection and update.
   It rereads after a raced forward advance, returns `stale` without writing when the current value already descends from `new_sha`,
   raises `RefDivergedError` on divergence, and raises `RefAdvanceContendedError` after a small fixed number of consecutive CAS losses.
   A failed update whose reread is unchanged may be a live ref lock rather than a hard failure, so retry it within the same bound.
   After the attempt bound, classify the final observed ref before reporting an error.
   If no disposition applies, preserve any unexplained unchanged-ref failure as `RepoError`; only pure observed CAS losses become `RefAdvanceContendedError`.

   Verify with micro tests in `packages/cod-sync/tests/test_repo.py`: a full bundle with no prerequisites,
   an incremental bundle whose header exposes its exact prerequisite, a v3 bundle with a capability line and malformed capability rejection,
   a reader that stops at the header's blank line without consuming pack bytes,
   a prerequisite whose commit subject contains spaces, a truncated or unrecognized header rejected rather than skipped,
   a prerequisite read from a bundle whose prerequisite commit is absent locally, a missing-prerequisite verification failure,
   an import that creates no ref, a merge base for divergent heads, pin creation, forward advancement, equal no-op, stale retention,
   divergent rejection, an out-of-order CAS race, a transient unchanged-ref failure, final-reread classification,
   bounded repeated contention, and error wrapping for each mutating operation.

2. **Replace the format, stores, coordinator, and callers as one coherent unit.**

   Module and format boundary:
   - Add immutable `Link` and `BundleDescriptor` domain values plus byte-oriented `encode_link`, `decode_link`, and canonical-signing functions in `cod_sync/format.py`.
     Decode with PyYAML's safe loader before enforcing the typed shape.
   - Encode version 2 as the named mapping above, with one optional predecessor, one implicit `main` head, and one bundle.
     Reject unknown top-level keys and preserve unknown keys inside `extensions`.
   - Keep the current model narrow: one predecessor, one `main`, and one bundle.
     Do not introduce a generic parent graph, contribution pointer, or empty version-dispatch hierarchy.
   - Decode only the supported major, preserve unknown extension keys, include them in canonical signing,
     and require each link's version to be greater than or equal to its predecessor's version in chronological order.
   - Bump `COD_SYNC_VERSION` to `2.0.0` for the new initial-link identity semantics.
     Reject version 1 with a typed unsupported-version result rather than adding a compatibility shim.

   API and ownership:
   - Move store interfaces and implementations to `cod_sync/store.py`, apply the `ReadableBundleStore`/`WritableBundleStore` names above,
     and change construction to `CodSync(repo, store)` everywhere.
   - Store methods exchange opaque bytes, file paths, ETags, and typed transport results; only `format.py` sees YAML.
   - Split readable and writable protocols so proxy stores do not advertise publication methods that only raise `NotImplementedError`.
   - Rename the public operations to `publish() -> PublishResult`
     and `fetch(pin_to_ref=None) -> FetchResult`.
   - Remove the unused branch arguments and use `NoPublishedHeadError` for an empty store.
   - Delete `clone_from_remote`; cold-start callers use `Repo.init`, `fetch`, and `Repo.checkout_branch(result.observed_head)`.
   - Remove both cwd-dependent construction sites and the `os.chdir` in invitation acceptance.

   Store contract:
   - Return absence only for an exact missing object.
     Map authentication, authorization, Hub, provider, malformed-response, and network failures to distinct typed errors.
   - Update the Hub cloud-download result vocabulary and its self, peer, explicit-proxy, and bootstrap endpoints so only a provider-confirmed absent object becomes HTTP 404.
     Preserve authentication and authorization status, and surface provider or Hub failure as a structured non-404 response that each Hub-backed store maps to the corresponding typed error.
     Update the provider adapters to report exact absence separately from other provider failures rather than making the endpoint guess from an error string.
   - Write bundles and archived links with create-only semantics.
     A UID collision fails rather than replacing bytes.
   - Write `latest-link.yaml` last, using create-only semantics for the first head and ETag CAS for later heads.
   - Make `LocalFolderStore` perform the comparison and atomic head replacement in one critical section, with the archived link durable before the head becomes visible.
   - Make every writable testing store obey the same CAS and write-once contract rather than accepting `expected_etag` without enforcing it.
   - Convert a network failure whose final head-write outcome is unknowable into `PublicationOutcomeUnknownError` with enough identifiers to reread and diagnose.

   Publication:
   - Bundle into one operation-scoped `TemporaryDirectory`.
   - Empty store: create a full `main` snapshot and a random link with `previous=None`.
     Publish it through the create-only store path.
   - Existing store: strictly decode the latest link, download and inspect its bundle, and require the advertised head and actual prerequisites to match the link.
     Before extending it, read the archived link named by its self-ID and require it to byte-match the latest-link bytes.
     Once its prerequisites are available, require `bundle verify` and the declared ancestry to succeed.
     A successful no-op or extension never rests only on local possession of the declared commit.
   - Unchanged validated head: return `PublishResult(head=local_head, changed=False, link_uid=current_link_uid)` without uploading or notifying.
   - Changed non-empty store: require the validated stored head locally and `is_ancestor(stored_head, refs/heads/main)`, then create `^<stored-head> main`.
   - Inspect the created bundle and require its advertised main head and actual prerequisite set to match the link before uploading.
   - Return `PublishResult(changed=True)` independently of the store transport's internal return value.
   - Raise the typed integration-required error before any upload when the stored head is missing or is not an ancestor.

   Publication callers:
   - `ssc_files.files.push_niche` and `push_registry` return `_cod_push`'s result instead of discarding it.
   - `ssc_files.sync.push_via_hub` raises `NothingToPushError` when the niche result has `changed=False` and treats an unchanged registry as success,
     replacing both `"Refusing to create empty bundle"` string matches.
   - `TeamManager.push_note_to_self` advances its adopted signal count only when `changed=True`, because a no-op sends no Hub notification.
   - `TeamManager.push_team` writes its local publication marker for either changed or unchanged success and reports `"already_published"` for the latter.
   - `ssc_files._cod_push`, both `TeamManager` publication sites, and `provisioning.py:1961` propagate or translate
     `PublicationIntegrationRequiredError`, `CasConflictError`, and `PublicationOutcomeUnknownError` without automatic integration or blind retry.

   Resolution:
   - Read the latest link and validate its structure first.
   - Download and inspect the latest bundle even when its declared head already exists locally.
     If its header matches, its prerequisite is present, `bundle verify` succeeds, and the declared ancestry holds, predecessor traversal and import are unnecessary.
   - Otherwise walk newest to oldest until `previous.head` exists locally or a valid `previous=None` link is reached.
   - Record visited link UIDs; reject cycles, missing predecessors, identity or `previous` errors, version regression,
     inconsistent predecessor heads, and link/bundle metadata mismatches.
   - Download each required bundle once, to a unique path inside one operation-scoped `TemporaryDirectory`.
   - Compare both the bundle heads and actual prerequisite set with the link before importing anything.

   Import and pin:
   - Process oldest to newest; verify each bundle once its prerequisites are present, then `bundle unbundle`.
   - Confirm the declared head now exists and descends from its declared prerequisite.
   - Create no `FETCH_HEAD`, remote, remote-tracking ref, or other implicit ref.
   - After complete validation, advance `pin_to_ref` through `Repo.advance_ref`.
     Return both `observed_head` and the resulting `pinned_head`, because a stale out-of-order fetch may retain a newer pin.

   Caller adoption:
   - Change `manager.refresh_note_to_self`, `ssc_files._cod_pull`, and affected tests to merge `FetchResult.observed_head` via `Repo.merge`,
     or adopt it with `Repo.checkout_branch` on an unborn branch.
   - Callers that fetch to a peer pin record `FetchResult.pinned_head`; they do not overwrite newer parked state with an older observation.
   - `ssc_files._cod_merge_ref` already merges a named ref through explicit Git arguments and remains the local behavioral precedent.
   - Remove the hard-coded `"cloud-codsync-bundle-tmp"` and pointless `add_remote` calls in the same unit that introduces validated fetch results.

   Deletion:
   - Remove `add_remote`, `remove_remote`, `initialize_existing_remote`, `merge_from_remote`, `merge_from_ref`, `_ensure_bundle_remote`, `bundle_tmp`,
     `change_to_root_git_dir`, `get_branches`, `get_branch_head_sha`, the legacy constructor parameters, `ssc_files._bundle_tmp_dir`, and `cod_sync/cli.py` with its script entry.
   - Remove YAML, requests, provider-store implementations, and direct Git execution from `protocol.py`.

   Verify format and stores with micro tests for strict current-major decoding, unknown top-level keys, malformed `previous`, preserved unknown extensions,
   canonical signatures containing unknown extensions, unsupported majors, version regression, exact absence versus each transport failure class,
   write-once archive collisions, create-only initial head, atomic local CAS, and an ambiguous final-write response.
   Verify publication with micro tests for initial publication, two racing first publications, a second genuine incremental publication,
   unchanged-head typed no-op, normal typed success, `NothingToPushError` raised only from an unchanged niche,
   unchanged NoteToSelf publication leaving the adopted signal count alone, missing stored head, unrelated and non-ancestor stored heads,
   missing and mismatched archived copies of the current link, created-bundle metadata, non-empty-chain CAS conflict, and temporary cleanup.
   Verify fetch with a true initial fetch, a genuine incremental fetch, a cold start from a chain of at least three links,
   an already-current fetch that still catches a latest bundle mismatch, an already-satisfied prerequisite,
   a prerequisite reached by walking back across two missing links, one download per bundle, head mismatch,
   actual-prerequisite mismatch including a hidden extra prerequisite, forward/equal/stale/divergent pin cases,
   and every malformed-chain case.
   Run the affected package suites after the targeted micro tests.

3. **Update the documentation that is actually wrong.**
   `Documentation/formats-etc.md` lists and diagrams the deleted methods; correct both the list and the mermaid block.
   `packages/cod-sync/README.md` needs no rewrite of its basic model, but its compaction section should state that compaction preserves forward Git ancestry.
   Update `packages/cod-sync/Documentation/format-spec.md` to version 2 semantics: one predecessor for the supported major,
   random identity for every real link, `previous: null` for the initial link, one implicit `main` head and bundle, create-only/write-once storage,
   and the distinction between strict top-level structure and additive `extensions`.
   Do not speculate about how a later major encodes multiple parents or pending contributions.
   `packages/ssc-files/spec.md:230` describes `push_niche`; update it for the new return value.
   `packages/cod-sync/Documentation/design.md` is a one-line stub; writing a real design document is out of scope for this branch and belongs in Follow-up.

## Validation

The branch is complete when these properties have direct witnesses.

| Property | Witness |
| --- | --- |
| Initial and genuine incremental round trips converge | New micro test: two successive heads to one store, then fetch the latest into an empty repository and check it out |
| First publication cannot silently replace another first publication | Two publishers race from an observed empty store; one create-only head write wins, the other raises `CasConflictError`, and the winning chain remains fetchable |
| Archived objects are write-once | Every writable store rejects replacement of an existing bundle or archived-link UID and preserves the original bytes |
| A new predecessor pointer is resolvable | Remove or alter the archived copy of the current latest link; publication stops before upload instead of extending that head |
| Publication onto a non-empty store advances stored Git history | New tests for descendant success and for missing, unrelated, and non-ancestor stored heads; every rejection leaves `latest-link` and its etag unchanged |
| Publication success and no-op are unambiguous | New tests assert `PublishResult.changed=True` for a new head and `False` for an unchanged head, independent of the store method's return value |
| The no-op still reaches the app as "nothing to push" | New `ssc-files` test: `push_via_hub` raises `NothingToPushError` with no new commits, and succeeds when only the registry is unchanged |
| A NoteToSelf no-op does not invent a notification | New Manager test: unchanged publication leaves the adopted self-signal count unchanged; changed publication advances it once |
| A rejected operation reports enough to act on | Assert the error names the stored head, local head, and link UID for publication, or the link UID, bundle UID, and heads for fetch, plus prerequisite sets whenever a bundle was inspected |
| An already-current fetch still validates the publication | Seed the declared commit locally and doctor the latest bundle; fetch rejects the mismatch rather than returning or moving the pin |
| Store absence is not a transport diagnosis | Provider-adapter and Hub-endpoint micro tests prove that only an exact missing object becomes 404; Hub-backed store tests distinguish that response from authentication, authorization, provider, malformed-response, and network failures |
| Ambiguous final publication is not blindly retried | Fake a final head write that succeeds remotely but loses its response; publication raises `PublicationOutcomeUnknownError`, and rereading reveals the winning link |
| No Cod Sync scratch path appears under a work tree or `$GIT_DIR` | Shared assertion used by publication, fetch, and cold-start success and forced-failure tests |
| No synthetic remote, tracking ref, `FETCH_HEAD`, or temporary tag | Compare remotes and refs against the operation's explicit allowed delta: none for publish, an optional pin for fetch, and `refs/heads/main` for cold-start checkout |
| Importing a head creates no durable ref except an explicit pin | Fetch with and without `pin_to_ref`; compare full `for-each-ref` output |
| A pin never moves backward or sideways | Micro tests cover absent, equal, forward, stale, divergent, and out-of-order concurrent updates; stale retains the newer descendant, divergence moves nothing, and repeated contention stops with a typed error |
| Link/bundle head mismatch fails before any ref moves | Doctored link or bundle fixture; destination and pin refs unchanged |
| Link/bundle prerequisite mismatch fails before import or ref movement | Initial bundle with a prerequisite, incremental bundle with the wrong prerequisite, and incremental bundle with a hidden extra prerequisite |
| Bundle headers are read correctly | `test_repo.py` cases for v2, v3 with a capability line, a subject containing spaces, an absent prerequisite commit, and a malformed header |
| The supported link format is strict without freezing extensions | Version-2 fixtures reject missing or unknown top-level keys and malformed `previous`, preserve unknown `extensions` through round-trip and signing, reject version regression, and classify unsupported majors as upgrade/intervention |
| A missing prerequisite is filled, or fails without moving a ref | Chain of at least three links, fetched from a repository missing the last two; plus a broken-chain variant |
| Each bundle is downloaded exactly once | Counting store wrapper over a cold start from a chain of at least three links; guards against the current quadratic behavior returning |
| Malformed chains terminate safely | Fixtures for a cycle, missing predecessor, wrong archived self-ID, malformed `previous`, inconsistent predecessor head, and version regression |
| Temporary bundle files are removed on success and failure | Capture the operation temp root, force failures in resolution, verification, import, and upload, then assert cleanup |
| Hub-backed and local-folder stores keep their gateway and local-only rules | Existing `test_smallsea_remote.py`, `test_hub_sync.py`, and `test_cas_behavior.py`, updated for the byte-oriented contract and stronger CAS semantics |
| Signed-link bytes and verification still round-trip | Existing `test_signed_bundles.py`, updated for the main-only declaration and new API |

Five load-bearing fixtures do not exist in any form today: a chain longer than two links,
a fetch that walks backward across more than one missing link, a bundle whose actual prerequisites disagree with its link,
a store wrapper that counts reads, and two writers that race on an observed empty store.
Building them is part of units 1 and 2, not a bonus.

Repo integrity, beyond the behavior table:
- `protocol.py` no longer executes Git commands, parses YAML, or implements stores; the module dependency graph is acyclic.
- `store.py` moves opaque bytes and reports transport truthfully; `format.py` alone owns link encoding and decoding.
- `ssc-files` names no Cod Sync Git remote and manages no Cod Sync scratch path.
- No Cod Sync entry point depends on the process working directory.
- Every production store continues to perform network I/O through the Hub; direct provider stores remain testing-only.
- Version 2 is the only supported link major and no version-1 compatibility path survives.
- Targeted suites: `uv run pytest packages/cod-sync packages/ssc-files packages/small-sea-manager packages/small-sea-hub`.
- Full suite: `uv run pytest` at the repository root.
- `git diff --check` passes, and `rg` finds no surviving deleted symbol, old storage-side type name, or `"Refusing to create empty bundle"` match.

# Follow-up

- Implement #189 after this branch to bound unattended Cod Sync work with finite default budgets, progress diagnostics, and explicit larger-budget continuation.
- Edit #187: remove the "fall back to a full main bundle when the prerequisite is absent" scope bullet and record the expanded format, store, and first-publication boundaries implemented here.
- Open an issue for verifying link signatures on the fetch path, or wherever the right layer turns out to be.
  Signing exists and verification exists; nothing connects them outside tests.
- Open an issue for caller-level publication integration policy above Cod Sync:
  who fetches, who merges, who needs a work tree, and how a retry crosses the CAS window.
  It should also cover the opposite direction — updates low-stakes enough that dropping them on conflict beats stopping for a person.
- Open an issue for collecting orphan bundles left in a store by conflicted or rejected publications.
  Store-side upload ordering, a sweep against the reachable chain, and compaction are all plausible tactics, and more than one may be wanted.
  Whatever it does, it must not delete an unreferenced bundle promptly enough to foreclose the pending-contribution issue below.
- Open an issue for caching proof that an exact immutable link and bundle pair has already validated.
  Until that exists, an already-current fetch and an unchanged publication download and validate the latest bundle rather than confusing local object possession with publication validity.
  The cache key must identify content, not merely trust a random UID.
- Open an issue for streaming bundle transport and a hard pre-download byte ceiling.
  #189 deliberately provides unattended-work policy rather than a hard resource sandbox; the current Hub JSON/base64 path may materialize an object before its size is known locally.
- Open an issue for pending contribution pointers: a device that loses the head-link race advertises its unmerged bundle
  so that a device in a better position can absorb it, instead of the losing device being the only one able to get unstuck.
  The motivating case is a heterogeneous fleet — a phone can publish and lose but may never have the work tree or the attention to merge — rather than retry latency.
  Constraints worth carrying into the issue:
  - Pointers live inside `latest-link.yaml`, so adding and clearing one are ordinary CAS writes on a file that already has a serialization point.
    Storing them anywhere else creates a distributed collection problem with nothing to serialize against.
  - A losing bundle needs no regeneration.
    It was built against a prerequisite that is a shared ancestor of the winning head, so any device current with the chain can already import it.
  - Clearing a pointer is `is_ancestor(pending_head, new_head)`, using the forward-only ancestry primitive introduced here.
    Two devices absorbing the same contribution is a benign no-op and does not need preventing.
  - A pointer is an offer, not a claim.
    It asserts that commits are not yet in the head; it does not assert that anyone intends to finish.
    Any promise of intent drags in leases, expiry, and liveness detection.
  - Pointer growth needs its own cap and eviction rule.
    The sync budget planned in #189 bounds unattended work but does not decide which durable offers may be discarded.
  - It depends on link signature verification.
    A pointer instructs other devices to download and merge a bundle, so shipping it while nothing verifies authorship is the wrong order.
  - Weigh the alternative before designing the mechanism: per-device chains that readers merge, which is what `PeerSmallSeaStore`,
    `team_device`, and `refs/peers/{teammate_id}/{branch}` (files.py:622) already do between teammates, and which has no head-link race to lose.
    The cost is that every reader does N link reads and N merges, and merging wants a work tree that `ssc-files` fetch paths deliberately do not require,
    where a single head amortizes that cost onto the one writer.
  - Prior art: Radicle (Git-native, no server, per-peer namespaces, signed ref sets), `git request-pull` (the same offer with no intent attached),
    Gerrit `refs/changes/*` (same structure with a server, instructive on abandonment and cleanup),
    and Autobase/Hypercore or Mercurial's multiple heads for the per-writer-log alternative.
- Open an issue for writing `packages/cod-sync/Documentation/design.md`, which is currently the one-line stub `some design`.
