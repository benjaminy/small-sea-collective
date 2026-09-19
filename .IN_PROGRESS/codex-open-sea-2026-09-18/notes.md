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
- Issue 235 component (leg 6): `fetch_self_via_hub` now has Hub micro tests with an in-process Hub (ASGI TestClient, not TCP) and local MinIO.
  Two Files roots for Alice share one Hub and one store; this is not an independent-device capstone.
  The fetch sent only `GET /cloud_file`, left Alice's bucket unchanged, parked Alice's head rather than Bob's, and moved nothing until `merge_self`.
  `SelfFetchPartialError` now derives from `FilesSyncError`, like every other Files sync error.
- Issue 235 component (leg 7): the Files CLI exposes the own-store fetch as `fetch --from-self`, mirroring `merge --from-self`; `fetch` now requires exactly one of `--from-teammate` or `--from-self`.
  A niche failure after the registry fetch exits 1 with a one-line message that names the kept registry head and the niche error.
  The leg-6 Hub/MinIO test now records every `SmallSeaS3Adapter._upload` call and asserts none happened during the fetch, which catches rewrites of identical bytes that ETags would miss.
- Leg 8: `fetch_self_via_hub` treats only `NoPublishedHeadError` on the registry as absence; it still fetches the niche and returns `registry_sha=None`.
  This covers a push that published the niche and stopped before the registry.
  If the niche is also absent it raises `SelfFetchNoHeadError` and parks nothing; any other registry failure stops the fetch.
  `fetch --from-self` now also reports Cod Sync errors as one line instead of a traceback.
