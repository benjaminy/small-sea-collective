# Branch Plan: Three-mode niche residency (issue #81)

## Goal

Formalise the three residency modes a niche can be in on a local device, make
them inspectable via vault API and CLI, and improve error messages that fire
when an operation requires a higher residency level than the niche currently
has.

## Background

After #80 a niche is either "not present" or "has a checkout". But the vault
already supports a middle state implicitly: the niche git dir exists and may
have fetched refs, but no checkout is attached. Issue #82 (transit removal)
and #78 (gitCmd cleanup) are coming up; this branch deliberately avoids those
areas.

## Three residency modes

| Mode | Condition |
|------|-----------|
| `REMOTE_ONLY` | No niche git dir on this device. Known only via registry. |
| `CACHED` | Niche git dir exists (possibly with fetched refs), but no checkout attached. |
| `CHECKED_OUT` | Niche git dir exists and a checkout is registered in checkouts.db. |

## Scope — what this branch does

1. **`NicheResidency` enum** (`vault.py`) — three values: `REMOTE_ONLY`,
   `CACHED`, `CHECKED_OUT`.

2. **`niche_residency()` function** (`vault.py`) — returns the `NicheResidency`
   for a given niche. Logic:
   - git dir absent → `REMOTE_ONLY`
   - git dir present, no checkout row → `CACHED`
   - git dir present, checkout row exists → `CHECKED_OUT`

3. **Richer `NoCheckoutError`** — distinguish "remote only" vs "cached" in the
   error message so callers know whether they need to fetch first or just
   attach a checkout. Add a `residency` field to the exception.

4. **`list_niches` includes residency** — add a `"residency"` key (string
   value of the enum) to each niche dict returned by `list_niches()`. No
   DB schema changes needed; this is computed on read.

5. **CLI `list` command** — display residency mode next to each niche name
   instead of the current `(no checkout)` fallback.

6. **Spec update** (`spec.md`) — add a "Niche residency" section describing
   all three modes and the transitions between them.

7. **Micro tests** — cover `niche_residency()` for all three modes; cover the
   improved `NoCheckoutError` message for `REMOTE_ONLY` vs `CACHED`.

## Scope — what this branch explicitly does NOT do

- Remove transit work tree (issue #82).
- Refactor `gitCmd` or abstract the git backend (issue #78).
- Add background-fetch automation (mentioned in #81 as future work).
- Change any on-disk storage layout or DB schema.

## Key files

- `packages/shared-file-vault/shared_file_vault/vault.py` — main changes
- `packages/shared-file-vault/shared_file_vault/cli.py` — list command update
- `packages/shared-file-vault/spec.md` — spec update
- `packages/shared-file-vault/tests/test_vault.py` — micro tests

## Order of work

1. Add `NicheResidency` enum and `niche_residency()` to `vault.py`.
2. Update `NoCheckoutError` to carry a `residency` field and use it in the
   error message.
3. Update `list_niches()` to include `"residency"` in each returned dict.
4. Update CLI `list` command to show residency mode.
5. Add micro tests.
6. Update `spec.md`.

## Validation

- All existing tests pass.
- New micro tests cover all three residency modes.
- New micro tests verify `NoCheckoutError` message differs for `REMOTE_ONLY`
  vs `CACHED`.
- `sfv list` output shows correct residency label for a niche at each of the
  three states.
