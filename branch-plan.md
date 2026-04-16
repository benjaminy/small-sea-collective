# Branch Plan: Vault peer-update UX (issue #13)

Branch: `issue-13-vault-peer-update-ux`
Tracking issue: [#13 — SharedFileVault — teammate update detection and pull UX](https://github.com/benjaminy/small-sea-collective/issues/13)

## Context

Issue #5's Hub-backed sync layer is in place (see
`Archive/branch-plan-vault-building-blocks.md`). Vault can open a team
session, push, fetch, merge, and render post-fetch parked state. What is
still missing is any sense of **"Bob may have changes I haven't fetched
yet"** — all current change-awareness kicks in only *after* a fetch has
already been done.

Two pieces of the infrastructure that were implicit in issue #13 have
since been verified to already exist:

- `GET /session/peers` already returns `signal_count` per peer
  (`packages/small-sea-hub/small_sea_hub/server.py` ~L698–717).
  `SmallSeaSession.session_peers()` wraps it; `sync.list_team_peers` passes
  it through. Vault currently ignores the field.
- `POST /notifications/watch` exists as a long-poll primitive
  (`server.py` ~L868). `SmallSeaSession.watch_notifications()` wraps it.
  No caller in Vault uses it yet.

## Goals

1. A per-peer, team-scoped "may have unfetched changes" hint derived from
   `signal_count` vs. a locally-persisted watermark.
2. The niche detail view surfaces that hint as a **state reminder**, not a
   prompt — un-dismissable, subtle, clears only on an actual fetch.
3. The peer panel refreshes in the background without a full page reload.

## Non-goals

- **Per-niche attribution** of unfetched updates. `signal_count` is
  per `(berth, peer)`, not per niche, so a bump means "Bob pushed
  something in this team" — it cannot tell us which niche without an extra
  registry fetch + tip diff. See follow-up below.
- **Push-based refresh.** Long-poll via `/notifications/watch` is
  deliberately deferred; see follow-up below.
- **Automatic background pulls.** The hint is a status indicator. The
  user still decides when to fetch.
- Rich conflict resolution UI.
- Reading team DBs from Vault runtime code.

## Design decisions

### Watermark granularity: team-scoped (not per-niche)

Per `signal_count`'s wire semantics, a team-scoped watermark is the honest
shape. Consequence: any push by Bob to *any* niche lights the "Bob has
changes" badge on every niche detail view in that team until Alice fetches
from Bob once. Alice's one "catch-up" fetch may land on a niche Bob didn't
actually push to — one wasted niche fetch per team per signal bump, worst
case. Acceptable for v1.

Upgrade path to per-niche is additive: insert a "fetch Bob's registry and
diff niche tips" step between observing `signal_count > watermark` and
computing the hint. v1 code paths keep working.

### Refresh transport: htmx polling (not SSE/long-poll)

`hx-trigger="every 20s"` on a new peer-panel fragment endpoint. The
response body is identical to the peer-panel render path used elsewhere
in the niche detail view, so an SSE-based v2 can reuse the same endpoint
by adding a stream that emits the same HTML on each `/notifications/watch`
wakeup. Polling stays as a graceful-degradation fallback.

20s latency is below the noise floor for "user awareness" — awareness of
a teammate push is not a time-critical signal.

### UI framing: state reminder, un-dismissable

The hint means "your watermark is behind for this peer." The only
semantic way to clear it is to actually fetch. Specifics:

- **Copy**: "{Name} has changes since you last fetched" (status tone).
  Not "New changes from {Name} — check?" (prompt tone).
- **Visual weight**: small badge or dot near the peer name. Not a
  full-width colored banner.
- **No dismiss button.** Only the existing "Check For Updates" button
  clears the hint, because only a fetch bumps the watermark.
- If Alice never fetches from Bob, Bob's badge stays lit indefinitely.
  Under the state-reminder framing this is correct; it would be obnoxious
  under a prompt framing.

## Work items

### 1. Watermark persistence (`shared_file_vault/sync.py`)

- Extend vault config TOML to include
  `[peer_signal_watermarks."{team_name}"."{member_id}"] = int`.
  Update `_dump_toml` and `load_config` accordingly (including test-only
  config override path `SMALL_SEA_VAULT_CONFIG`).
- Helpers:
  - `get_signal_watermark(team_name, member_id) -> int`
  - `set_signal_watermark(team_name, member_id, count) -> None`
  - `clear_signal_watermark(team_name, member_id) -> None`
- In `fetch_via_hub`, read the *observed* `signal_count` from
  `session_peers()` for that member *before* the fetch, then after the
  fetch succeeds, write it as the new watermark. Observing before fetch
  avoids a race where a peer pushes a second time during the fetch and we
  accidentally mark that second push as already seen.

Micro tests:

- round-trip config write/read with watermarks
- `fetch_via_hub` bumps the watermark on success for the fetched peer
- a failed fetch leaves the watermark unchanged
- unrelated peers' watermarks are not touched

### 2. Hint composition

- Extend `PeerUpdateStatus` with two fields:
  - `current_signal_count: int`
  - `last_seen_signal_count: int`
  and a derived property `has_unfetched_hint: bool` (true when
  `current_signal_count > last_seen_signal_count`).
- Plumb `signal_count` from `session_peers()` into `peer_update_status`
  consumers. Today `_niche_detail_response` calls `list_team_peers` then
  `peer_update_status` separately — combine so the peer card has
  everything it needs in one dataclass.

Micro tests:

- hint is True when `current > watermark`
- hint is False when `current == watermark`
- hint is False when peer has never bumped (both zero)

### 3. Peer-panel fragment endpoint (`shared_file_vault/web.py`)

- New route: `GET /teams/{team}/niches/{niche}/peer_panel`. Returns
  the peer-panel HTML fragment only (no surrounding niche detail).
- Factor the peer-panel rendering out of `_niche_detail_response` into a
  helper so both full-page and fragment responses use it.
- Add `hx-get` + `hx-trigger="every 20s"` to the peer-panel container in
  `niche_detail.html`. Pause polling when the page is hidden if htmx has
  a straightforward way (`hx-trigger="every 20s[document.visibilityState === 'visible']"`).

Micro tests (pytest + FastAPI TestClient):

- fragment endpoint returns peer-panel HTML with expected teammate labels
- fragment respects team-session-none vs team-session-active states

### 4. UI copy and styling (`templates/fragments/niche_detail.html`)

- Add a small "has-changes" badge next to `peer.label` when
  `peer.update_status.has_unfetched_hint` is true and
  `peer.update_status.ready_to_merge` is false (so we don't double-show
  pre-fetch and post-fetch hints).
- Copy: "Has changes since your last fetch" on hover/title, badge shows a
  dot or "●".
- Add a small explanatory line under the peer panel noting the hint is
  team-scoped and approximate.
- Keep the existing "Changes available (sha)" and "already merged"
  messages unchanged — those are the post-fetch states.

### 5. Integration test

In `tests/test_web_sync.py` (or a new `test_peer_update_ux.py`):

- Two vaults sharing a team session fixture.
- Peer B pushes through the Hub.
- Peer A fetches the peer-panel fragment; assert the "has changes" badge
  is present for B.
- Peer A clicks "Check For Updates" (fetch); assert the badge disappears
  on the next fragment fetch.
- Peer B pushes again; badge reappears.

## Follow-up issues to file before merging

1. **Per-niche attribution of unfetched updates.** Replace the
   team-scoped hint with a registry-diff step: after observing a signal
   bump, fetch the peer's registry, diff niche tip SHAs against
   local parked/merged state, surface hints per `(peer, niche)`.
2. **SSE/long-poll refresh via `/notifications/watch`.** Add an SSE
   endpoint on the Vault web app that wraps
   `SmallSeaSession.watch_notifications` and emits the peer-panel
   fragment on each bump. Keep htmx polling as fallback.

Both follow-ups are additive — they don't invalidate the v1 shape.

## Validation — convincing a skeptic

### "Did the branch accomplish its goals?"

- Goal 1 (unfetched hint): demonstrated by the watermark micro tests plus
  the integration test where a peer's push flips the badge on.
- Goal 2 (state-reminder UX): visible in the rendered fragment; copy and
  visual weight explicitly match the state-reminder intent. No dismiss
  affordance exists.
- Goal 3 (background refresh): the polling fragment endpoint is covered
  by a TestClient test. Manual smoke via `shared-file-vault serve` with
  two vaults confirms the badge appears without a page reload.

### "Did repo integrity hold up?"

- Coupling: changes are confined to `shared_file_vault/sync.py`,
  `web.py`, and one template. No new Vault → team-DB reads. No new Hub
  endpoints. No cod-sync changes.
- Architectural mandates:
  - Hub remains the sole gateway — the new hint is derived from
    existing `GET /session/peers` data.
  - Only the Manager reads team DB directly — unchanged.
- Backward compatibility: vault config gains an optional table;
  pre-existing configs without it default to watermark 0 and behave
  identically to current code until the first fetch.
- Tests: existing `test_hub_sync.py` / `test_web_sync.py` continue to
  pass; new tests cover the added surface.

### "What did I deliberately not do, and why?"

- No per-niche resolution — filed as follow-up; documented trade-off.
- No push-based refresh — filed as follow-up; polling is the honest
  stepping stone.
- No dismiss button — the UX framing makes it redundant.

## Out-of-scope surface untouched

- `packages/cod-sync/*` — no protocol changes.
- `packages/small-sea-hub/*` — no new endpoints.
- `packages/small-sea-client/*` — no new wrappers (`session_peers` and
  `watch_notifications` already exist).
- `packages/small-sea-manager/*` — no changes.

## Wrap-up

On merge:

1. File the two follow-up issues on GitHub, linking back to this branch
   plan.
2. Update `branch-plan.md` with the "What landed" section mirroring the
   format of `Archive/branch-plan-vault-building-blocks.md`.
3. Move `branch-plan.md` to `Archive/branch-plan-issue-13-vault-peer-update-ux.md`.
