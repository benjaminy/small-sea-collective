# Notes

- Issue 247 (leg 2): the Hub now declares `small-sea-manager` at runtime; Files declares a `test` extra with `small-sea-hub` and `small-sea-manager`.
  Manager runtime imports no Hub, so no runtime cycle.
  Built wheel metadata shows both; an isolated venv with only the Hub's declared sibling wheels imports `small_sea_hub.server`, and fails without the Manager wheel.
  Third-party packages came from the shared venv via PYTHONPATH because a fully offline install could not resolve registry packages from cache.
- Issue 247 (leg 4): `uv.lock` regenerated with `uv lock --offline`; `uv lock --offline --check` passes and no locked package version changed.
  The Files `test` extra now also declares `pytest` and `boto3`, which Files tests import directly.
  In a fresh venv holding only Files and its runtime sibling wheels, Files test collection fails on `small_sea_hub`.
  After adding the `test` extra's sibling closure, the Files tests run 78 passed, 3 skipped.
  Third-party packages were again borrowed from the shared venv without `.pth` processing, and `test_support.py` was copied from the repo root.
