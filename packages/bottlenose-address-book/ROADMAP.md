# Bottlenose Roadmap

**Status:** broad plan, not a commitment.
Each phase exists to retire a specific unknown; later phases will sharpen as earlier ones teach us things.

## Why This Sequence

The interesting risks in Bottlenose are not in the Small Sea half of the system.
Storage in a berth, sync through the Hub, session authorization — those use the same machinery every other Small Sea app uses, and the patterns are known.

The risks are in the contacts half.
CardDAV is smaller than CalDAV, but address book clients still vary in discovery behavior, vCard versions, groups, photos, ETag handling, sync tokens, and how aggressively they rewrite cards.
Contact merge also has a special human risk: silently combining two different people is just as bad as silently splitting one person into several stale cards.

So the roadmap is sequenced to confront the scariest unknowns earliest, with cheap experiments before commitments.
Each phase ends with a tighter or revised plan for the next one rather than a fixed multi-phase plan worked out up front.

## Known Unknowns

Roughly ordered by how badly they could break the project if they turn out wrong.

1. **Real CardDAV clients are quirky.**
   Apple Contacts, Thunderbird, GNOME/Evolution, KDE address book tooling, and DAVx5-style Android paths may disagree about discovery, vCard versions, ETags, sync tokens, groups, photos, and custom fields.
   We do not know which client behaviors will accept the kind of server we want to build until we try.
2. **Setup UX may be the actual constraint.**
   Even a perfect adapter is worthless if a non-technical user cannot finish the "add account" flow.
   The provisioning story (configuration profiles, QR pairing, username/password-shaped credentials, manual entry as a floor) needs early pressure-testing.
3. **Local authentication is harder than localhost-only suggests.**
   Localhost binding does not prevent unrelated processes on the same machine from reading team contacts.
   We need a per-client credential model that fits into how contacts apps actually store credentials and that can be revoked when a client is uninstalled.
4. **Contact identity and merge are genuinely hard.**
   Two teammates can create similar cards for the same person, one can delete while another edits, and clients can rewrite vCards in ways that make naive text merges misleading.
   Bottlenose should not collapse distinct people by friendly name, email address, or arbitrary row order.
5. **Groups and lists are not uniform across clients.**
   Some clients model groups as vCard categories, some as address book collections, some as separate list objects, and some barely round-trip them.
   Team address books need a conservative model that does not lose group membership under sync.
6. **Contact photos change the storage profile.**
   Photos are binary blobs riding alongside mostly text-shaped vCards.
   We need to decide whether photos live inline, as separate content-addressed objects, or behind some other local materialization strategy before Cod Sync starts carrying many large contact cards.
7. **Outlook and Exchange are a separate compatibility world.**
   Microsoft's native contact stack is Exchange-shaped rather than CardDAV-shaped.
   A future Microsoft 365 bridge might sync through Graph, but pretending to be Exchange should remain outside the early app.

The phases below try to expose each of these to a cheap test before anything downstream relies on the answer.

## Phase 0 — Spikes

Timeboxed, throwaway code.
The goal is to answer "will any of this work at all" before building real infrastructure.

- Stand up a trivial CardDAV server on localhost and add it as an account in Apple Contacts, Thunderbird, GNOME/Evolution, KDE address book tooling, and DAVx5 if practical.
  Note what works, what breaks, and where the user-visible friction sits.
- Probe how each client behaves under deliberately quirky server responses: stale ETags, missing sync tokens, partial multistatus responses, rewritten UIDs, photo changes, and group changes.
  These observations shape the server we eventually build.
- Survey low-friction setup paths: configuration profiles, custom-scheme deep links, QR pairing, username/password-shaped credentials, and manual entry as the floor.
  Output is a ranked list, not a chosen mechanism.

What we keep at the end: notes, not code.
The decision the spikes inform is whether the rest of the roadmap is buildable in the shape currently imagined or needs reshaping.

## Phase 1 — Read-only path from a static store

Prove the end-to-end pipeline before introducing writes or sync.

- A real Bottlenose process serving CardDAV on localhost, backed by a hand-curated vCard-shaped store inside a Small Sea berth.
- One team, one address book, read-only.
- Per-client credentials for authentication, exposed through the simplest client-compatible auth shape we can prove against real contacts apps.
  Username/password-style setup is probably the practical baseline; bearer-token support may still be useful for developer tools and micro tests if it is cheap.

This phase retires unknown 1 (basic CardDAV compatibility against real clients), unknown 2 in a first form (the user can actually finish account setup), and unknown 3 (per-client credential model exists).

Deliberately out of scope for this phase: writes, contact photos beyond pass-through fixtures, groups beyond pass-through fixtures, conflict handling, multiple address books, and Outlook/Exchange compatibility.

## Phase 2 — Writes from a single device

Round-trip creates, updates, and deletes from a contacts client through Bottlenose into Small Sea storage, on one device only.

- Commit to a storage shape (one vCard per file? content-addressed blob keyed by UID? canonical JSON plus raw vCard preservation? a journal of changes?) now forced by the need to write.
- ETag and sync-token discipline good enough that real clients do not enter a perpetual re-sync loop.
- Preserve custom fields, photos, and groups through a round-trip even if Bottlenose does not yet interpret every field.

This phase retires the single-writer half of unknowns 4, 5, and 6 and forces the storage-shape decision.
Out of scope: multi-device sync and concurrent edits.

## Phase 3 — Multi-device sync

The first version that looks like the actual product: two teammates editing the same address book from their own devices.

- Sync through the Hub using the team berth.
- Conflict detection at the contact identity level, with conservative handling when identity is ambiguous.
  Bottlenose should preserve competing states rather than guess that two cards are the same person.
- A small local conflict-resolution UI that lets a human choose one version, preserve both, merge selected fields, or defer.
  The resolver should be local and focused; ordinary contacts clients provide the breadcrumb, not the whole repair surface.

This phase retires the multi-device half of unknown 4 and gives conflict UX its first real user-facing answer.
It should also produce evidence about whether the chosen photo and group storage model remains maintainable under Cod Sync.

## Phase 4 — Groups, Photos, and Client Rewrites

Harden the parts most likely to drift between clients after the basic multi-device path works.

- Concurrent edits to group membership from different contacts clients.
- Contact photo updates, removals, and large-photo behavior under Cod Sync.
- Client rewrite behavior where a contacts app normalizes, drops, reorders, or reserializes fields Bottlenose did not mean to change.
- A documented loss policy for edge cases the standards and clients do not resolve cleanly; the goal is honest semantics, not perfect ones.

This phase retires the remaining halves of unknowns 5 and 6.

## Phase 5 — Real-world polish

Once the core is honest about its semantics, the surrounding pieces matter.

- Multiple address books per team, if real client behavior supports that without confusing setup.
- The provisioning flow upgraded from "rough" to "easy enough to demo to a non-technical friend without preparing them."
- Import and export paths for ordinary `.vcf` files.
- A clear stance on whether Bottlenose ever integrates with non-Small Sea contact sources, with the default answer being "not in the core adapter."

## Indefinitely Deferred

Not on the roadmap as currently imagined; would require a new motivating need to revisit.

- A public people directory or global profile registry.
- CRM features such as pipelines, sales stages, lead scoring, marketing automation, or customer lifecycle tracking.
- A generic network-reachable CardDAV hosting service.
- Exchange ActiveSync, MAPI/HTTP, EWS, or Outlook-native compatibility.
  The Microsoft stack should be treated as a separate bridge or compatibility product if it ever matters.
- A Bottlenose-native contacts UI replacing Apple Contacts, Thunderbird Address Book, GNOME Contacts, KAddressBook, DAVx5, or similar tools.
  The whole architecture is built around not doing this; revisiting it would be a redesign, not a phase.

## How This Roadmap Changes

Each phase produces information that should reshape the next.
If Phase 0 reveals that one major client cannot tolerate the kind of server we want to build, Phase 1 may not look the same.
If Phase 2's storage decision turns out to make Phase 3 conflicts intractable, Phase 2 may need to be revisited before continuing.

The intent is to never have more than one phase's worth of detailed planning open at a time.
Long-range commitments past the next phase are deliberately vague, and that vagueness is a feature, not an omission.
