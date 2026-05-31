# Tide Table

**Status:** concept-stage app.
This package currently contains only enough metadata to be a valid workspace member; it does not implement the app yet.

Tide Table is a Small Sea team calendar and scheduling app.
The name leans on the useful double meaning: a tide table is already a schedule, and Small Sea teams need a shared rhythm that belongs to them.

The likely first product shape is not a new calendar UI.
It is a local CalDAV-compatible adapter backed by Small Sea.
Existing calendar apps would talk to Tide Table over localhost, while Tide Table stores and syncs team calendar state through the normal Small Sea app model.

```text
Calendar app
    ↓ CalDAV over localhost
Tide Table
    ↓ Small Sea client session
Local app-owned calendar state
    ↓ Cod Sync through the Hub
Team berth
```

## Purpose

Calendars are one of the places where small groups get value from shared infrastructure immediately.
Families, volunteer groups, studios, classrooms, clubs, and tiny companies often need a shared view of what is happening more than they need a bespoke workflow app.

Tide Table should let those groups keep that shared schedule inside Small Sea without asking everyone to abandon the calendar apps they already use.
The Small Sea part should be ownership, authorization, local-first storage, and synchronization.
The calendar-app ecosystem should continue doing the heavy interface work: day views, week views, notifications, reminders, time zones, and platform integration.

## Architectural Fit

Tide Table is a Small Sea app, not a Hub feature.
It requests an authorized Hub session for a Tide Table berth and owns its local materialized calendar state.

The local CalDAV surface is a client-facing adapter.
It should listen only on localhost by default and use local credentials or tokens so unrelated software on the same device cannot silently read team calendars.

All Small Sea internet traffic still goes through the Hub.
Tide Table must not talk directly to cloud storage, notification services, peer devices, or provider APIs.
It also must not read or write Manager-owned Core databases directly.
Team identity, session, and berth information should come from Hub session APIs.

## Early Scope

The first useful version should be small and boring in the best way:

- expose one or more team calendars through a local CalDAV account
- create, edit, and delete events from ordinary calendar clients
- preserve recurring events and time zone data
- sync calendar changes through the Hub using the team berth
- surface conflicts conservatively instead of silently choosing a winner

Deliberately out of scope for the first version:

- external email invitations
- global free/busy lookup across unrelated teams
- public calendar publishing
- replacing native calendar applications
- real-time scheduling guarantees

## Open Design Questions

The main technical question is the storage and merge model.
Plain `.ics` files are attractive because they match the ecosystem, but event-level merge behavior probably needs more structure than line-based text merging can provide.

Recurring events are the second sharp edge.
Tide Table should preserve standards-compliant recurrence data from clients, but edits to one occurrence, all future occurrences, or an entire series need careful conflict semantics.

Scheduling invitations are the third sharp edge.
CalDAV clients may expect iTIP-style attendee workflows, but Small Sea teams may initially get more value from shared team calendars than from full email-shaped invitation machinery.

## Product Feel

Tide Table should feel practical, calm, and quietly dependable.
Its promise is not "a smarter calendar."
Its promise is "our shared schedule belongs to us, and it still works with the calendar tools we already know."
