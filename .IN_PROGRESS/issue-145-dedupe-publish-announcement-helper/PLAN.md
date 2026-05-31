# Branch Plan: Deduplicate `_publish_storage_announcement_for_session`

**Branch:** `issue-145-dedupe-publish-announcement-helper`
**Base:** `main` (after #144 lands)
**Primary issue:** #145 "Deduplicate _publish_storage_announcement_for_session test helper"
**Predecessor:** #137 introduced the duplication; #144 added one more call site in `test_cloud_api.py`.
**Kind:** Test-only refactor.
Zero production-code changes.
One new test-support module, seven test files updated to import it.

## Purpose

Slice B (#137) and the rotation-hardening branch (#144) left seven
near-identical copies of `_publish_storage_announcement_for_session`
across three packages.
Each copy looks up the session, fetches the allocation, looks up
`(team_id, self_member_id)`, and calls
`Provisioning.publish_member_berth_storage_announcement`.

The next behavior change to storage-announcement publication (signer
metadata, return shape, NoteToSelf handling, etc.) currently requires
parallel edits in seven files, with the obvious risk that the copies
drift.
This branch consolidates them into one shared helper so future
storage-announcement work changes one symbol, not seven.

## Variants the consolidated helper must absorb

Inventoried across the seven files:

| File | Signature | NoteToSelf | Returns | Module alias | `team_id` assert |
|------|-----------|------------|---------|--------------|------------------|
| `small-sea-hub/tests/test_cloud_api.py` | `(playground_dir, backend, session_hex)` | no early-return | yes | `Provisioning` | yes |
| `small-sea-hub/tests/test_notifications.py` | `(backend, session_hex)` | skip | no | `Provisioning` | no |
| `small-sea-manager/tests/test_hub_invitation_flow.py` | `(backend, session_hex)` | skip | no | `Provisioning` + direct imports | no |
| `small-sea-manager/tests/test_invitation.py` | `(backend, session_hex)` | skip | no | `provisioning` | no |
| `small-sea-manager/tests/test_signed_bundles.py` | `(backend, session_hex)` | skip | no | `Provisioning` + direct imports | no |
| `shared-file-vault/tests/test_hub_sync.py` | `(backend, session_hex)` | skip | no | `Provisioning` | no |
| `shared-file-vault/tests/test_web_sync.py` | `(backend, session_hex)` | skip | no | `Provisioning` | no |

The differences are minor and reconcilable:

- `playground_dir` in `test_cloud_api.py` is the same value the
  backend was constructed with (`SmallSeaBackend(root_dir=playground_dir)` in
  the `test_env` fixture in `packages/small-sea-hub/tests/test_cloud_api.py`),
  so `backend.root_dir` is interchangeable.
- The NoteToSelf skip is harmless to add to `test_cloud_api.py`'s
  callers — none of them pass a NoteToSelf session today.
- The `team_id == ss_session.team_id` assertion in `test_cloud_api.py`
  is a useful defensive check and is cheap to keep.
- Returning the result is non-breaking for callers that ignore it.
  One call site in `test_cloud_api.py`
  (`test_team_cloud_file_requires_storage_announcement`) reads
  `published["wrote"]`, so the return value must be preserved.

## Design choices

### Where the helper lives

New file at the **repo root**: `test_support.py`.

Imported as `from test_support import publish_storage_announcement_for_session`.

Reasoning:

1. **Keep test scaffolding out of every runtime distribution.**
   Putting the helper under any `packages/*/<pkg>/` tree means it
   ships in that package's wheel/sdist, making it possible for
   production code (in this repo or downstream) to accidentally
   import test-only scaffolding.
   A repo-root location ships with nothing.
2. **Already importable** without new sys.path setup.
   The root `pyproject.toml` has `pythonpath = ["."]`, so a module
   at the repo root resolves from every package's tests via
   `from test_support import …`.
3. **Cross-cuts three packages** (hub, manager, vault).
   No single package owns the helper, so placing it inside one of
   them is arbitrary and creates a directional dependency the layout
   doesn't otherwise require.
4. **Lighter than a new workspace package.**
   A standalone `packages/test-support/` with its own `pyproject.toml`
   would also keep the helper out of runtime wheels, but is more
   ceremony than a single function warrants.
   If shared test scaffolding grows (see Follow-up), promote
   `test_support.py` to a `test_support/` directory or a workspace
   package then; not yet.

The helper does NOT type-annotate the `backend` parameter as
`SmallSeaBackend` — that would require importing from
`small_sea_hub`, which we don't want a repo-root test helper to
depend on.
Instead it duck-types on the two attributes it needs (`root_dir`
and `_lookup_session`).
The docstring documents the contract.

### Helper signature

```python
def publish_storage_announcement_for_session(backend, session_hex) -> dict | None:
    """Publish this session's own-storage announcement.

    For NoteToSelf sessions this is a no-op (returns None) — that team
    has no shared storage to announce.

    `backend` must expose `.root_dir` and `._lookup_session(session_hex)`
    returning a `SmallSeaSession`.  Duck-typed so this module can stay
    free of hub/vault imports.
    """
```

Name has no leading underscore: it is an exported test-support symbol,
not a module-private helper.

### Return shape

Returns whatever `publish_member_berth_storage_announcement` returns
(a `dict` with at least `"wrote"` per the existing
`test_team_cloud_file_requires_storage_announcement` caller),
or `None` for NoteToSelf.
Callers that ignore the return value continue to work.

### Ordering versus #144

#144 (`issue-144-allowance-rotation-hardening`) added one more call
site to `test_cloud_api.py`:
`test_team_cloud_file_bootstrap_allowance_rejects_rotated_signer`.
On `main` today the file has three call sites; once #144 lands the
file has four.
This plan assumes #144 has merged before #145 begins.
If #145 is started before #144 merges, rebase after #144 lands and
update the new call site to the shared helper too.

## Branch Contract

When this branch is done, all of the following are true:

1. A new repo-root module `test_support.py` exists, exporting
   `publish_storage_announcement_for_session`.
2. The seven listed test files no longer define their own
   `_publish_storage_announcement_for_session`.
   Each imports `publish_storage_announcement_for_session` from
   `test_support` and uses it at every former call site.
3. The shared helper:
   a. duck-types `backend` (no `SmallSeaBackend` import);
   b. early-returns `None` for `team_name == "NoteToSelf"`;
   c. asserts `team_id == ss_session.team_id` after `_team_row`;
   d. returns the
   `publish_member_berth_storage_announcement` result for non-NoteToSelf
   sessions.
4. `grep -rn "def _publish_storage_announcement_for_session\b"
   packages/` returns no matches.
5. `git diff main..HEAD -- packages/` touches only files under
   `packages/*/tests/`.
   No file under any `packages/*/<pkg>/` runtime tree is modified.
6. `uv run pytest packages/small-sea-hub/tests
   packages/small-sea-manager/tests packages/shared-file-vault/tests`
   is green (modulo pre-existing Docker-daemon flake in
   `test_notification_roundtrip`'s setup, which is environmental).

## Scope

### In scope

- Adding repo-root `test_support.py` with one function.
- Updating all seven test files to import and call the shared helper.
- Removing each file's local `_publish_storage_announcement_for_session`
  definition.

### Out of scope

- Generalizing other duplicated test helpers (e.g. `_push_via_hub`,
  `_make_bucket_public`).
  If a smell appears during the import edits, capture it in
  `FOLLOW-UP.md` rather than expanding scope.
- Adding type annotations to the helper beyond `-> dict | None`.
  The `backend` parameter stays untyped to avoid pulling
  `small_sea_hub` into a repo-root test helper.
- Moving the helper into production `provisioning` as a public API.
  It's specifically a test-only orchestration over real provisioning
  functions.
- Promoting `test_support.py` to a `test_support/` package or a
  workspace package.
  Deferred until more shared helpers consolidate (see Follow-up).

## Implementation Pass

One pass, executed in this order to keep each step independently
verifiable:

1. Add repo-root `test_support.py` with
   `publish_storage_announcement_for_session`.
   Sanity-check that `from test_support import
   publish_storage_announcement_for_session` resolves from a
   per-package test by running one existing test file before
   touching its call sites.
2. Update `packages/small-sea-hub/tests/test_cloud_api.py`:
   - Replace `_publish_storage_announcement_for_session(playground_dir,
     backend, session_hex)` with
     `publish_storage_announcement_for_session(backend, session_hex)`
     at all call sites.
     `backend.root_dir == playground_dir` at every site so this is
     value-preserving.
   - Delete the local `_publish_storage_announcement_for_session`
     definition.
   - Add the import.
   - Run `uv run pytest packages/small-sea-hub/tests/test_cloud_api.py`
     and confirm green before proceeding.
3. Update each of the remaining six files in turn, running each
   file's tests before moving on.
4. After all seven are migrated, run the full Hub/Manager/Vault
   pytest suite.

The per-file pause is deliberate: if a variant turns out to have a
subtlety the inventory missed, the failure is localized to that file
rather than splattered across all seven.

## Concrete File Changes

**Added:**

- `test_support.py` (repo root) — new module, one function.
  Resolves via the existing `pythonpath = ["."]` in the root
  `pyproject.toml`; no new sys.path or workspace configuration.

**Modified (each: one import added, one local helper deleted,
call-site argument list updated):**

- `packages/small-sea-hub/tests/test_cloud_api.py`
- `packages/small-sea-hub/tests/test_notifications.py`
- `packages/small-sea-manager/tests/test_hub_invitation_flow.py`
- `packages/small-sea-manager/tests/test_invitation.py`
- `packages/small-sea-manager/tests/test_signed_bundles.py`
- `packages/shared-file-vault/tests/test_hub_sync.py`
- `packages/shared-file-vault/tests/test_web_sync.py`

No `__init__.py` change needed — Python picks up the new module on
import.

## Validation

A skeptical reviewer should be able to confirm each of these without
running the code:

1. **The helper truly replaces every copy.**
   `grep -rn "def _publish_storage_announcement_for_session\b"
   packages/` is empty.
2. **No runtime-package code changed.**
   `git diff main..HEAD -- packages/` touches only files under
   `packages/*/tests/`.
   The only other change in the tree is the new repo-root
   `test_support.py`.
3. **`test_support.py` imports only `small_sea_manager`.**
   `grep -E "^from |^import |small_sea_hub|shared_file_vault" test_support.py`
   shows the helper's import lines and no `small_sea_hub` or
   `shared_file_vault` dependency.
   The shared helper does not pull cross-package runtime deps.
4. **Behavior preserved for the one caller that reads the return
   value.**
   `test_team_cloud_file_requires_storage_announcement` still asserts
   `published["wrote"] is True`.
5. **Full suite green:**
   `uv run pytest packages/small-sea-hub/tests
   packages/small-sea-manager/tests packages/shared-file-vault/tests`.
   The pre-existing `test_notification_roundtrip` Docker-daemon error
   is unrelated and ignored.

## Non-Negotiable Invariants

1. Test-only refactor.
   The repo-root `test_support.py` is the only non-test file added;
   it lives outside every `packages/*/<pkg>/` runtime tree and so
   ships in zero runtime distributions.
   No file under any `packages/*/<pkg>/` is modified.
2. No new inter-package dependencies.
   `test_support.py` must not import from `small_sea_hub` or
   `shared_file_vault`; only `small_sea_manager.provisioning` is
   needed.
3. NoteToSelf early-return semantics preserved: callers that pass a
   NoteToSelf session must observe a no-op return (`None`), not an
   assertion or exception.

## Follow-up

If migration surfaces other duplicated cross-package test helpers
(e.g. `_push_via_hub`, `_make_bucket_public`, `_push_team_repo_via_hub`),
record them in `.IN_PROGRESS/issue-145-dedupe-publish-announcement-helper/FOLLOW-UP.md`
rather than expanding scope here.
