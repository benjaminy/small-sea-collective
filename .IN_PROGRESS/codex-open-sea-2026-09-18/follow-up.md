# Follow-up

- Manager tests import the Hub, but the Hub now depends on the Manager at runtime.
  A Manager `test` extra on `small-sea-hub` would form an extras-only cycle; decide whether that is acceptable or whether those tests belong elsewhere.
- Files now declares its test runners; other packages still get pytest (and Manager/Hub test helpers) from root `dev-dependencies`.
- Files tests import the repo-root `test_support.py`, which no package ships; an isolated Files test run must copy it in.
- `test_dropbox_latency.py` imports `requests`, which nothing in Files declares; it arrives transitively. Left undeclared as a pre-existing gap.
