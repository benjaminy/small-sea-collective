# Branch Plan

Branch plan for `opt-in-opt-out-crypto`, covering GitHub issue `#42` and its
follow-up comment.

## Branch Goal

Make Hub encryption a session-level choice for team berths: encrypted by
default for normal team sessions, passthrough only when explicitly requested.
Replace the current hard-coded `vault/` path prefix check with this policy.

## Why This Branch Exists

The Hub already does group encryption, but only for paths starting with
`vault/`. All other traffic currently passes through in plaintext despite going
through third-party cloud storage.

The encryption decision should not depend on path naming conventions. It should
be an explicit, per-session policy set when the session request is opened.

This branch makes one deliberate design choice: one Hub session token carries
one encryption policy. That is a stronger commitment than "the API has
options", but it keeps the first version simple and avoids mixing plaintext and
ciphertext writes behind the same token.

Invitation/bootstrap needs an explicit carveout. In a decentralized first
contact flow, Alice and Bob do not yet share the standard team keys, so the
earliest invitation steps cannot use the normal team-traffic encryption path.
If later we introduce a more clever first-contact design using pre-existing
public keys, that is still a different protocol from ordinary team traffic.
This branch should treat invitation/bootstrap as a separate special-case flow,
not as evidence that normal team sessions should stay path- or convention-based.

## Design

- Session mode (`encrypted` / `passthrough`) is chosen by the caller in
  `/sessions/request`, stored on the pending session, and carried through
  `/sessions/confirm`.
- `NoteToSelf` stays `passthrough` in this branch.
- Non-`NoteToSelf` team sessions default to `encrypted`.
- Callers may explicitly request `passthrough` for narrow bootstrap/public
  cases.
- Once created, a session's mode does not change for the life of that token.
- The Hub applies crypto based solely on session mode — no path inspection.
- If an app needs both encrypted and plaintext traffic for one team, it opens
  two sessions.
- Session caches/helpers therefore need to key by `(app, team, mode)`, not just
  `(app, team)`.
- Session metadata should expose `mode` so a resumed token can report which
  policy it carries.
- Passthrough team sessions are a convention-backed escape hatch, not a proof
  that plaintext is impossible to request. The safety improvement in this branch
  is that the choice is explicit, visible, and fixed at session creation time.
- Non-standard team sessions should be visibly special during approval. For this
  branch, a temporary alarming marker such as `[unsafe]` in the PIN prompt /
  approval text is good enough, even if the final wording changes later.
- Per-request encryption toggles are out of scope for this branch.
- `path_uses_group_crypto` and `_ENCRYPTED_PREFIXES` go away.

## Branch Success

1. Session mode is requested in `/sessions/request`, persists through
   confirmation, and defaults to encrypted for normal team sessions
2. Hub encrypt/decrypt is driven entirely by session mode, not path
3. Existing vault traffic still works (now encrypted because the session is,
   not because the path starts with `vault/`)
4. Invitation/bootstrap flows are explicitly documented as a separate
   passthrough first-contact path, not conflated with ordinary team traffic
5. Non-standard passthrough team sessions are visibly marked during approval
6. Micro tests cover both encrypted and passthrough session behavior

## Likely Touch Points

- `packages/small-sea-hub/small_sea_hub/crypto.py` — remove path-based check
- `packages/small-sea-hub/small_sea_hub/backend.py` — session creation accepts
  mode from caller, defaults to `encrypted`; upload/download uses session mode
  directly
- `packages/small-sea-hub/small_sea_hub/server.py` — pass mode through the
  session creation API and expose mode in session metadata
- Session approval / notification text — visibly mark non-standard passthrough
  team sessions
- `packages/small-sea-client/small_sea_client/client.py` — session open/request
  helpers need to carry mode and recover it from session metadata
- `packages/small-sea-manager/small_sea_manager/manager.py` — invitation flow
  and session caching need to handle explicit mode
- Docs: add a brief encryption policy note

## Out Of Scope

- Redesigning invitation tokens or membership flows
- Designing a stronger first-contact cryptographic protocol for invitations
- Changing sender-key storage or ratchet architecture
- Multiple encryption flavors beyond `encrypted` / `passthrough`

## Validation

- Existing Shared File Vault encrypted flow still passes
- Invitation acceptance bootstrap still passes end to end
- A session opened without an explicit mode defaults to encrypted for a normal
  team berth
- A session's mode cannot silently change after creation
- A new micro test proves a non-`vault/` team path uses encryption on an
  encrypted session
- A new micro test proves a passthrough session stays plaintext
- A new micro test proves request/confirm preserves the requested mode
- A passthrough team session is visibly marked in approval / PIN text
- A resumed token can report its mode correctly through session metadata
- Hub remains the only Small Sea component doing network I/O
