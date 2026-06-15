# Bottlenose

**Status:** concept-stage app.
This package currently contains only enough metadata to be a valid workspace teammate; it does not implement the app yet.

Bottlenose is a Small Sea team contacts and address book app.
The name leans on bottlenose dolphins' social recognition and individually distinctive signature whistles: the app is about remembering who people are and how a team can reach them.

The likely first product shape is not a new contacts UI.
It is a local CardDAV-compatible adapter backed by Small Sea.
Existing contacts and address book clients would talk to Bottlenose over localhost, while Bottlenose stores and syncs team contact state through the normal Small Sea app model.

```text
Contacts app
    v CardDAV over localhost
Bottlenose
    v Small Sea client session
Local app-owned contact state
    v Cod Sync through the Hub
Team berth
```

## Purpose

Contacts are one of the quiet pieces of shared infrastructure that small teams need before they notice they need them.
Families, studios, clubs, neighborhoods, classrooms, and tiny companies all accumulate phone numbers, email addresses, mailing addresses, emergency contacts, vendor contacts, and notes about who is who.

Bottlenose should let those groups keep shared contact information inside Small Sea without asking everyone to abandon the address book tools they already use.
The Small Sea part should be ownership, authorization, local-first storage, and synchronization.
The contacts app ecosystem should continue doing the heavy interface work: platform search, autocomplete, phone and mail integration, contact cards, photos, and device-native editing.

## Architectural Fit

Bottlenose is a Small Sea app, not a Hub feature.
It requests an authorized Hub session for a Bottlenose berth and owns its local materialized contact state.

The local CardDAV surface is a client-facing adapter.
It should listen only on localhost by default and use local credentials or tokens so unrelated software on the same device cannot silently read team contacts.

All Small Sea internet traffic still goes through the Hub.
Bottlenose must not talk directly to cloud storage, notification services, peer devices, external contact providers, or provider APIs.
It also must not read or write Manager-owned Core databases directly.
Team identity, session, and berth information should come from Hub session APIs.

## Product Shape

CardDAV is the likely protocol surface because it is the address book sibling of CalDAV.
It is based on WebDAV and vCard, and is supported by standards-friendly clients such as Apple Contacts, Thunderbird, GNOME/Evolution, KDE address book tooling, and Android through DAVx5-style adapters.

Outlook and Exchange compatibility should stay out of scope for the early product.
Microsoft's native contact stack is Exchange-shaped rather than CardDAV-shaped, and pretending to be Exchange would be a separate compatibility product.
A future bridge that syncs Bottlenose contacts into Microsoft 365 through Microsoft Graph might be possible, but it would not be the core Small Sea adapter.

## What Bottlenose Is Not

These are enduring positions, not first-version omissions.
Specific storage, merge, setup, and conflict-resolution choices should live in a later roadmap after protocol spikes have produced evidence.

- **Not a new native contacts UI.**
  Bottlenose should not replace Apple Contacts, Thunderbird Address Book, GNOME Contacts, KAddressBook, DAVx5, or similar tools.
  Focused setup, status, and conflict-resolution screens are useful, but ordinary contacts clients should remain the main address book UI.
- **Not a public people directory.**
  Contacts live inside Small Sea teams.
  There is no global person search, public profile registry, or internet-facing directory.
- **Not a CRM.**
  Bottlenose is for shared address book knowledge, not sales pipelines, marketing automation, lead scoring, or customer lifecycle management.
- **Not a generic CardDAV hosting service.**
  The CardDAV surface is a localhost adapter for the device's own contacts apps.
  It is not a network-reachable hosted CardDAV service, an iCloud replacement, or a multi-tenant address book provider.
- **Not an Exchange clone.**
  Exchange ActiveSync, MAPI/HTTP, EWS, and Outlook-native compatibility are far larger and more proprietary surfaces than this app should carry early.

## Backlog Notes

When Bottlenose moves beyond concept stage, start with a throwaway CardDAV and account-setup spike rather than storage architecture.
Real contacts clients will decide what product shape is possible.
Apple Contacts, Thunderbird, GNOME/Evolution, KDE tooling, DAVx5, and similar clients may disagree about discovery, vCard versions, sync tokens, ETags, groups, photos, and custom fields.

Account setup is part of the product, not an afterthought.
Configuration profiles, QR pairing, username/password-shaped credentials, local credential revocation, and manual setup as a floor all need early pressure-testing.

Conflict handling should stay Small Sea-shaped.
When concurrent edits cannot be safely merged, preserve the competing states and make the ambiguity visible.
Contacts are less dangerous than recurring calendar events, but identity ambiguity is still real: two people can create similar cards for the same person, one teammate can delete while another edits, and clients can rewrite vCards in surprising ways.

A small local resolver should handle ambiguous merges.
The resolver can offer choices such as pick one version, preserve both as separate contacts, merge selected fields, or defer.
The ordinary contacts app should provide the breadcrumb, not the whole repair surface.

Above all, Bottlenose should remain an adapter, not an address book empire.
Its promise is not "a smarter contacts app."
Its promise is "our shared address book belongs to us, and it still works with the contact tools we already know."
