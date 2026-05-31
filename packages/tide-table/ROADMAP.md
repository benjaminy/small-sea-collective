# Tide Table Roadmap

**Status:** broad plan, not a commitment.
Each phase exists to retire a specific unknown; later phases will sharpen as earlier ones teach us things.

## Why This Sequence

The interesting risks in Tide Table are not in the Small Sea half of the system.
Storage in a berth, sync through the Hub, session authorization — those use the same machinery every other Small Sea app uses, and the patterns are known.

The risks are in the calendar half.
CalDAV is a large, old standard; clients implement it inconsistently; recurring events are notoriously where calendar projects die; and the macOS/iOS path for adding third-party CalDAV accounts has gotten worse, not better, in recent years.
Most ways this project could quietly fail are not "the protocol is unsound" but "we built a faithful CalDAV server and a non-technical person cannot actually use it from their calendar app."

So the roadmap is sequenced to confront the scariest unknowns earliest, with cheap experiments before commitments.
Each phase ends with a tighter or revised plan for the next one rather than a fixed multi-phase plan worked out up front.

## Known Unknowns

Roughly ordered by how badly they could break the project if they turn out wrong.

1. **Real CalDAV clients are quirky.**
   Apple Calendar, Thunderbird, GNOME Calendar, and Outlook implement CalDAV inconsistently.
   ETag handling, sync tokens, CTag invalidation, REPORT queries, and recurrence behavior all vary.
   We do not know which client behaviors will accept the kind of server we want to build until we try, and the answer may differ across clients in ways that force adapter-specific code.
2. **Setup UX may be the actual constraint.**
   Even a perfect adapter is worthless if a non-technical user cannot finish the "add account" flow.
   Apple in particular has buried third-party CalDAV under several Settings menus on macOS and iOS.
   The provisioning story (signed configuration profiles, deep links, QR pairing, manual entry as a floor) may need as much engineering attention as the sync engine.
3. **Local authentication is harder than localhost-only suggests.**
   Localhost binding does not prevent unrelated processes on the same machine from reading team calendars.
   We need a per-client token model that fits into how calendar apps actually store credentials (system keychain, account dialogs, etc.) and that can be revoked when a client is uninstalled.
4. **Concurrent edits to recurring events are where calendar prototypes die.**
   `RRULE` + `EXDATE` + `RECURRENCE-ID` produces edge cases that lose data under naive merge, and multi-device concurrent edits sharpen this further.
   We will not know our merge model is adequate until we run it under real concurrent editing patterns.
5. **External invitations have an ambiguous v1 answer.**
   Teams will eventually want to invite people outside Small Sea — to a birthday party, a parent-teacher conference, a neighborhood meeting.
   A minimum credible answer ("export `.ics`, email it yourself") needs to exist somewhere on the roadmap; a maximum answer (full iTIP RSVP/attendee workflow) is probably permanently out of scope.
   The middle is unclear.

The phases below try to expose each of these to a cheap test before anything downstream relies on the answer.

## Phase 0 — Spikes

Timeboxed, throwaway code.
The goal is to answer "will any of this work at all" before building real infrastructure.

- Stand up a trivial CalDAV server on localhost (or point at Radicale as a known-good reference) and add it as an account in Apple Calendar, Thunderbird, and GNOME Calendar.
  Note what works, what breaks, and where the user-visible friction sits.
- Probe how each client behaves under deliberately quirky server responses: stale ETags, missing CTags, sync-token churn, partial multistatus responses.
  These observations shape the server we eventually build.
- Survey low-friction setup paths: signed `.mobileconfig` profiles on macOS/iOS, custom-scheme deep links, QR pairing, manual entry as the floor.
  Output is a ranked list, not a chosen mechanism.

What we keep at the end: notes, not code.
The decision the spikes inform is whether the rest of the roadmap is buildable in the shape currently imagined or needs reshaping.

## Phase 1 — Read-only path from a static store

Prove the end-to-end pipeline before introducing writes or sync.

- A real Tide Table process serving CalDAV on localhost, backed by a hand-curated `.ics`-shaped store inside a Small Sea berth.
- One team, one calendar, read-only.
- Per-client bearer tokens for authentication; the simplest provisioning flow we can build, even if it is rough.

This phase retires unknown 1 (basic CalDAV compatibility against real clients), unknown 2 in a first form (the user can actually finish account setup), and unknown 3 (per-client token model exists).

Deliberately out of scope for this phase: writes, recurrence editing, conflict handling, multiple calendars, time-zone correctness beyond passing through what is already in the store.

## Phase 2 — Writes from a single device

Round-trip `PUT` / `DELETE` from a calendar client through Tide Table into Small Sea storage, on one device only.

- Commit to a storage shape (one `VEVENT` per file? content-addressed blob keyed by UID? a journal of changes?) — the question Open Design Questions in the original README deferred, now forced by the need to write.
- ETag and sync-token discipline good enough that real clients do not enter a perpetual re-sync loop.
- Pass-through recurrence: standards-compliant `RRULE`/`EXDATE`/`RECURRENCE-ID` survives a round-trip even though we are not yet editing occurrences in interesting ways.

This phase retires the single-writer half of unknown 4 and forces the storage-shape decision.
Out of scope: multi-device sync, concurrent edits, occurrence-level recurrence editing.

## Phase 3 — Multi-device sync

The first version that looks like the actual product: two teammates editing the same calendar from their own devices.

- Sync through the Hub using the team berth.
- Conflict surfacing at the event (UID) level: a conservative policy (likely last-writer-wins with a visible record of what was overwritten) rather than a silent merge.
- Tooling to inspect the conflict log from inside a calendar app — perhaps a dedicated "conflicts" calendar overlay so users see overwritten edits in the same surface where they live.

This phase retires the second, harder half of unknown 4 for non-recurring events.
It is deliberately separated from Phase 4 because the merge model for plain events and the merge model for recurring series need different evidence and should not be tangled in the same iteration.

## Phase 4 — Recurring event hardening

The dragon, attacked only after multi-device merge for simple events has settled.

- Concurrent edits to "this occurrence," "this and future occurrences," and "the entire series" from different devices.
- A documented loss policy for edge cases the standard does not resolve cleanly; the goal is honest semantics, not perfect ones.
- Conflict surfacing that distinguishes "the series moved" from "one occurrence was edited" so the user can reason about what just changed.

This phase retires the remaining half of unknown 4.

## Phase 5 — Real-world polish

Once the core is honest about its semantics, the surrounding pieces matter.

- Multiple calendars per team (work events, social events, deadlines, etc.) exposed as separate CalDAV collections under one Small Sea team.
- The provisioning flow upgraded from "rough" to "easy enough to demo to a non-technical friend without preparing them."
- Export-to-`.ics` as a partial answer to unknown 5: a team member can hand a single event or a curated subset to someone outside Small Sea over ordinary email.
- Notifications and reminders posture clarified (current leaning: let the calendar client handle reminders; Tide Table does not push, because Small Sea has its own notification rails and double-notifying users is worse than no notifications).

## Indefinitely Deferred

Not on the roadmap as currently imagined; would require a new motivating need to revisit.

- A full iTIP attendee workflow (RSVP, attendee delegation, free/busy responses across organizations).
- Free/busy lookup across unrelated teams.
- Public calendar publishing on the open internet.
- A Tide Table-native calendar UI replacing Apple Calendar, Outlook, Thunderbird, or similar.
  The whole architecture is built around *not* doing this; revisiting it would be a redesign, not a phase.

## How This Roadmap Changes

Each phase produces information that should reshape the next.
If Phase 0 reveals that one major client cannot tolerate the kind of server we want to build, Phase 1 may not look the same.
If Phase 2's storage decision turns out to make Phase 3 conflicts intractable, Phase 2 may need to be revisited before continuing.

The intent is to never have more than one phase's worth of detailed planning open at a time.
Long-range commitments past the next phase are deliberately vague, and that vagueness is a feature, not an omission.
