# Bootstrap fix review assessment

The low-effort Opus review ran against the small patch, not the full architecture.

- The directory-id lookup is not an authorization bypass: `_resolve_berth` checks the marker before opening Core, and the direct-id micro test verifies rejection.
- The old Manager helper import remains available because provisioning imports it from the shared module; no compatibility shim is needed.
- The marker writer creates its parent directories and initialization preserves them; fresh-install and pre-adoption retry cases pass.
- A truncated marker could interrupt nickname discovery for unrelated identities.
  The shared helper now treats unreadable or malformed content as blocked, and three marker cases exercise discovery and direct access.
- The two database reads in finalization now explicitly close their connections.

The review does not establish whole-snapshot authenticity.
That remains a separate bootstrap design and implementation gap.
