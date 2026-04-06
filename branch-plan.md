# Branch Plan

Branch plan for `opt-in-opt-out-crypto`, covering GitHub issue `#42` and its
follow-up comment.

## Branch Goal

Generalize Hub encryption beyond the current `vault/` special case so normal
team-app traffic uses standard team encryption by default, while still allowing
explicit passthrough exceptions for onboarding, bootstrap, and future
intentionally public paths.

## Why This Branch Exists

Right now the Hub already marks normal team sessions as `encrypted`, but the
actual crypto seam in `small_sea_hub/crypto.py` only activates for `vault/`
paths. That is enough for Shared File Vault, but it is not yet a real Hub-level
encryption policy.

The issue comment sharpens the product requirement:

- default team traffic should be wrapped in standard team encryption
- some traffic must remain plaintext by explicit choice
- the API needs real options, not accidental behavior from path conventions

## Branch Success

This branch succeeds if, at the end:

1. Hub encryption handling is an explicit policy surface, not a hard-coded
   `vault/` prefix check
2. normal team-app traffic can opt into the standard encrypted path without
   inventing app-specific naming tricks
3. invitation/bootstrap flows still work because passthrough cases are
   deliberate and documented
4. micro tests make the encrypted-vs-passthrough behavior easy to trust

## Likely Shape

- replace `_ENCRYPTED_PREFIXES` with an explicit Hub encryption policy
- keep `NoteToSelf` traffic passthrough
- make standard team traffic encrypted by default
- add a narrow way for callers to request passthrough for bootstrap/public
  cases
- update current Hub callers that rely on plaintext team-repo bootstrap paths

## Likely Touch Points

- `packages/small-sea-hub/small_sea_hub/crypto.py`
- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/cod-sync/cod_sync/protocol.py`
- Manager invitation/bootstrap flows and the related micro tests

## Out Of Scope

- redesigning invitation tokens or membership flows
- changing sender-key storage or ratchet architecture
- solving every future public-sharing product case in this branch

## Validation

- existing Shared File Vault encrypted flow still passes
- invitation acceptance bootstrap still passes end to end
- a new micro test proves a non-`vault/` team path can use encryption
- a new micro test proves explicitly passthrough team traffic stays plaintext
- Hub remains the only Small Sea component doing network I/O
