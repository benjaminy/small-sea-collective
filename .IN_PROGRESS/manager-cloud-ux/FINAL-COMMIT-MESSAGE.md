Manager UX for Core cloud allocation and repair (#139)

Present Hub session authorization and current Core-route state as separate facts, and give each
typed Hub storage precondition the repair that actually fixes it. `cloud_location_missing` and
`cloud_credentials_missing` no longer collapse into `storage_not_configured`: they map to
`location_missing` and `credentials_missing`, which reconciliation can and cannot repair
respectively, and each gets its own web and CLI guidance. A push the Hub refuses now names its
route reason and offers the existing reconciliation action instead of rendering the Hub's raw
JSON body.

No new allocation or publication path: this builds on #208's `reconcile_team_route` and its
Manager-generated location. Linked-device allocation ownership and signer identity are
deliberately untouched and tracked separately.
