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

No single implementation will work everywhere.
This section gives the executive summary of each candidate so a developer can read just this README and have an honest sense of the landscape.
Fuller treatment, including service-philosophy boundaries and risky-provider rules, is in [architecture.md](architecture.md).

### Cloud storage plus notifications

Sender encrypts an event batch, writes it to generic cloud storage, asks the Hub to fire a notification; receiver wakes, fetches, applies.

- **Shape:** personal-egress (each device brings its own storage and notification provider).
- **Tier:** default — works with services regular users already have.
- **Strengths:** universal floor; works when no other path is available; vendor-replaceable on both sides.
- **Weaknesses:** not real streaming; latency from "feels live" down to "feels delayed" depending on notifications and polling; battery cost from polling.
- **Status:** first-class degraded mode, not an afterthought.

### User operated relays

A team member deploys a Small Sea relay binary on infrastructure they pay for and trust — a PaaS click-deploy, a VPS, or a home machine — and the team's Hubs route through it when direct connectivity fails.

- **Shape:** personal-egress (one person provisions, the rest connect).
- **Tier:** default if the PaaS path passes the "non-technical person can do it" UX bar; power-user otherwise.
- **Strengths:** no vendor lock-in; team controls the relay; relay is app-opaque so it can be swapped or rotated.
- **Weaknesses:** real operator burden — billing card, deploy click-through, certificate, long-term ownership; UX of candidate PaaS substrates is an open question.
- **Status:** probably the most important thing to get right; whether it counts as default tier is a UX investigation, not a software project.

### P2P overlay frameworks

A bundled stack — Iroh, libp2p (DCUtR + circuit relay), Hypercore/Holepunch, IPFS — handles peer discovery, NAT traversal, and fallback relay as one piece.

- **Shape:** typically depends on public bootstrap nodes; relays may be vendor- or community-operated.
- **Tier:** experimental.
- **Strengths:** integrates discovery, traversal, and relay; less hand-rolled glue than assembling STUN/TURN/relay separately.
- **Weaknesses:** ecosystems are young; bootstrap dependencies may not be replaceable on demand; libp2p was already demoted on operator-burden grounds.
- **Status:** worth a survey, especially of Iroh; not a baseline candidate today.

### Whole team subscribes to some VPN service

Every team member joins the same mesh VPN product (Tailscale, ZeroTier, NetBird), and Hubs talk to each other as if on a flat private network.

- **Shape:** shared-network — every participant must be a member of the same instance for it to be useful.
- **Tier:** power-user only; never the baseline path.
- **Strengths:** very high quality once configured — direct paths, low latency, generic transport; products are mature.
- **Weaknesses:** every teammate must adopt the same vendor; useless across teams that picked different vendors; vendor failure blocks the entire team's live transport.
- **Status:** acceptable as opt-in; the matched-membership cost must be documented so users know what they are signing up for.

### LAN and proximity

Hubs reach each other directly over local-area networking — mDNS, Bluetooth, Wi-Fi Direct — when they happen to be physically close.

- **Shape:** zero third-party.
- **Tier:** default when it applies; doesn't apply often enough to stand alone.
- **Strengths:** lowest latency and highest privacy of any option; works offline; no provider metadata.
- **Weaknesses:** only available when teammates are on the same link; varies by OS and platform permissions.
- **Status:** real win when it applies; should compose with other options, not replace them.

### STUN when the routers are well behaved

A WebRTC peer connection uses ICE with a STUN server to discover its public-facing address; if the NATs cooperate, the steady-state path is directly between devices.

- **Shape:** personal-egress (free vendor STUN such as Google or Cloudflare; signaling is the Hub's job).
- **Tier:** default — STUN is ambient and free.
- **Strengths:** lowest latency of any option; minimal external dependency once connected; no per-team setup.
- **Weaknesses:** only works when NATs cooperate — fails on symmetric NATs, carrier-grade NATs, many corporate networks; not a complete solution on its own.
- **Status:** always tried first inside the WebRTC flow; pairs with TURN as the natural fallback.

### TURN when the routers are jerks

When STUN cannot establish a direct path, both peers connect outbound to a TURN relay that forwards encrypted bytes between them.

- **Shape:** probably personal-egress (vendor TURN as SaaS — Twilio Network Traversal, Cloudflare Calls, Xirsys, Metered, Vonage). The "only one side needs the provider account" version needs validation.
- **Tier:** default-live candidate — vendor TURN is a real market with no infrastructure to operate, but the billing and setup model is still developer-shaped.
- **Strengths:** works on hostile networks where direct paths fail; vendor-replaceable; WebRTC data-channel payloads should remain encrypted between peers.
- **Weaknesses:** paid metered service; latency higher than direct; short-lived session credentials must be minted on demand, not synced.
- **Status:** the natural fallback inside the WebRTC flow; promising default-live candidate, pending validation.

### Anonymizing networks

Hubs communicate through a privacy-preserving overlay such as Tor instead of exposing direct peer addresses or ordinary relay metadata.

- **Shape:** shared public anonymity infrastructure, with unclear fit for Small Sea's provider model.
- **Tier:** exploratory only.
- **Strengths:** may reduce provider-visible metadata about who is talking to whom; potentially useful for especially sensitive teams.
- **Weaknesses:** latency and reliability may be poor for interactive use; economics, abuse constraints, mobile behavior, and compatibility with the Hub model are unclear.
- **Status:** worth an experiment, not a candidate baseline.

### Federated messaging substrates

Use an existing federated network — Matrix, XMPP, possibly IMAP — as the transport pipe.

- **Shape:** federated; user picks a provider from a public network of providers.
- **Tier:** exploratory only.
- **Strengths:** large pre-existing provider base; federation aligns with Small Sea's anti-monoculture stance.
- **Weaknesses:** message-shaped not stream-shaped; federated reachability politics; identity bleed across systems.
- **Status:** listed for completeness; probably not adopted, but the absence of an explicit rejection has been a gap.

### Platform-vendor realtime services

Hub uses a single vendor's realtime infrastructure — Apple PushKit, Firebase Realtime Database, Google Cloud Pub/Sub — directly.

- **Shape:** single-vendor; usually app-aware to some degree; lock-in is the value proposition.
- **Tier:** rejected.
- **Strengths:** mature, low-effort, often free at small scale.
- **Weaknesses:** fails the local-first boundary on its face — single vendor, not replaceable on demand, often sees app data.
- **Status:** listed so the rejection is on the record. Revisit if a future variant is genuinely generic and replaceable.

### App developer offers relay service for only their app traffic

An app's developer runs a relay sized and tuned for that app's traffic, and Hubs route the app's live payloads through it as an optimization.

- **Shape:** bespoke — app-specific by definition, even if app-opaque on the wire.
- **Tier:** optimization-only; never the baseline path.
- **Strengths:** can deliver performance, reliability, and simplicity that generic relays cannot match for that app's traffic shape.
- **Weaknesses:** introduces a service whose disappearance must not break the app, and which the team must be able to walk away from at any time — not just survive the death of.
- **Status:** open — admissible only if the local-first boundary in [architecture.md](architecture.md) holds, including replaceability on demand. There may be no way to make this work; we keep looking.
