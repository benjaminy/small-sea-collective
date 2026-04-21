# Branch Plan: Prompt Device-Link Visibility

**Branch:** `issue-59-device-link-visibility`
**Base:** `main`
**Primary issue:** #59 "Make linked devices first-class for sender keys and peer routing"
**Related issues (context):** #97 (trust-domain reframe, closed), #98 (admin-quorum admission, merged), #69 (same-member bootstrap), #43 (sender-key rotation), #73 (hygiene rotation)
**Related archived plans:**
`Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-issue-59-peer-routing-watches.md`,
`Archive/branch-plan-issue-59-peer-device-model.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`,
`Archive/branch-plan-issue-43-sender-key-rotation.md`

## Reevaluation of #59 After Recent Work

Issue #59 has been re-scoped twice by the author and has already had three
implementation slices land. Before planning more work we have to be honest about
what #59 still owns today, because most of its original surface is no longer its
problem.

### What already landed under #59

1. **Sender-device runtime identity**
   (`Archive/branch-plan-issue-59-sender-device-runtime-identity.md`). Sender-key
   streams are now named by `sender_device_key_id = key_id_from_public(team-device public key)`
   across `cuttlefish.group`, device-local sender-key tables, Hub crypto, and
   provisioning. Two sender-key streams from two linked devices of one member can
   coexist on a recipient device.
2. **Device-aware runtime reconciliation and watch-triggered redistribution**
   (`Archive/branch-plan-issue-59-peer-routing-watches.md`). Manager owns
   `reconcile_runtime_state(...)`; the Hub watcher triggers it on local team-DB
   change; adopted-removal triggers rotation + redistribution on non-removing
   devices; same-member sibling devices are real fanout targets; delivery dedupe
   state is device-local.
3. **Shared device-model schema**
   (`Archive/branch-plan-issue-59-peer-device-model.md`). `peer` is gone; fresh
   team DBs carry `member` + `team_device`. Endpoint lookup is now per
   `team_device` row. Create-team, invitation, linked-device bootstrap, and
   member removal all maintain `team_device` with FK cascade cleanup.

Related unblockers that also landed or closed:

- **#97 trust-domain reframe** (closed). Sibling-linked devices are readable by
  sibling handoff; admission-time confidentiality against the sibling admitter
  is not a real property of the protocol.
- **#98 admin-quorum admission** (merged). Teammate admission is now a
  transcript-bound quorum flow, not a sender-key redistribution trigger.
- **#69 same-member linked-device encrypted-team bootstrap** (merged).

### What #59 no longer owns

The first two comments on #59 described an older shape where "new linked device
appears" forced sender-by-sender redistribution. The trust-domain reframe
deleted that requirement. Per the current issue body:

> treating "new linked device appeared" as an automatic requirement that every
> sender redistribute immediately

is explicitly **Out of Scope**. So is same-member bootstrap mechanics (#69) and
admin-quorum teammate admission (#98).

Periodic/hygiene rotation policy is #73, not here.

### What the current #59 body actually still asks for

Reading the current issue body and its third comment carefully, the remaining
scope is narrow:

- device-scoped sender-key runtime identity — **done** (slice 1).
- device-aware peer routing and endpoint handling — **largely done** (slices 2
  and 3); `team_device` is the shared endpoint owner and Hub reconciliation is
  device-keyed. The archived peer-routing-watches plan explicitly flagged as
  intentional limit that **Hub caller APIs remain member-keyed even though
  endpoint resolution now goes through `team_device`**.
- **watch behavior that surfaces newly visible linked devices promptly** — this
  is the one bullet the current issue body emphasizes as *mattering more, not
  less* after #97. The author's stated reason is that if admission-time
  confidentiality against a sibling admitter is not a real property, prompt
  observability is the user-facing protection.
- runtime behavior for multiple linked devices of one teammate — done in crypto
  and reconciliation paths.

So the unique remaining deliverable #59 still owns is the **observability
slice**: a teammate whose peer's device just linked another sibling should see
that event promptly, as an event their own device observed and surfaced, not
only as a stale row visible the next time they open the Manager UI.

Today that event already exists in `admission_events._linked_device_events`
and is rendered in the Manager's `admission_events.html` fragment with an HTMX
poll (`admission_events_watch.html`, `hx-trigger="load delay:{watch_delay}"`).
But:

- it surfaces only to users who happen to have the Manager web UI open,
- it does not fire an OS-level notification through the Hub's notification
  adapters (`ntfy`, `gotify`) the way other runtime events can,
- the Hub's team-DB watch loop does not treat the appearance of a new
  `device_link` cert for a teammate as a first-class user-facing signal. The
  watch loop currently cares about new `device_prekey_bundle` rows and trusted
  peer-device set changes for reconciliation, but the *human-visible* side
  still depends on polling.

## Proposed Goal

After this branch lands:

1. When a device adopts a team-DB view in which a new `device_link` cert has
   appeared for a **teammate** (not self), the Hub's runtime watch loop
   recognizes that as a user-facing admission event and pushes an OS-level
   notification through the Hub's existing notification adapters, in addition
   to the existing `admission_events` Manager-UI card.
2. Self-linked new devices remain visible in the Manager UI but do **not**
   trigger an OS notification by default (the user presumably just linked their
   own device; this is a different UX).
3. The Hub notification carries enough context for the user to open the
   Manager UI's admission-events card and decide whether to exclude or ignore,
   but does not try to embed approval/exclude actions in the notification
   payload itself.
4. Notifications are deduped so adopting the same cert twice, or restarting
   the Hub, does not re-page the user.
5. Specs and micro tests describe the new observer-side flow honestly.

## Why This Slice, Why Now

This is the smallest branch that closes the user-facing side of the #97
reframe. The reframe accepted that a sibling admitter can in principle share
plaintext or receiver state; the user-facing compensation is **prompt social
visibility**. Right now that compensation is only partial, because visibility
depends on the user opening a web UI. Finishing the observability slice is
what makes #59 honestly closable.

It deliberately does **not**:

- change shared schema,
- change sender-key crypto or rotation behavior,
- change reconciliation or redistribution,
- re-open #69 / #98 concerns.

## Scope Decisions

### S1. Event selection is observer-centric

The notification fires only for `device_link` certs adopted locally that
describe a **teammate's** new device, i.e. `team_device.member_id != self_member_id`.
A `device_link` cert the local device itself just issued or just joined
through should not fire an OS notification, even though it still appears in
the Manager-UI event list.

`self_member_id` here is the **observing device's member identity**, not the
issuer of the cert. So if two of Alice's devices both observe Alice linking a
third Alice device, neither observing device pages. That is intentional: this
slice is about "another teammate linked a new sibling device" visibility.

**Why:** the reframe's threat model is "sibling admits a new device the other
teammates did not expect." Self-linking is a deliberate user action in the same
user-agent session; paging them for it is noise.

### S2. Hub owns notification delivery; Manager owns the event list

`admission_events.list_admission_events(...)` stays the sole enumerator of
user-facing admission events. The Hub's watch loop calls into that (or a narrow
sibling helper) when it detects an adopted change relevant to admissions, and
hands new-to-this-device events to the notification adapters.

**Why:** keeps Manager as the sole reader of the admission event model and
avoids duplicating event taxonomy inside the Hub.

### S3. Dedupe is device-local and durable

"Already notified" state is kept device-local in the existing Manager-owned
admission-event disposition store, not in synced team state and not in a new
Hub-local table. Restarting the Hub must not re-page the user for events
already seen.

**Why:** dedupe state is not useful to other devices and must not depend on
synced mutable state.

### S4. Dismissal parity with the UI "Ignore" action

If a user clicks "Ignore" on an admission-event card, that dismissal should
also mark the notification-side dedupe so we do not re-page on the next watch
tick. Conversely, receiving the notification does not auto-dismiss the card.

If a dismissal races with an in-flight publish attempt, one final notification
is acceptable; the dismissal must suppress later ticks.

**Why:** the card is the place to take action (exclude, etc.); the notification
is the prompt. One dismissal affordance, two surfaces.

### S5. No new admission event types

This branch only lights up push delivery for the `LINKED_DEVICE` event type
that already exists in `AdmissionEventType`. Invitation-related event types
(proposal shell, awaiting quorum, finalized) are out of scope for this slice
even though the same infrastructure could eventually light them up.

**Why:** those are #98's surface; the #59 reframe specifically names the
linked-device case as the user-facing protection.

## In Scope

### 1. Hub watch hook into admission events

Extend the Hub's existing runtime watch path (same path that triggers
`reconcile_runtime_state`) so that after a local adopted-view change it also
enumerates admission events for the current team and identifies the subset
that:

- are `LINKED_DEVICE` type,
- describe a teammate's new device (not self),
- have not been notified for before on this device.

### 2. Notification payload

Compose a small, human-readable notification per new linked-device event
using the existing `AdmissionEvent.title` / `badge_label` fields. Route
through whichever notification adapter the Hub is configured to use
(`ntfy` / `gotify` / OS notify). Content should at minimum include:

- team name,
- teammate display name (or a short hex fallback),
- the fact that a new device was linked,
- enough pointer information (team name + event type) to let the Manager UI
  scroll to the card.

### 3. Device-local dedupe seam

Add a small device-local persistence keyed by `(team_id, event_type, artifact_id_hex)`
that records "notified at". The watch pass filters this out before dispatch.
Wire the Manager's existing "dismiss" path so a dismissal records the same
row (or treats it as equivalent) to avoid re-paging after the user has
already acted.

### 4. Self-vs-teammate discrimination

Thread the caller's `self_member_id_hex` into the Hub-side enumeration so the
filter in S1 is applied before any notification is dispatched. Reuse
`admission_events` logic rather than recomputing.

### 5. Specs and micro tests

Update `packages/small-sea-hub/spec.md` and
`packages/small-sea-manager/spec.md` to describe the observer-side flow and
the division of responsibility in S2.

Minimum micro-test coverage:

- adopting a `device_link` cert for a teammate fires exactly one notification
  dispatch,
- adopting the same cert twice (e.g. restart, or re-pull with no new rows)
  does not re-dispatch,
- self-linked `device_link` does not dispatch a notification,
- dismissing via the Manager UI path prevents a later notification for that
  same artifact,
- a teammate admission cert does not cross-trigger notifications under other
  admission event types,
- a `device_link` for a teammate whose `team_device.member_id` matches a
  currently-excluded / removed member is handled without crashing (benign
  no-op is fine).

## Out Of Scope

- New sender-key crypto or rotation primitives (#43, #73).
- Automatic sender-key redistribution triggered by a new sibling device
  appearing (explicitly out per the current #59 body).
- Same-member bootstrap mechanics (#69).
- Admin-quorum teammate admission (#98).
- Invitation proposal / quorum event types driving notifications.
- Full shared peer-table / member-schema redesign (already handled).
- Revocation certificates or cryptographic device-removal semantics.
- In-notification action buttons (approve / exclude from the OS-level prompt).

## Concrete Change Areas

### 1. `packages/small-sea-manager/small_sea_manager/admission_events.py`

- keep `list_admission_events(...)` as the UI-facing aggregator,
- add a **Manager-owned, notification-specific helper** that returns only the
  linked-device events eligible for push delivery, already filtered by
  `self_member_id_hex` and by device-local disposition state,
- make that helper return notification-ready metadata (`title`, `summary`,
  `member_id_hex`, `artifact_id_hex`, `occurred_at`) so the Hub does not need
  to learn admission-event SQL, event taxonomy rules, or dismissal semantics.

### 2. `packages/small-sea-manager/small_sea_manager/provisioning.py`

- extend the existing device-local `admission_event_disposition` store rather
  than creating a parallel dedupe store,
- expand disposition vocabulary from only `dismissed` to at least
  `dismissed` and `notified`, preserving device-local scope,
- widen the primary key to `(event_type, artifact_id, disposition)` so
  `dismissed` and `notified` can coexist as independent facts for one event,
- add narrow helpers such as "list notified", "mark notified", and "dismiss"
  so callers do not manipulate the disposition table ad hoc.

### 3. `packages/small-sea-hub/small_sea_hub/server.py` (and possibly `backend.py`)

- extend the existing adopted-view watch path immediately after
  `reconcile_runtime_state(...)`,
- reuse the watcher's existing per-session / per-team iteration rather than
  inventing a second enumeration loop,
- ask the Manager-owned helper for newly visible linked-device notification
  candidates,
- dispatch each candidate through the existing notification adapter plumbing,
- record successful dispatch back through the Manager-owned disposition helper,
- keep Hub responsibility limited to watcher timing, adapter delivery, and
  best-effort error handling.

### 4. Specs

- `packages/small-sea-hub/spec.md`: document that the watcher now has two
  independent side effects after adopted-view change:
  reconciliation for crypto/runtime artifacts, and push delivery for
  newly-visible teammate linked-device events.
- `packages/small-sea-manager/spec.md`: document that Manager remains the owner
  of admission-event interpretation, filtering, and device-local disposition
  state, even when the Hub triggers notification delivery.

### 5. Tests

- add focused Hub micro tests for watch-triggered notification dispatch and
  non-redispatch,
- add focused Manager micro tests for linked-device candidate enumeration and
  disposition transitions,
- prefer fake/in-memory notification adapters in micro tests rather than
  Darwin- or service-specific behavior.

## Implementation Strategy

### Phase 1. Stabilize the seam

Create the smallest Manager-owned helper that can answer:
"For this participant and team, which linked-device admission events are
eligible for notification right now?"

That helper should encapsulate:

- linked-device event selection,
- self-vs-teammate filtering,
- dismissal/notified suppression,
- payload text derivation.

The Hub should only consume the helper result and should not carry any new SQL
or event-type branching beyond "send these results via the configured adapter."
This is an in-process Python call, not a new HTTP seam.

### Phase 2. Reuse one local disposition model

Do not introduce a second dedupe database. This branch is about observability,
not storage-model expansion. Reusing `admission_event_disposition` keeps the
behavior auditable:

- `dismissed` means "hide from the UI and suppress notification",
- `notified` means "notification already sent; keep card visible",
- both remain local to one device clone.

The storage shape is decided now: record dispositions as independent rows keyed
by `(event_type, artifact_id, disposition)`. Suppression and dedupe checks are
simple existence queries over those rows.

Initial backlog semantics are also decided now: when the upgraded disposition
store is first initialized on a device, seed `notified` rows for every
currently-existing `LINKED_DEVICE` event visible in that local team view, and
persist a migration timestamp. Only linked-device certs whose effective event
time is strictly later than that migration timestamp are eligible for first-time
push delivery. This avoids paging users for ancient history the first time the
branch runs on an established clone.

### Phase 3. Hook the watcher without widening scope

The adopted-view watcher already notices relevant local team-DB changes and
already has the notification adapters wired. The branch should only insert the
new observer-side step into that existing loop. It should not:

- add a new background worker,
- add a second polling mechanism,
- invent new admission event types,
- change sender-key redistribution behavior.

Delivery semantics are also decided here:

- if no notification adapter is configured, skip dispatch, record nothing, and
  leave the event eligible for later ticks,
- only a publish that returns success records `notified`,
- publish failure or exception records nothing and retries naturally on the
  next watcher tick,
- no explicit retry queue or backoff is added in this slice.

## Validation

This branch should be considered complete only if it supplies evidence for both
behavioral correctness and repo integrity.

### A. Behavioral proof

These should be demonstrated by micro tests, not just by reasoning:

1. adopting a teammate `device_link` cert causes exactly one push dispatch on
   the observing device,
2. adopting the same cert again does not cause a second dispatch,
3. restarting the Hub after a successful dispatch does not cause a second
   dispatch,
4. self-linked-device events remain visible in the Manager UI but do not
   dispatch,
5. dismissing the event before dispatch suppresses dispatch,
6. dispatching the notification does not auto-dismiss the UI card,
7. a publish failure or missing adapter leaves the event eligible so a later
   successful tick can still dispatch it,
8. first-run backlog seeding suppresses historical linked-device events while
   still allowing newly-issued ones after the migration point,
9. non-`LINKED_DEVICE` admission events do not ride along accidentally,
10. a benignly orphaned or excluded-member-linked `device_link` row is ignored
    without crashing.

### B. Architectural proof

The branch should also make a skeptical reviewer comfortable that the repo got
cleaner, not just different. Review should be able to confirm all of:

1. the Hub still owns delivery mechanics only; it does not gain new
   admission-event SQL or event-taxonomy knowledge,
2. the Manager remains the only package defining how admission events are
   discovered, filtered, titled, and locally suppressed,
3. notification dedupe remains device-local and unsynced,
4. no network path bypasses the Hub,
5. no sender-key rotation, redistribution policy, or teammate-admission logic
   changes as collateral damage,
6. the UI rendering path still goes through `list_admission_events(...)` and
   preserves existing behavior except where the new disposition semantics are
   intentionally shared,
7. the watcher still uses the existing adopted-view loop rather than a second
   parallel team-enumeration mechanism.

### C. Human proof

In addition to tests, the branch should leave behind a short "how to convince
yourself" manual check path that works with the repo's actual local harness:

1. start from a local playground with two participant roots / Managers sharing
   one team,
2. observe one push notification on the watcher device,
3. reload the Manager UI and confirm the linked-device card is still present,
4. dismiss the card,
5. re-run the watcher path or restart the Hub and confirm no repeat page,
6. repeat with a self-linked device and confirm card-only behavior.

## Risks to Avoid

- Letting the Hub grow new admission-event SQL or duplicated event-taxonomy
  logic. It may call Manager-owned helpers; it should not become a second
  admission-events implementation.
- Accidentally using synced team-DB state to store notification dedupe.
  Disposition state must remain device-local.
- Re-introducing member-scoped conflation when determining whether an event is
  "self" or "teammate." The whole point of this branch is device visibility.
- Auto-dismissing the UI card when a notification is sent. Notification and UI
  prompt are related, but not the same user action.
- Making notification payloads carry approval/exclusion controls rather than
  acting as a pointer back to Manager.
- Sliding into #73-style hygiene rotation, #43 redistribution policy, or #98
  invitation/quorum work.

## Open Questions

### Q1. Batching

If many eligible linked-device events appear in one watcher pass, should the
Hub emit one notification per event or a capped summary? Preference: start with
one per event unless tests or manual validation show unacceptable noise.
