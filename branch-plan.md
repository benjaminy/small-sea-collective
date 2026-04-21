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

"Already notified" state is kept device-local (note-to-self or a Hub-local
table). Key is `(team, event_type, artifact_id)` — the same key
`admission_events.html` dismisses on. Restarting the Hub must not re-page the
user for events already seen.

**Why:** dedupe state is not useful to other devices and must not depend on
synced mutable state.

### S4. Dismissal parity with the UI "Ignore" action

If a user clicks "Ignore" on an admission-event card, that dismissal should
also mark the notification-side dedupe so we do not re-page on the next watch
tick. Conversely, receiving the notification does not auto-dismiss the card.

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

### 1. `packages/small-sea-hub/small_sea_hub/server.py` (and/or `backend.py`)

- extend the adopted-view watch handler to enumerate admission events after
  reconciliation and dispatch new `LINKED_DEVICE` items through the
  configured notification adapter,
- take care to share rather than duplicate the existing `ntfy` / `gotify`
  adapter plumbing.

### 2. `packages/small-sea-manager/small_sea_manager/admission_events.py`

- expose a narrow helper the Hub can call to list events for a team,
  parameterized by `self_member_id_hex`, filtered to notification-relevant
  items. Keep `list_admission_events` unchanged in behavior for the UI path.

### 3. `packages/small-sea-manager/small_sea_manager/provisioning.py`

- add a small device-local dedupe table (or reuse the existing
  admission-event dismissal table) keyed by
  `(team_id, event_type, artifact_id_hex)` with a `notified_at` column
  distinct from `dismissed_at` where useful.

### 4. Specs

- `packages/small-sea-hub/spec.md`: add the observer-side watch → notification
  seam.
- `packages/small-sea-manager/spec.md`: describe the shared event enumeration
  and dedupe row.

### 5. Tests

- new Hub micro test covering items 1–6 in §In Scope 5,
- small Manager test for the dedupe-row wiring if dismissal is the same row.

## Validation

This branch should convince a skeptical reviewer if all of the following are
true after it lands:

- adopting a `device_link` cert for a teammate produces exactly one OS-level
  notification on the adopting device, covered by a micro test,
- self-linked `device_link` does not produce an OS-level notification, covered
  by a micro test,
- re-pulling the same cert, or restarting the Hub, does not re-notify, covered
  by a micro test,
- dismissal and notification are consistent: dismissing the UI card before the
  notification fires suppresses the notification, covered by a micro test,
- no new sender-key crypto, no new rotation policy, no automatic sender-by-
  sender redistribution on sibling link — review confirms scope was held,
- the Manager UI still renders admission events exactly as before,
- specs describe the observer flow clearly enough that a new reader can locate
  which component owns which responsibility.

## Risks to Avoid

- Letting the Hub start reading the admission-event taxonomy directly (keep
  it going through `admission_events`).
- Accidentally using synced team-DB state to store notification dedupe
  (must stay device-local).
- Re-introducing member-scoped conflation in the watch path when identifying
  "teammate vs self."
- Making the notification payload actionable in ways that duplicate the
  Manager UI's affordances — keep it a pointer, not a control surface.
- Sliding into #73-style periodic rotation or #43 redistribution policy.

## Open Questions

### Q1. Reuse dismissal table or add a sibling row?

The dismissal table already exists and is keyed by
`(event_type, artifact_id)`. Easiest path is to add a nullable `notified_at`
column to the same row and treat `dismissed_at IS NOT NULL OR notified_at IS NOT NULL`
as "do not re-notify." Alternative is a separate table. Preference: extend
the existing table, but the implementation branch should confirm column
evolution is acceptable for the current schema version policy.

### Q2. Which notification adapter is the default target?

The Hub already has `ntfy.py` and `gotify.py` adapters. The branch should
confirm which is wired up in the current default Hub config path and whether
a local fallback (e.g. `osascript` on Darwin for dev) is worth adding for
micro-test ergonomics, or whether tests should inject a fake adapter.
Preference: inject a fake adapter in tests, do not add a Darwin-only path.

### Q3. Scope of "teammate" for the filter

`team_device.member_id != self_member_id` is the obvious filter. Edge case:
a `device_link` cert for a removed / excluded member. The schema FK cascade
should delete the `team_device` row on member removal, so the join will drop
the row; behavior is a benign no-op. Worth a regression-style micro test but
not a design change.

### Q4. Batching

If many linked-device events land in one adoption (unlikely in real use but
possible for a long-offline device), should we collapse to one notification
with a count, or emit N? Preference: emit up to a small cap (e.g. 3)
individually, then one summary if more — but this can start as "emit N" and
tighten later if noisy.
