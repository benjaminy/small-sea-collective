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

## What Tide Table Is Not

These are enduring positions, not first-version omissions.
The version-scoped sequencing and the open design questions about storage, merge semantics, recurring events, and external invitations live in `ROADMAP.md`.

- **Not a new native calendar UI.**
  Tide Table never replaces Apple Calendar, Outlook, Thunderbird, or the like.
  Day views, week views, drag-and-drop scheduling, and platform-native reminders are the calendar app ecosystem's job, not ours.
  The whole architecture is built around inheriting those decades of polish rather than reinventing them.
- **Not a public calendar publishing service.**
  Calendars live inside Small Sea teams.
  There is no "publish this team's calendar to the open internet" surface.
- **Not a free/busy or directory service across unrelated teams.**
  A query like "when is anyone in any of my teams free next Tuesday" is out of scope.
  Each team's calendar is its own world.
- **Not a generic CalDAV server for arbitrary clients on arbitrary networks.**
  The CalDAV surface is a localhost adapter for the device's own calendar apps.
  It is not a network-reachable CalDAV server, an iCloud replacement, or a multi-tenant hosted service.
- **Not a real-time scheduling guarantee.**
  Events sync through the same Small Sea transport every other app uses.
  Eventual consistency, not real-time co-editing.

## Product Feel

Tide Table should feel practical, calm, and quietly dependable.
Its promise is not "a smarter calendar."
Its promise is "our shared schedule belongs to us, and it still works with the calendar tools we already know."

## Roadmap

The phased plan — and the unknowns that drive its sequence — lives in `ROADMAP.md`.
The roadmap is organized to confront the calendar ecosystem's scar tissue (client quirks, setup UX, recurrence merge semantics) as early and cheaply as possible, with throwaway spikes ahead of any real architectural commitments.

## Backlog Notes

When Tide Table comes back off the shelf, start with a throwaway CalDAV and account-setup spike rather than storage architecture.
Real calendar clients will decide what product shape is possible.
Apple Calendar, Thunderbird, GNOME Calendar, Outlook, DAVx5, and similar clients may all disagree about the parts of CalDAV that matter most.

Account setup is part of the product, not an afterthought.
Configuration profiles, QR pairing, username/password-shaped credentials, local credential revocation, and manual setup as a floor all need early pressure-testing.

Conflict handling is where Tide Table should stay most Small Sea-shaped.
When concurrent edits cannot be safely merged, preserve the competing states and make the ambiguity visible.
A likely UX is a read-only "Tide Table Conflicts" calendar with synthetic `CONFLICT: ...` events that link to a small local resolver.

Time zones and recurrence cannot remain vague for long.
Even read-only display can break around `VTIMEZONE`, daylight-saving transitions, all-day events, floating times, and recurring events.

Mobile probably splits the roadmap.
Desktop can start with localhost CalDAV.
iOS may need a Home Hub or other remote CalDAV shape to preserve the Hub security model, while Android may eventually support something closer to a local Hub after serious experimentation.

External invitations should stay humble at first.
Exporting `.ics` for ordinary email sharing is a much safer first bridge than full RSVP or iTIP workflows.

The Tide Table name has not been collision-checked the way The Hedgerow's was.
Before serious branding or external mentions, a brief sweep is worth doing: GitHub orgs, npm/PyPI/crates package names, `.org` domains, USPTO marks, and the major app stores.
"Tide table" as a phrase is mostly nautical territory, but "Tide Table" as software may overlap with something not yet noticed.

Above all, Tide Table should remain an adapter, not a calendar empire.
Focused setup, status, and conflict-resolution screens are useful, but ordinary calendar apps should remain the main calendar UI.
