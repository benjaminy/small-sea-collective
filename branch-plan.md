# Branch Plan: Three-mode niche residency (issue #81)

## Goal

Make niche residency an explicit concept in Shared File Vault so the code,
CLI, and spec all agree on the three local states a niche can be in:

- known only from the registry
- cached locally without a user checkout
- checked out into a user-visible directory

This branch should make that state inspectable, use it to produce better
actionable errors, and do so without changing storage layout or widening the
branch into transit cleanup or git abstraction work.

## Background

After #80 the user-facing language is effectively binary: a niche is either
"not present" or "has a checkout". But the vault already has a meaningful
middle state: the niche git dir exists locally, possibly with fetched peer
refs and local history, while no checkout is attached.

That middle state matters operationally:

- `fetch_niche()` can create local niche state without a checkout.
- `create_niche()` creates the git dir before any checkout exists.
- `merge_niche()` and `pull_niche()` fail differently depending on whether the
  niche is absent locally or merely missing a checkout.

Issue #82 (transit removal) and #78 (`gitCmd` cleanup) are nearby, but this
branch deliberately avoids both. The aim here is to name and expose the
current state model, not redesign the machinery underneath it.

## Three residency modes

| Mode | Condition |
|------|-----------|
| `REMOTE_ONLY` | No niche git dir exists on this device. The niche may still be known via the shared registry. |
| `CACHED` | The niche git dir exists locally, but no checkout is registered. The niche may have commits, fetched refs, or both. |
| `CHECKED_OUT` | The niche git dir exists locally and a checkout is registered in `checkouts.db`. |

Residency is about local materialization, not sync freshness. A niche can be
`CACHED` or `CHECKED_OUT` and still be behind a teammate.

Stale checkout registrations remain a separate concern. For this branch,
`CHECKED_OUT` means "a checkout row is registered". If the registered path is
missing on disk, that is still reported through `StaleCheckoutError`, not by
introducing a fourth residency mode.

## State transitions this branch should document

- `REMOTE_ONLY -> CACHED`
  - `create_niche()`
  - `fetch_niche()`
  - `pull_niche()` when it lazily creates the git dir before rejecting for
    missing checkout

- `CACHED -> CHECKED_OUT`
  - `add_checkout()`

- `CHECKED_OUT -> CACHED`
  - `remove_checkout()`

- `CHECKED_OUT -> CHECKED_OUT`
  - `publish()`, `push_niche()`, `merge_niche()`, `pull_niche()`

- `CACHED -> CACHED`
  - repeated `fetch_niche()`
  - sync activity that changes refs/history but does not attach a checkout

This branch does not add automatic transitions back to `REMOTE_ONLY`; no
local deletion flow is being introduced here.

## Scope — what this branch does

1. **Add `NicheResidency` to `vault.py`.**
   Three values: `REMOTE_ONLY`, `CACHED`, `CHECKED_OUT`.

2. **Add `niche_residency()` to `vault.py`.**
   It computes residency from existing local state only:
   - git dir absent -> `REMOTE_ONLY`
   - git dir present, no checkout row -> `CACHED`
   - git dir present, checkout row present -> `CHECKED_OUT`

3. **Make no-checkout failures residency-aware.**
   Extend `vault.NoCheckoutError` with a `residency` field and make its
   message actionable:
   - `REMOTE_ONLY`: tell the caller they need local niche data first
     (`fetch_niche()` / equivalent fetch flow), then a checkout
   - `CACHED`: tell the caller they can attach a checkout directly

4. **Preserve that richer error through the sync layer.**
   `shared_file_vault.sync.NoCheckoutError` currently wraps the vault-layer
   exception. This branch must carry the residency information through that
   wrapper so CLI and web callers do not lose it.

5. **Expose residency from `list_niches()`.**
   Add a `"residency"` field to each dict returned by `list_niches()`, using
   the enum's string value. This is computed on read; no schema change.

6. **Update CLI `list`.**
   Show residency explicitly for each niche. Preserve the useful existing
   information when possible:
   - still show checkout path when one exists
   - replace the old generic `(no checkout)` fallback with the specific
     residency label

7. **Update the spec.**
   Add a "Niche residency" section to `packages/shared-file-vault/spec.md`
   describing the three modes, what they mean, and which operations move a
   niche between them.

8. **Add micro tests.**
   Cover the new residency API, residency-aware error behavior, and surfaced
   caller-visible outputs.

## Scope — what this branch explicitly does NOT do

- Remove the transit work tree (issue #82).
- Refactor `gitCmd` or abstract the git backend (issue #78).
- Add background-fetch automation mentioned in #81.
- Change any on-disk storage layout or DB schema.
- Introduce a fourth residency mode for stale checkout paths.
- Change join-flow semantics beyond clearer state naming and clearer errors.

## Key files

- `packages/shared-file-vault/shared_file_vault/vault.py` — residency enum,
  computation, and vault-layer errors
- `packages/shared-file-vault/shared_file_vault/sync.py` — preserve residency
  through sync-layer exceptions
- `packages/shared-file-vault/shared_file_vault/cli.py` — list output update
- `packages/shared-file-vault/shared_file_vault/web.py` — optional alignment
  if residency is surfaced in UI helpers/messages
- `packages/shared-file-vault/spec.md` — residency model and transitions
- `packages/shared-file-vault/tests/test_vault.py` — core micro tests

## Order of work

1. Add `NicheResidency` and `niche_residency()` in `vault.py`.
2. Update `vault.NoCheckoutError` to carry `residency` and produce
   state-specific guidance.
3. Update `sync.NoCheckoutError` and wrapping code so residency survives
   vault -> sync propagation.
4. Update `list_niches()` to include `"residency"`.
5. Update CLI `list` output to show residency without losing checkout-path
   visibility.
6. Update the spec with the residency model and transitions.
7. Add and run micro tests covering API behavior, error behavior, and CLI
   output.

## Validation

To convince a skeptical reviewer this branch is both correct and contained,
validation needs to show not just that the new feature exists, but that it
fits the current design cleanly and does not silently disturb other behavior.

### Feature correctness

- New micro tests cover `niche_residency()` for all three states:
  - niche only in registry -> `REMOTE_ONLY`
  - local git dir with no checkout -> `CACHED`
  - checkout registered -> `CHECKED_OUT`
- New micro tests verify `list_niches()` includes `"residency"` and that the
  reported value matches actual local state.
- New micro tests verify `vault.NoCheckoutError` differs for `REMOTE_ONLY`
  versus `CACHED`, and that each message tells the user the right next step.
- New micro tests verify the sync-layer wrapper preserves residency-aware
  behavior instead of collapsing back to a generic message.
- CLI validation shows `sfv list` reports the correct residency label in all
  three states, while still showing the checkout path when one exists.

### Repo integrity / containment

- All existing Shared File Vault micro tests still pass.
- No DB schema version changes are required.
- No storage paths or checkout semantics change.
- Existing join flow remains the same:
  `fetch -> checkout -> merge`.
- The branch only adds explicit state vocabulary and clearer inspection/error
  reporting; it does not couple residency work to transit removal, git
  abstraction, or background automation.
