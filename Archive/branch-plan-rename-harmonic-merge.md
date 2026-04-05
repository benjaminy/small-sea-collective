# Harmonic Merge Rename

Branch plan for `rename-harmonic-merge`.

## Goal

Replace the name Harmonic Merge with Splice Merge.

Use the punctuation that is most appropriate in each context:

- `harmonic-merge` -> `splice-merge`
- `harmonic_merge` -> `splice_merge`
- `Harmonic Merge` -> `Splice Merge`
- `HarmonicMerge` -> `SpliceMerge`

Compatibility does not matter for this branch.
No migration shims are needed.
Check all code, docs, and package metadata.

## Result

This branch completed the rename in the active codebase:

- package path renamed to `packages/splice-merge/`
- import package renamed to `splice_merge`
- distribution renamed to `splice-merge`
- SQLite merge driver renamed to `splice-sqlite-merge`
- manager wiring, docs, and workspace metadata updated to match

Validation run during implementation:

- `uv run pytest packages/splice-merge/tests/test_merge.py`
- `uv run pytest packages/small-sea-manager/tests/test_merge_conflict.py`
