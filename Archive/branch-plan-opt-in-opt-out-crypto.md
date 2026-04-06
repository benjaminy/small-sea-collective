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
Important clarification: the carveout is only for invitee-directed bootstrap
artifacts. Ordinary team DB updates that happen during the invitation flow are
still normal team traffic and should use encrypted team sessions.

## Design

- Session mode (`encrypted` / `passthrough`) is chosen by the caller in
  `/sessions/request`, stored on the `PendingSession` row, and carried through
  `/sessions/confirm` into the real `SmallSeaSession`.
- Both the Hub HTTP API and the small-sea-client library default to `encrypted`
  when the caller omits mode. There is no special NoteToSelf carveout in the
  Hub or client library. If a caller needs plaintext for a rare workflow, it
  must ask for `passthrough` explicitly.
- Once created, a session's mode does not change for the life of that token.
- The Hub applies crypto based solely on session mode — no path inspection.
- If an app needs both encrypted and plaintext traffic for one team, it opens
  two sessions. There is no "one session per berth" rule — the Hub allows
  multiple concurrent sessions for the same app+team.
- This branch does not introduce separate path namespaces for encrypted vs
  passthrough traffic. Readers and writers for a given workflow must stay on the
  same agreed mode. The strong default for ordinary team traffic is encrypted;
  exceptions should be few and visibly special.
- The Manager's session cache currently keys by `(app, team)`. For this branch,
  the cache key widens to include mode so the Manager can hold both an
  encrypted and a passthrough session for the same team. (The `app` dimension
  in the cache key is redundant — the Manager always uses
  `SmallSeaCollectiveCore` — but simplifying that is a separate cleanup;
  see issue #46.)
- Full Manager UX for showing or choosing multiple concurrent session modes can
  defer to issue `#46`. This branch only needs enough Manager handling to keep
  validation honest: request the special passthrough session when needed and
  mark it visibly.
- Add `mode` to the existing `/session/info` response dict so a resumed token
  can report which policy it carries.
- Passthrough team sessions are a convention-backed escape hatch, not a proof
  that plaintext is impossible to request. The safety improvement in this branch
  is that the choice is explicit, visible, and fixed at session creation time.
- Non-standard passthrough team sessions should be visibly special during
  approval. For this branch, a temporary alarming marker such as `[unsafe]`
  should appear in the OS notification text, the pending-session list in the
  sandbox dashboard, and the session approval card HTML. Final wording can
  change later.
- Per-request encryption toggles are out of scope for this branch.
- `path_uses_group_crypto` and `_ENCRYPTED_PREFIXES` go away.

## Branch Success

1. Session mode is requested in `/sessions/request`, persists through
   confirmation, and defaults to encrypted for normal team sessions
2. Hub encrypt/decrypt is driven entirely by session mode, not path
3. Existing vault traffic still works (now encrypted because the session is,
   not because the path starts with `vault/`)
4. Invitation/bootstrap flows are explicitly documented as a separate
   passthrough first-contact path for invitee-directed artifacts, not
   conflated with ordinary team traffic
5. Non-standard passthrough team sessions are visibly marked during approval
6. Micro tests cover both encrypted and passthrough session behavior

## Likely Touch Points

- `packages/small-sea-hub/small_sea_hub/crypto.py` — remove `path_uses_group_crypto`,
  `_ENCRYPTED_PREFIXES`; upload/download just checks `ss_session.mode`
- `packages/small-sea-hub/small_sea_hub/backend.py` — add `mode` parameter to
  `request_session` (default `"encrypted"`); add `mode` column to
  `PendingSession`; `confirm_session` copies mode from pending row instead of
  computing it; `open_session` smoke-test shortcut accepts and passes `mode`
- `packages/small-sea-hub/small_sea_hub/server.py` — add `mode` field to
  `SessionRequestReq`; pass it through to `request_session`; add `mode` to
  `/session/info` response
- Session approval / notification text — `_send_os_notification`, pending-session
  list (`devtools/sandbox/sandbox/templates/fragments/pending_sessions.html`),
  approval card (`packages/small-sea-manager/small_sea_manager/templates/fragments/session_card.html`),
  and the Manager's active team-session UI (`team_session.html`) visibly mark
  passthrough team sessions
- `packages/small-sea-client/small_sea_client/client.py` — session open/request
  helpers accept `mode` parameter (default `"encrypted"`)
- `packages/small-sea-manager/small_sea_manager/manager.py` — session cache key
  widens to include mode; invitation flow requests passthrough explicitly
- Docs: add a brief encryption policy note, including what generic mode markers
  are safe to show in the Hub status UI

## Out Of Scope

- Redesigning invitation tokens or membership flows
- Designing a stronger first-contact cryptographic protocol for invitations
- Changing sender-key storage or ratchet architecture
- Multiple encryption flavors beyond `encrypted` / `passthrough`
- Full Manager UX for browsing or choosing multiple concurrent session modes

## Validation

- Existing Shared File Vault encrypted flow still passes
- Invitation acceptance bootstrap still passes end to end
- A session opened without an explicit mode defaults to encrypted
- There is no special NoteToSelf default in the Hub or client; callers that need
  plaintext request `passthrough` explicitly
- A session's mode cannot silently change after creation
- A new micro test proves a non-`vault/` team path uses encryption on an
  encrypted session
- A new micro test proves a passthrough session stays plaintext
- A new micro test proves request/confirm preserves the requested mode
- Smoke tests exercise both encrypted and passthrough modes with appropriate
  expectations for each
- Invitation-flow tests show the split honestly: invitee-directed bootstrap
  artifacts may stay plaintext, but normal team DB pushes in the flow still use
  encrypted team sessions
- A passthrough team session is visibly marked in OS notification, pending list,
  and approval card
- A resumed token can report its mode correctly via `/session/info`
- Hub remains the only Small Sea component doing network I/O
