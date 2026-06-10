# Review Note — Issue #138: Remove Legacy `team_device` Transport Fallback

## TL;DR for a skeptical reviewer

`team_device` no longer has `protocol`/`url`/`bucket` columns at all. Peer storage
routing comes solely from signed `member_berth_storage_announcement` rows. The three
production flows that allocate Core berth storage now publish that announcement
(previously only tests did), and `accept_invitation` — which used to discard the
invitee's allocation and publish nothing — now publishes the invitee's own signed
announcement. There is no `legacy-fallback` status anywhere.

The skeptical-reviewer bar from the plan was: *tests fail if legacy `team_device`
storage fields can still affect peer routing.* That bar is now structurally
unmeetable in the wrong direction — the columns are gone, so no code path can read
them. The decisive creator test asserts their absence via `PRAGMA table_info`.

## What to look at first

- `provisioning.py`
  - `_insert_member_berth_storage_announcement` (new in-transaction core) +
    `publish_member_berth_storage_announcement` (thin wrapper). The reason for the
    split is transaction safety — see design record §"Transaction-safe core".
  - `create_team`: in-transaction publish, no more transport write to `team_device`.
  - `accept_invitation`: captures the allocation (previously discarded at the old
    `provisioning.py:4780`), publishes the acceptor's signed announcement, commits it.
  - `finalize_linked_device_bootstrap`: transport write removed; publishing deferred
    (FOLLOW-UP §2 — this path derives a bucket name, it is not allocation-producing).
  - `_upsert_team_device_row`: `protocol`/`url`/`bucket` params + columns dropped.
  - `_migrate_team_db_to_member_and_team_device`: inline CREATE + INSERT updated to
    match the column drop.
  - `_legacy_transport_by_member`: deleted; `_effective_transports_by_member` no
    longer passes `legacy_fallback`.
- `backend.py` (Hub): `_download_peer_file` → `_select_member_berth_storage` directly;
  deleted `_legacy_transport_for_member`, `_load_member_transport_announcements`, and
  the unreachable `_effective_peer_transport[_selection]` member-level path.
- `wrasse_trust/transport.py`: `legacy_fallback` param + `legacy-fallback` branch
  removed from both selection functions.
- `sql/core_other_team.sql`: `team_device` DDL minus transport columns.

## Decisive end-to-end coverage (both roles, no manual publish)

- **Creator** — `test_peer_transport.py::test_create_team_publishes_creator_storage_for_peer_download`:
  `create_team` alone (no manual `publish_*` call) makes the creator's berth storage
  downloadable by a peer through `_download_peer_file`; also asserts `team_device`
  has no `protocol`/`url`/`bucket` columns.
- **Invitee** — `test_invitation.py::test_full_invitation_flow`: the real invite/accept
  flow with no manual/helper publish for Bob proves `accept_invitation` auto-published
  a valid, **Bob-signed** announcement whose `(protocol,url,location)` matches Bob's
  allocation. This is the path that previously published nothing.

The invitee test proves *publishing*; the creator test proves the full *read* path;
the same `_download_peer_file`/selection code serves both, and the wrasse-trust unit
tests cover selection (tampered / wrong-signer / untrusted → `missing`). The one
thing **not** asserted end-to-end is "Alice downloads Bob's file in one process" —
that additionally needs cross-member announcement merge, which is existing sync-layer
plumbing out of scope for #138 (FOLLOW-UP §3).

## Behavior changes a reviewer might flag (all intended)

- Manager members listing: a self member with no signed member-transport announcement
  now shows `transport_status: "missing"` + `needs_transport_announcement: True`
  (was `"legacy-fallback"` / `False`). Updated `test_member_transport.py` + `members.html`.
- `member_transport_announcement` and `select_effective_member_transport` are KEPT —
  they back the live `announce_transport` web feature; #138 removed the fallback, not
  that feature. (#123 grooming: see FOLLOW-UP §1.)
- `test_peer_transport.py`'s two "falls_back" tests still pass and still assert the
  same buckets, but the thing they now fall back *to* is `create_team`'s valid
  announcement, not `team_device`. They remain valid precedence tests (newer-but-invalid
  announcement skipped in favor of older-valid).

## Test results

- `wrasse-trust` + `shared-file-vault` + hub `test_peer_transport` + `test_cloud_api`:
  126 passed, 3 skipped.
- Full `small-sea-manager` + `small-sea-hub` suites: **204 passed, 1 error**.
- The single error is `test_notifications.py::test_notification_roundtrip`, which
  requires a Docker `ntfy` container; no Docker daemon is available in this
  environment, so it errors at fixture setup. Unrelated to this change (it does not
  touch transport/announcement/team_device code).

## Out-of-branch noise

- `uv.lock` gained a `tide-table` workspace entry — an incidental `uv run` sync for an
  already-merged package (PR #149), not part of #138. Left as a correct lockfile sync.
