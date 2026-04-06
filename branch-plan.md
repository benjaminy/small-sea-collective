# Branch Plan

Branch plan for `opt-in-opt-out-crypto`, covering GitHub issue `#42` and its
follow-up comment.

## Branch Goal

Make Hub encryption a session-level choice: encrypted by default, passthrough
only when explicitly requested at session creation time. Replace the current
hard-coded `vault/` path prefix check with this policy.

## Why This Branch Exists

The Hub already does group encryption, but only for paths starting with
`vault/`. All other traffic — including NoteToSelf and non-vault team traffic —
passes through in plaintext despite going through third-party cloud storage.

The encryption decision should not depend on path naming conventions. It should
be an explicit, per-session policy set at creation time.

## Design

- Session mode (`encrypted` / `passthrough`) is chosen by the caller at session
  creation time. Default is `encrypted`.
- The Hub applies crypto based solely on session mode — no path inspection.
- If an app needs both encrypted and plaintext traffic, it opens two sessions.
- `path_uses_group_crypto` and `_ENCRYPTED_PREFIXES` go away.

## Branch Success

1. Session mode is a caller choice at creation time, defaulting to encrypted
2. Hub encrypt/decrypt is driven entirely by session mode, not path
3. Existing vault traffic still works (now encrypted because the session is,
   not because the path starts with `vault/`)
4. Invitation/bootstrap flows still work via passthrough sessions
5. Micro tests cover both encrypted and passthrough session behavior

## Likely Touch Points

- `packages/small-sea-hub/small_sea_hub/crypto.py` — remove path-based check
- `packages/small-sea-hub/small_sea_hub/backend.py` — session creation accepts
  mode from caller, defaults to `encrypted`; upload/download uses session mode
  directly
- `packages/small-sea-hub/small_sea_hub/server.py` — pass mode through the
  session creation API
- `packages/small-sea-manager/small_sea_manager/manager.py` — invitation flow
  explicitly requests passthrough
- Docs: add a brief encryption policy note

## Out Of Scope

- Redesigning invitation tokens or membership flows
- Changing sender-key storage or ratchet architecture
- Multiple encryption flavors (future work; mode is a string, not a bool,
  so it extends naturally)

## Validation

- Existing Shared File Vault encrypted flow still passes
- Invitation acceptance bootstrap still passes end to end
- A new micro test proves a non-`vault/` team path uses encryption on an
  encrypted session
- A new micro test proves a passthrough session stays plaintext
- Hub remains the only Small Sea component doing network I/O
