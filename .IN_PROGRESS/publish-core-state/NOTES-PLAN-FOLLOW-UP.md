# Notes

Branch scope: GitHub issue #184, "Make `TeamManager.push_team` publish completed Core state".
This work was split from the `sync-orchestration` planning branch so it can land independently.
Fetching or integrating another Core chain, notification-driven sync, and combined incoming/outgoing UX remain outside this branch.

Current state of the code:

- `push_team` (`packages/small-sea-manager/small_sea_manager/manager.py`) opens a session, calls `CodSync.push_to_remote(["main"])`, then writes the current HEAD to `.ss_last_push`.
  It never stages or commits `Sync/core.db`.
  A completed Manager mutation can therefore remain outside the published chain without any warning.
  Most `provisioning.py` helpers hide this by staging and committing `core.db` themselves, so the gap is only reachable through the ones that do not:
  `publish_teammate_berth_storage_announcement`, `announce_teammate_transport`, and `set_team_admission_policy`.
- `push_team` and `get_team_sync_status` have no test coverage today.
  `push_team`'s only caller is the web route at `web.py:422`.
  `get_team_sync_status` also supplies the team detail, team list, and sync-badge routes at `web.py:99`, `web.py:178`, and `web.py:415`.
  Standing up the first Hub-backed publication fixture is most of this branch's work.
- `push_note_to_self` already has the required commit-before-push ordering.
  It is an ordering precedent only: it still calls the whole-index `Repo.commit`, and changing its publication behavior is outside #184.
- `push_team` does not pass the Manager's injected HTTP client to `SmallSeaRemote`.
  A Manager micro test using an in-process Hub would therefore fall through to the real Hub socket instead of exercising the injected local route.
- The CAS handler says to pull from peers, but a CAS conflict concerns the participant's own published chain.
  This branch will report that fact accurately but will not design same-participant recovery.
  CAS catches only the narrow etag race.
  The adjacent hazard is quieter: when the remote head is not present locally, `push_to_remote` falls back to a full `initial-snapshot` bundle (`protocol.py:210-218`) and replaces the link, dropping remote-only commits without raising anything.
  Step 6 rewords an error message; it does not make divergence safe.
- `get_team_sync_status` compares HEAD with `.ss_last_push` and ignores an uncommitted `core.db`, so completed local work can report `synced`.
- A repeated unchanged Cod Sync push tries to create an empty incremental bundle, which Git rejects.
  The Manager can avoid that failure when `.ss_last_push` records that this installation already published HEAD.
  A markerless clone whose remote already points at the same HEAD remains exposed to the Cod Sync defect and belongs in the follow-up issue.
- Production publication is unsigned.
  `push_to_remote`'s `signing_key` and `teammate_id` parameters are exercised only in tests; no production caller passes them.
  That is outside #184, but "publish completed Core state" should not be read as "publish signed Core state".

## Scope decisions

- Add a path-scoped `Repo` commit operation without changing the existing `Repo.commit` contract.
  Add a read-only `work_tree_paths_differ_from_head(paths)` operation and have the scoped commit delegate its no-op decision to it.
  Manager sync status must use that same operation rather than reproducing the Git check.
  Make it a separate method rather than an optional `paths=` argument, because the semantics genuinely differ:
  `commit` commits the index, while `git commit -- <paths>` commits work-tree content at those paths.
  Say so in the docstring, along with the precondition that the named paths are already tracked —
  for an untracked path the no-op check reports nothing to do while the commit itself errors.
  Scope the no-op check to the work tree (`git diff --quiet HEAD -- <paths>`), not the index.
  Copying `Repo.commit`'s `diff --cached` idiom would guard the wrong thing:
  with `core.db` modified but unstaged, `--cached` reports a no-op while the scoped commit would still commit it.
  An unrelated staged entry must not make an unchanged `core.db` look committable.
- Commit only `core.db` during team publication.
  The pathspec commit reads the work tree, so no separate staging step is needed.
  `.gitattributes` is installed and committed during team provisioning and is not publication-time state.
- Accept that publication-time commit changes commit granularity for the helpers that do not commit themselves.
  Several uncommitted mutations followed by one push produce one commit with a generic message rather than one commit per operation.
  For those helpers that is strictly better than today's silent omission.
  Making publication the only git write point for the team repo is follow-up work, not this branch.
- Decide `already_published` before opening the Hub session.
  Preparing `core.db` is purely local, so a no-op publication should not open a session or prompt for a PIN.
  `already_published` means that this installation's `.ss_last_push` marker confirms HEAD; it is not a fresh observation of remote state.
  When the marker is absent, `push_team` attempts publication even if the remote may already point at HEAD.
- Leave `push_note_to_self` unchanged on this branch.
  Its analogous whole-index risk belongs with the Manager multi-device NoteToSelf work in #48 after that issue is reconciled with the now-implemented push/refresh flow.
- Treat Manager mutations and publication as serialized on one device.
  Capture the intended HEAD before the remote operation, but do not add cross-process locking or a separate index file here.
  Same-participant concurrent-history and recovery semantics belong in #135.
- Keep team sync status outgoing-only.
  Incoming hinted, fetched, parked, and conflicted state remains part of #35, #185, and the later `sync-orchestration` branch.
- Return a small publication outcome from `push_team`: `published` or `already_published`.
  Do not introduce a general sync result framework in this branch.

# Plan

1. Add an additive path-scoped commit operation to `cod_sync.repo.Repo` while preserving the existing whole-index `commit` behavior.
   Add `work_tree_paths_differ_from_head(paths) -> bool` as the shared read-only check, implemented with `git diff --quiet HEAD -- <paths>`, and make the scoped commit call it.
   The operation returns `None` when the named paths match HEAD even if unrelated paths are staged.
   → verify: Cod Sync micro tests cover a changed `core.db` with an unrelated staged file, and an unchanged `core.db` with an unrelated staged file.
   In both cases the unrelated index entry remains staged and byte-identical; only the changed `core.db` case creates a commit.
   A third case covers `core.db` modified but not staged, which must produce a commit rather than a reported no-op.

2. Make `TeamManager.push_team` path-commit `core.db` before publication.
   Use the message "Update team Core", the generic message the commit-granularity scope decision refers to, matching the shape of `push_note_to_self`'s "Update NoteToSelf".
   Pass `self.client._http_client` into `SmallSeaRemote` so production still uses the configured Hub URL while micro tests use the injected in-process Hub.
   → verify: call `TeamManager.publish_teammate_berth_storage_announcement`, which writes a signed team-Core row and commits nothing, then call `push_team`,
   then clone or fetch what the Hub-backed remote actually received and find that row in the published `core.db`.
   The witness must fail against the current `push_team`.
   A helper that already commits its own `core.db` would pass without the fix and is not a valid witness.
   Verify an unrelated unstaged file remains unstaged and absent from both the publication commit and the remote clone.

3. Make unchanged publication an explicit Manager no-op.
   After preparing `core.db`, compare the resulting HEAD with `.ss_last_push`.
   If they match, skip the Hub push and return `already_published`; otherwise perform the remote push and return `published` on success.
   This is deliberately a local-observation check: a missing marker does not prove the remote lacks HEAD, so it still attempts publication.
   Leave markerless clones whose remote already points at HEAD to the Cod Sync empty-bundle follow-up rather than reading remote history into this Manager branch.
   Reach that decision before opening the Hub session.
   That reorders the publication commit ahead of session opening, so a denied PIN or a failed `ensure_cloud_ready` now leaves a fresh unpublished local commit where today's `push_team` would have failed before committing anything.
   That is the intended outcome — the work is preserved — but it is new behavior, so step 5 covers it.
   After a previous successful publication status reports `needs_push`; after a failed first attempt it remains `never_pushed`, because no successful-publication marker exists.
   Do not use only the scoped commit's return value for this decision, because an earlier failed push can leave HEAD unpublished even though a retry creates no new commit.
   → verify: two pushes without an intervening Core mutation create no empty commit or bundle, the second call returns `already_published`, and the second call opens no Hub session.

4. Surface the publication outcome in the web route.
   `web.py:419` currently reports "Pushed to cloud." regardless of what `push_team` did.
   Map `published` and `already_published` to distinct notices.
   Route-level testing has one precedent: `_manager_web` (`test_app_sightings_ui.py:102`) builds the Manager app with an injected Hub client and a pre-set session.
   Adapt that shape rather than inventing new scaffolding, but the session differs:
   `_manager_web` sets a NoteToSelf passthrough session, while `push_team` resolves an encrypted team session through `_get_or_open_session` (`manager.py:119-131`).
   `test_teammate_transport.py:151` is not a second precedent.
   It builds a plain `create_app` and `TestClient` over `localfolder` storage, with no injected Hub client and no session.
   → verify: a route-level micro test renders "Already published." for the second of two pushes.

5. Make `.ss_last_push` identify the exact successful publication.
   Capture the intended HEAD after the publication commit and before calling Cod Sync.
   Write that captured SHA only after the remote operation succeeds.
   → verify: after a successful push, the marker equals the remote link's published `main` SHA.
   After an injected failure following a new Core commit, the marker remains byte-identical and HEAD retains the local commit.
   Status is `needs_push` after a previous successful publication and `never_pushed` after a failed first attempt.
   Cover both failure points that step 3's ordering creates: a Cod Sync push failure, and a session-open failure raised after the publication commit.

6. Replace the misleading CAS advice with a minimal accurate Manager failure contract.
   Raise an accurately worded `RuntimeError` from the original `CasConflictError`, stating that the participant's published Core chain changed and that the local commit remains unpublished.
   Do not add recovery or a new exception taxonomy here.
   → verify: a CAS micro test asserts the chained cause, unchanged marker, retained local commit, and `needs_push` status.

7. Make `get_team_sync_status` inspect only Manager-owned outgoing state.
   Keep the existing no-HEAD branch first: a repo with no commits returns `never_pushed` without consulting the marker or the work tree.
   `git diff --quiet HEAD -- <paths>` is fatal where HEAD does not resolve, so that ordering is load-bearing.
   A team repo always carries the `create_team` commit (`provisioning.py:4714`), which also satisfies step 1's tracked-path precondition, so this branch is defensive rather than reachable.
   Preserve `never_pushed` when no marker exists.
   Return `needs_push` when HEAD differs from the marker or when `core.db` differs from HEAD.
   Return `synced` when HEAD matches the marker and `core.db` is clean, even if unrelated files under `Sync/` are staged, modified, or untracked.
   The "`core.db` differs from HEAD" test must call the read-only Repo operation from step 1, which the scoped commit also uses, not a second independent check.
   If the two drift, the UI reports `needs_push` forever or reports `synced` on unpublished work, and neither step's own tests would catch it.
   → verify: focused micro tests cover the clean, dirty-Core, unrelated-dirty, unpushed-commit, and never-pushed cases.

8. Update the narrow durable contract and run layered local validation.
   Adjust the Manager spec's outgoing-sync description (`spec.md:950`) so it includes completed but uncommitted Manager-owned Core state and makes clear that incoming state is separate.
   Record why `push_team` and `push_note_to_self` end this branch with different publication idioms — path-scoped in `push_team`, whole-index at `manager.py:206` — either as a comment at both sites or in the design record.
   Run the focused Cod Sync and Manager micro tests, then the relevant existing Manager/Hub suites.
   → verify: no test performs provider access outside the Hub, no unrelated caller changes behavior through `Repo.commit`, and the final diff contains only #184 work plus its plan records.

## Validation shape

- Generic path-scoped commit behavior belongs in `packages/cod-sync/tests/test_repo.py`.
  Manager publication, status, error, and web-notice claims belong in focused micro tests under `packages/small-sea-manager/tests/`.
- The end-to-end publication witness must inspect state obtained from the Hub-backed remote, not only local HEAD or mock call counts.
  This prevents a commit-after-push implementation from passing.
- That witness is a micro integration test, the term `test_cloud_roundtrip.py` already uses for this shape.
  It needs MinIO: the Hub has no `localfolder` storage adapter, so no Hub-backed push test can avoid it.
- Build the Hub test around an injected `TestClient` and a current per-berth cloud allocation.
  `test_note_to_self_refresh.py` and `test_signed_bundles.py` are the transport precedents; `test_cloud_roundtrip.py` shows current berth-allocation setup.
- Do not add another hardcoded MinIO port.
  Extend `packages/small-sea-manager/tests/conftest.py` so `minio_server_gen` accepts `port=None` and allocates two ephemeral ports, one for the API and one for the console, instead of deriving the console port as `port + 1`.
  Change only the Manager copy of that fixture on this branch.
- Use MinIO only, reached through Hub endpoints by the Manager and Cod Sync clients.

# Follow-up

- Open a new issue for making publication-time commit the only git write point for the team repo.
  Manager spreads that responsibility today: sixteen inline `stage`/`commit` pairs in `provisioning.py` (thirteen on team repos, three on NoteToSelf), while `publish_teammate_berth_storage_announcement`, `announce_teammate_transport`, and `set_team_admission_policy` mutate the team DB and commit nothing.
  Step 2 installs the choke point, which demotes the sixteen inline commits from load-bearing to redundant; deleting them is the follow-up, not this branch.
  The cleanup is cheap on the test side — only `test_invitation.py:441` and `test_create_team.py:320` assert on those commit messages.
  No existing issue is a natural home. #163 and #174 scope themselves to core event semantics and explicitly exclude Manager write policy, and #3 is being decomposed rather than grown.
  Two questions must settle first: whether a git commit still carries provenance once the #173/#174 event envelope lands, and #185's clean-work-tree preflight, which assumes a dirty `core.db` is exceptional rather than the normal resting state between mutation and publication.
- Open an issue for Cod Sync bundle temp files landing inside Manager work trees.
  `CodSync.bundle_tmp` defaults to `{repo_dir}/.codsync-bundle-tmp` when no `bundle_tmp_dir` is passed (`protocol.py:483-484`), and Manager passes none in `push_team`, `push_note_to_self`, or `refresh_note_to_self`.
  Every Sync work tree therefore carries a permanently untracked `.codsync-bundle-tmp/` after its first transport operation, so no Manager work tree is ever clean again.
  This is a second, independent reason #185's clean-work-tree preflight cannot hold as written: it fails even with `core.db` fully published.
  `ssc-files` already solved this by passing a git-dir path, with a docstring naming the reason (`ssc_files/files.py:314`); adopt that in Manager, or make the git-dir location the Cod Sync default so no caller has to know.
  This branch's scoped commit is unaffected either way, since an untracked path is never committed by a pathspec that does not name it.
- Open an issue for the Cod Sync empty-bundle failure.
  `push_to_remote` builds a `tag..main` bundle spec (`protocol.py:220`) and Git refuses to create an empty bundle, so an unchanged re-push fails for every caller.
  Step 3 guards Manager team repetitions whose successful publication was recorded by this installation, which is the right scope for this branch.
  It leaves the defect in place for other callers and for a markerless team clone whose remote already points at its HEAD.
  It is reachable today, not latent: `push_note_to_self` twice with no intervening NoteToSelf mutation hits it, because the only state that call advances afterwards is the device-local adopted count (`small_sea_note_to_self/db.py:163-182`), which never touches the shared `core.db`.
  File it as a live defect.
- Open a test-infrastructure housekeeping issue.
  Roughly thirty hardcoded MinIO ports sit across four duplicated `minio_server_gen` fixtures, and each start pays a fixed `time.sleep(2)` where the sibling `hub_server_gen` already polls readiness through `_wait_for_hub_ready`.
  Propagate this branch's two-ephemeral-port change to the other three copies and replace the sleep with a readiness poll.
  Fold the `ntfy_server` fixture (`packages/small-sea-hub/tests/conftest.py:91`) into the same issue.
  It hardcodes port 9090 for a PID-named container, so a leftover container from an earlier run holds the port and `docker run` fails with "port is already allocated".
  That failure surfaces as a fixture error and reads as a broken test rather than as stale local state.
  Its readiness handling already polls, so only the port allocation and the debris need attention:
  when the `docker run` itself fails the container is left in `Created` state, because `check=True` raises before either cleanup path runs.
- Leave remote-history integration and recovery implementation out of this branch.
  #135 is the design home for choosing rebase or merge based on publication or observation status, including same-participant concurrency.
  It may determine the later integration flow, but it does not currently own reliable publication-observation or concurrent-writer implementation.
  Open separate implementation issues for those concerns after #135 settles the design rather than treating its current scope as an implementation plan.
- Attach the analogous NoteToSelf whole-index publication concern to #48 when that issue's stale description is reconciled with the current push/refresh implementation.
- Leave combined incoming/outgoing sync state and notification-driven presentation to #3, #35, #185, and the `sync-orchestration` branch.
- Treat the existing fixture repairs in #153 as independent unless they block the focused #184 witness.
- Confirm that #184 landing satisfies the prerequisite expected by `sync-orchestration`; do not pull its fetch, integrate, or notification work back into this branch.
