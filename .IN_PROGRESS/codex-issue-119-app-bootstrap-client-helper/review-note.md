# Review Note

This branch adds a `SmallSeaAppBootstrapRequired` exception to `small-sea-client`.
It recognizes the Hub's top-level `409 app_bootstrap_required` response before generic conflict handling, preserves raw reason strings, and gives apps a canonical `user_message` telling the user to open Manager.

The focused micro tests cover all current reason values, unknown future reasons, `team=None`, `str(exc)`, non-JSON fallthrough, and both JSON and non-JSON non-bootstrap `409` conflicts.
