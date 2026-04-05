# Wrasse Trust — Small Sea Identity and Trust

Wrasse Trust is the Small Sea package for identity, certification, and trust
evaluation. It owns the BURIED/GUARDED/DAILY key hierarchy and the web-of-trust
machinery that lets teammates vouch for each other without a central identity
provider.

## Scope

Wrasse Trust currently owns:

- participant key hierarchies
- certificate and revocation formats
- key-signing ceremony helpers
- trust graph traversal

Wrasse Trust does **not** own pairwise or group message encryption. That lives
in `cuttlefish`.

## Module Map

- `wrasse_trust.keys` — participant key hierarchies and protection levels
- `wrasse_trust.identity` — certificate and revocation issuance/verification
- `wrasse_trust.ceremony` — payloads and helpers for in-person signing
- `wrasse_trust.trust` — certificate graphs and trust-path search

## Design Notes

The trust model is intentionally separate from the session-crypto layer:

- trust decides which identities and keys should be believed
- cuttlefish decides how messages and bundles are encrypted in transit

That split keeps each package smaller and cleaner today. A future integration
layer will still need to bind "this encrypted action" to "this trusted
identity," but that binding is outside the scope of this package split.
