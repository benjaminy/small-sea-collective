# Small Sea Live

"Small Sea Live" is the working name for this package.
Something more evocative will eventually replace it.

## Purpose

Small Sea apps already get two ambient services from the Hub: generic cloud storage for durable data, and notifications for waking peers up.
Small Sea Live is the third ambient capability.
It is the Hub's abstraction for live-ish interaction between Small Sea devices — across teammates, and across one person's own devices.

Unlike storage and notifications, there is no end-user-facing vendor that just provides this.
Live transport between devices on uncooperative networks is a patchwork of partial options that compromise differently on latency, cost, operator burden, vendor entanglement, and privacy.
This package exists to hide that patchwork from app developers without lying to them about the transport they actually have at runtime.
If the best available path falls all the way back to storage plus notifications, users will experience something different.
The abstraction should report that difference rather than papering it over.

The Manager owns provider account configuration.
The Hub is responsible for doing live communication through the providers the Manager has configured.

## Scope

Open question, not settled.

The narrow reading is that this package owns byte-stream transport between two device endpoints and reports the mode it is running in (direct, relayed, mailbox-degraded, unavailable).
Apps build presence, multi-device fan-out, and team broadcast on top.

The broader reading is that presence, multi-device awareness, and team-scoped broadcast belong here too, because building those well on top of raw transport needs the same mode-aware information the package already has, and because pushing them into apps means every app reinvents them — probably badly.

Current lean is broader.
The argument for narrow is real and this section will keep saying so until the question is actually settled.

See [architecture.md](architecture.md) for the currently unresolved design questions.

## App Interface

What does Small Sea Live look like to apps?

One simple piece: Apps have to start Hub sessions before they get to play at all.
Just like storage and notifications.

Probably a basic point to point byte stream should be part of it.
But what about an individual's multiple devices?
And teammates?
What should broadcast/multicast look like?
I hope there is some good prior art to draw on here.
The serious challenge here is that I want very different implementation options to poke through the abstraction boundary as little as possible.
Perfect abstraction is probably impossible.

## Implementation Options

### Cloud storage plus notifications

### User operated relays

### Whole team subscribes to some VPN service

### STUN when the routers are well behaved

### TURN when the routers are jerks

### App developer offers relays service for only their app traffic

This is a slippery slope that we need to be super careful about.
One of the founding principles of Small Sea is no dependence on bespoke services.
But risky service providers may still be welcome in the Small Sea ecosystem if they stay inside firm local-first boundaries.
They must not become the durable source of truth, the identity authority, or the only way a team can continue existing.
Within that boundary, bespoke live services might provide useful performance, simplicity, or reliability boosts.
