# Follow-up

- Manager tests import the Hub, but the Hub now depends on the Manager at runtime.
  A Manager `test` extra on `small-sea-hub` would form an extras-only cycle; decide whether that is acceptable or whether those tests belong elsewhere.
- `uv.lock` was not regenerated for the Hub and Files dependency changes; run `uv lock` with network access.
- Test runners (pytest, boto3, respx) come from root `dev-dependencies`, not per-package extras; left as is.
