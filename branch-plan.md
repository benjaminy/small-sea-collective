# Branch Plan: Device Link Cert-History Slice

**Branch:** `device-linking`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`,
`architecture.md`  
**Related archive plans:** `Archive/branch-plan-identity-model-rethink.md`,
`Archive/branch-plan-admin-control-clarification.md`,
`Archive/branch-plan-device-oriented-identity-first-steps.md`,
`Archive/branch-plan-better-fetch-merge-separation.md`
**Deferred follow-up issues:** #57, #58, #59  
**Related existing issues:** #48, #44

## Context

The repo now has the first half of the device-only model in code:

- team creation emits a self-issued `membership` cert
- invitations admit a new member by issuing `membership`
- the old per-team identity-key layer is gone from the live path
- fetch/merge separation is in place (`CodSync.fetch_from_remote` +
  `merge_from_ref`, parked refs under `refs/peers/<member_id_hex>/<branch>`)

What is still missing:

- `device_link` is documented but is not a supported live cert type
- there is no code path for "same member, second team-device key"
- Git link signatures still assume one effective signing key per member

The previous version of this plan tried to solve all of the following at once:

- `device_link` certs
- device-aware Git signatures
- a new `member_device` table
- an admission pipeline for sync-arrived certs
- joining-device bootstrap/provisional state

That plan answered some real questions, but it also revealed that we were
designing several branches at once.

## Cut Line

This branch should prove the **trust model first**, using only cert history.

It should **not** introduce new shared derived trust state yet:

- no `member_device` table
- no admission pipeline
- no merge hooks for trust projection
- no `team_device_key` schema loosening for future rotation
- no whole-install/new-machine bootstrap flow

If the cert-history-only approach proves too awkward in practice, that will
give the next branch a concrete reason to introduce persisted projections or
admission plumbing.

## Proposed Goal

Land the smallest honest `device_link` slice:

1. `device_link` becomes a supported live cert type with issue/verify helpers
2. trusted device sets are computed directly from `key_certificate` history
   (`membership` + `device_link`) rather than from a new table
3. Manager gains an authorizing-side helper that admits an externally
   generated device public key for an existing member
4. Git link signatures become device-aware, so a link signed by any trusted
   linked device for a member verifies correctly
5. sender keys, peer routing, shared trust projections, and joining-device
   bootstrap remain deferred

## Why This Slice

This branch should separate three concerns:

- **Cert vocabulary and trust logic:** what `device_link` means
- **Git verification:** how a pushed link names the device that signed it
- **Operational multi-device runtime:** how multiple installations become fully
  live and stay in sync

The first two belong together. The third clearly does not.

## Proposed Scope

### 1. Add live `device_link` cert support

Update `wrasse_trust.identity`:

- add `CertType.DEVICE_LINK` to `SUPPORTED_CERT_TYPES`
- add `issue_device_link_cert(...)`
- add `verify_device_link_cert(...)`
- the subject key is the newly linked device key
- the only required claim is `member_id`
- the authorizing device is identified by the cert envelope signer, not a
  separate claim

Trust rule for this branch:

- the signer of a `device_link` cert must already be trusted for that same
  member in that same team
- trust is transitive across `device_link` edges
- invalid or unreachable `device_link` certs simply do not contribute to the
  trusted device set

### 2. Compute trusted device sets directly from cert history

For this branch, `key_certificate` remains the only shared trust state.

Add a helper that answers:

- "what device public keys are trusted for member M in team T?"

using only:

- the member's `membership` cert
- any reachable `device_link` certs whose signer is already trusted for that
  member

Properties:

- no extra synced SQL table is needed
- no merge hook is needed
- sync-arrived certs become effective automatically because verification reads
  cert history directly
- bogus or irrelevant cert rows are harmless as long as the helper ignores
  anything invalid, off-team, wrong-member, or unreachable

Implementation shape:

- put the graph/traversal logic in a pure helper over cert rows or decoded certs
- add a thin manager/provisioning wrapper that loads relevant certs from the
  team DB and calls that helper

### 3. Add an authorizing-side device-link helper

The production semantics should stay strict:

- the joining device generates its own keypair locally
- only the public key leaves that device
- the authorizing installation receives that public key and issues a
  `device_link` cert for it

So the helper for this branch should look like:

- "issue a `device_link` cert for this externally generated public key"

not:

- "mint a private key for some other device"

For this branch, that helper only needs to cover the **authorizing side**.
It does not need to solve the joining installation's full bootstrap lifecycle.

### 4. Make Git link signatures device-aware

Update CodSync link signatures so a verifier can tell which linked device signed
for a member.

Recommended shape for this branch:

- change the signature payload from:
  - `member_id -> signature`
- to:
  - `member_id -> { device_public_key, signature }`

Rationale for raw public keys:

- they are already public in the trust model
- they keep verification self-contained
- they avoid introducing a new indirection layer in the same branch

Verification rule:

- load the trusted device set for the member from cert history
- accept the signature only if the named device public key is in that set and
  the signature verifies under that key

### 5. Do minimal doc cleanup

This branch should update docs only enough to keep them honest:

- say that the first `device_link` implementation slice is cert-history-based
- explicitly defer persisted trusted-device state and admission pipelines
- explicitly defer joining-device bootstrap / whole-install linking

## Explicitly Out of Scope

- any new synced `member_device` table
- any admission pipeline or validate-on-insert projection step
- merge hooks that materialize trust state after sync
- changing `team_device_key` PK or doing rotation work
- whole-install/new-machine bootstrap
- NoteToSelf clone/setup flows for linking a new device
- joining-device provisional-state UX
- changing Cuttlefish sender-key identity from member-based to device-based
- simultaneous encrypted-send behavior from multiple linked devices
- peer download/routing that distinguishes sibling devices of one member
- revocation traversal over `device_link`
- device removal / re-key UX
- cross-team batching of device enrollment

## Open Questions

These still matter, but they no longer block the branch.

### 1. Where should trust traversal live?

Should the transitive "trusted device set for member M" logic live:

- in `wrasse_trust` as a pure trust helper, with manager code only loading rows
- or in manager/provisioning, with Wrasse Trust staying at the single-cert level?

My leaning is the first option.

### 2. Exact CodSync signature payload shape

Is the best wire shape:

- `member_id -> { device_public_key, signature }`

or a slightly different object/list form?

The branch should pick one simple shape and stick to it.

### 3. How much authorizing-side helper should be exposed?

Do we want only a low-level provisioning helper for now, or also a Manager
method thinly wrapping it?

This is implementation-shape, not model-shape.

## Concrete Change Areas

### 1. `wrasse_trust.identity`

- add live `DEVICE_LINK` support
- add issue/verify helpers
- add pure trust-resolution helper(s) for member device sets, or add them in a
  nearby trust module if that is a better fit
- add micro tests:
  - happy-path verification
  - wrong-member / wrong-team rejection
  - transitive signer case
  - unknown-signer case ignored by traversal

### 2. `small_sea_manager.provisioning`

- add authorizing-side helper to issue a `device_link` cert for an externally
  supplied public key
- store the resulting cert in `key_certificate`
- add helper(s) to load trusted device keys for a member from cert history
- do not add `member_device`
- do not add admission hooks

### 3. `cod_sync` signature shape

- update the link supplement signature shape to identify the signing device by
  raw public key alongside the member
- update verification to use the cert-history-derived trusted device set
- update existing tests and any fixture data that depend on the old signature
  shape

### 4. Micro tests

- `packages/wrasse-trust/tests/test_identity.py`
  - `device_link` happy-path verification
  - wrong-member / wrong-team rejection
  - transitive signer accepted
  - unknown signer ignored
- manager/provisioning micro test
  - create team
  - issue a `device_link` for an externally generated public key
  - trusted-device lookup now contains both the founding device and the linked
    device
- multi-installation-ish verification test
  - installation A issues and pushes a `device_link`
  - installation B fetches and merges the team repo
  - B computes the trusted device set from merged cert history
  - a signed bundle signed by the linked device verifies on B

## Risks

### 1. Read-time trust traversal may be awkward

If computing trusted device sets directly from cert history is too awkward or
too slow in practice, that is useful evidence for a follow-up branch adding a
persisted projection.

### 2. Git signature shape churn

Changing link signatures from member-only to device-aware is still a cross-cut
through CodSync and signed-bundle tests. The branch should keep that change as
small and explicit as possible.

### 3. Over-promising multi-device support

If the branch lands `device_link` plus device-aware Git signatures, readers may
infer that all linked devices are fully live. Docs and tests must be explicit
that this is not yet true for sender-key behavior, peer routing, or bootstrap.

## Validation

This branch is successful if:

- the code can issue and verify a real `device_link` cert
- trusted device sets are computed correctly from `key_certificate` history
- a transitively linked device is trusted
- a `device_link` signed by an unknown device is ignored
- signed-bundle verification succeeds for a member whose valid signer is one of
  several linked device keys
- in a multi-installation scenario, a `device_link` issued on installation A
  and merged on installation B is honored on B without any admission pipeline or
  auxiliary shared state
- the updated micro tests pass

Suggested validation command:

`uv run pytest packages/wrasse-trust/tests/test_identity.py packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_invitation.py packages/small-sea-manager/tests/test_signed_bundles.py`

## Locked Scoping Decision

This first `device_link` branch should prove the multi-device trust model using
only cert history plus device-aware Git signatures.

It should not introduce persisted trusted-device projections, merge-time
admission plumbing, or whole-install device-link bootstrap in the same slice.
