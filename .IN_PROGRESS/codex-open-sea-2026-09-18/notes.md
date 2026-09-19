# Notes

- Issue 247 (leg 2): the Hub now declares `small-sea-manager` at runtime; Files declares a `test` extra with `small-sea-hub` and `small-sea-manager`.
  Manager runtime imports no Hub, so no runtime cycle.
  Built wheel metadata shows both; an isolated venv with only the Hub's declared sibling wheels imports `small_sea_hub.server`, and fails without the Manager wheel.
  Third-party packages came from the shared venv via PYTHONPATH because a fully offline install could not resolve registry packages from cache.
