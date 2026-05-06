# Small Sea Live

"Small Sea Live" is the working name for this package.
Something more evocative will eventually replace it.

## Purpose

The Small Sea project itself provides no internet services.
Apps run against a local Hub, and the Hub translates their needs onto generic services users can choose or operate.
For Small Sea apps to offer real-time collaboration — live events and live transport capabilities between authorized devices — Small Sea Live has to build that experience from the transports available in the wild: local networks, NAT traversal, relays, cloud storage, notifications, and other imperfect options.

Small Sea Live is the third major networking service the Hub provides to applications, after cloud storage and peer notification.
It is the Hub's live coordination layer for Small Sea devices — across teammates and across one person's own devices — and it is explicit about the transport quality it is currently delivering rather than pretending all paths feel the same.
It moves app-opaque events between authorized devices, plus the membership and reachability information apps need to address those events.
It also exposes which live transport capabilities are currently available — best-effort events, reliable byte streams, and unreliable datagrams when the underlying transport supports them.

Live transport between devices on uncooperative networks is a patchwork of partial options that compromise differently on latency, cost, operator burden, vendor entanglement, and privacy.
This package centralizes that complexity inside Small Sea, so app developers get one interface while still seeing the capabilities and limits of the transport currently in use.
If the best available path falls all the way back to storage plus notifications, users will experience something different.
The abstraction should report that difference rather than papering it over.

What makes Small Sea Live not just another generic networking layer is its integration with Small Sea's identity and authorization.
The Manager owns provider account configuration and the team/device authorization model.
The Hub is responsible for live communication through both.
Apps don't authenticate users, don't pair devices, and don't decide who's allowed to talk to whom — the Hub answers all of that, the same way it does for storage and notifications.

## Scope

Small Sea Live owns transport and the thin information layer immediately above it.

In scope:

- per-device reachability and current transport mode
- membership and device addressing, derived from Small Sea's authorization model
- delivery of app-opaque events to a device, to all of a member's devices, to all reachable devices in a team, or to a caller-supplied scope within that team
- reliable byte streams between authorized devices, when the active transport supports them
- unreliable datagrams between authorized devices, when the active transport supports them
- explicit reporting of the mode and degradation the available transport currently provides

Deliberately not in scope:

- durable transfer, resumable file movement, or bulk replication
- media semantics such as codecs, tracks, mixing, or device capture
- presence semantics — online vs. away vs. idle vs. typing, what counts as activity, when "online" expires
- heartbeat policy and expiry
- durable rooms, channel membership, or subscription state
- reconciliation across multiple devices reporting different states for the same member
- app-specific liveness inference

Routing scopes are named, app-defined labels for best-effort live fanout.
They are not rooms, durable subscriptions, permission boundaries, or presence models.
Interest in a scope is connection-bound: when the app's live session ends, its interest ends.

The layer above is real and app authors will want it. The likely shape is a thin Hub-side library plus client-side helpers built on top of Small Sea Live's primitives. Whether that ships as a sibling Small Sea package, as a third-party library, or is left to each app is an open committee question. See [architecture.md](architecture.md) for the rationale behind drawing the line where it is.

This package is not a CRDT library and it is not durable sync.
CRDT libraries and realtime apps are expected customers, but durable truth still belongs to app state and Cod Sync.

## App Interface

The app-facing API should feel like a Hub service, not like a networking toolkit.
Apps start an authorized Hub session, then ask the Hub to move app-opaque live events to Small Sea identities and routing scopes.

The likely primitives are:

- send to a specific device
- send to a member's reachable devices
- broadcast to reachable devices in the team
- register connection-bound interest in an app-defined routing scope
- broadcast to reachable devices interested in a routing scope
- receive events delivered to any of the above
- open a reliable byte stream to an authorized device, when supported
- accept a reliable byte stream from another authorized device, when supported
- send and receive unreliable datagrams to and from an authorized device, when supported
- observe the current transport mode and per-primitive availability

Delivery is best-effort: apps should expect out-of-order arrival and possible duplicates, especially in degraded modes.
Reliable byte streams require a live transport mode and are unavailable when the package falls back to mailbox-degraded delivery.
Unreliable datagrams sit on a more specific capability floor; they are useful for high-rate connection-shaped traffic where loss is preferable to head-of-line blocking, and not every live transport supports them.
Routing scopes are namespaced per app, so two apps on the same Hub can pick the same scope name without colliding.

Common patterns compose on these primitives without becoming primitives themselves: request/response is events plus a correlation ID; file transfer with resumability is reliable streams plus an app-layer protocol; media is datagrams or reliable streams plus codecs above the line; delivery acknowledgment is a receiver-sent return event.

Apps should not need to authenticate users, pair devices, discover relay providers, or decide which peer addresses are valid.
They should not need to know whether the current path is LAN, STUN, TURN, relayed, or mailbox-degraded to send an event — but they do get capability and mode information so they can adjust UX when "live" becomes delayed, expensive, partial, or unavailable.

## Implementation Options

No single implementation will work everywhere.
This section gives the executive summary of each candidate so a developer can read just this README and have an honest sense of the landscape.
Fuller treatment, including service-philosophy boundaries and risky-provider rules, is in [architecture.md](architecture.md).

At a glance, in rough order of implementation priority:

**Likely baseline path:**

- **Cloud storage plus notifications** — universal degraded floor.
- **STUN** — cheap direct path when NATs cooperate.
- **TURN** — vendor relay fallback for hostile NATs.
- **LAN and proximity** — beats everything when teammates are on the same link.
- **User operated relays** — team-controlled relay if a member is willing to deploy and own it.

**Power-user opt-in:**

- **Whole team subscribes to some VPN service** — high-quality direct paths if every member joins the same vendor.

**Exploratory or constrained:**

- **P2P overlay frameworks** — bundled discovery, traversal, and relay; Iroh worth a look.
- **Anonymizing networks** — explored for teams that want metadata privacy.
- **App developer offers relay service** — bespoke optimization, admissible only if it stays replaceable.

**Listed for completeness:**

- **Federated messaging substrates** — using existing federated networks as a transport pipe.
- **Platform-vendor realtime services** — listed so the rejection is on the record.

### Cloud storage plus notifications

Sender encrypts an event batch, writes it to generic cloud storage, asks the Hub to fire a notification; receiver wakes, fetches, applies.

- **Shape:** personal-egress (each device brings its own storage and notification provider).
- **Strengths:** universal floor; works when no other path is available; vendor-replaceable on both sides.
- **Weaknesses:** not real streaming; latency from "feels live" down to "feels delayed" depending on notifications and polling; battery cost from polling.
- **Fit:** default baseline and first-class degraded mode, not an afterthought.

### STUN when the routers are well behaved

A WebRTC peer connection uses ICE with a STUN server to discover its public-facing address; if the NATs cooperate, the steady-state path is directly between devices.

- **Shape:** personal-egress (free vendor STUN such as Google or Cloudflare; signaling is the Hub's job).
- **Strengths:** lowest latency of any option; minimal external dependency once connected; no per-team setup.
- **Weaknesses:** only works when NATs cooperate — fails on symmetric NATs, carrier-grade NATs, many corporate networks; not a complete solution on its own.
- **Fit:** default attempt inside the WebRTC flow; pairs with TURN as the natural fallback.

### TURN when the routers are jerks

When STUN cannot establish a direct path, both peers connect outbound to a TURN relay that forwards encrypted bytes between them.

- **Shape:** probably personal-egress (vendor TURN as SaaS — Twilio Network Traversal, Cloudflare Calls, Xirsys, Metered, Vonage). The "only one side needs the provider account" version needs validation.
- **Strengths:** works on hostile networks where direct paths fail; vendor-replaceable; WebRTC data-channel payloads should remain encrypted between peers.
- **Weaknesses:** paid metered service; latency higher than direct; short-lived session credentials must be minted on demand, not synced.
- **Fit:** promising default-live candidate and natural WebRTC fallback, pending validation; the billing and setup model is still developer-shaped.

### LAN and proximity

Hubs reach each other directly over local-area networking — mDNS, Bluetooth, Wi-Fi Direct — when they happen to be physically close.

- **Shape:** zero third-party.
- **Strengths:** lowest latency and highest privacy of any option; works offline; no provider metadata.
- **Weaknesses:** only available when teammates are on the same link; varies by OS and platform permissions.
- **Fit:** default when it applies, but it does not apply often enough to stand alone; should compose with other options.

### User operated relays

A team member deploys a Small Sea relay binary on infrastructure they pay for and trust — a PaaS click-deploy, a VPS, or a home machine — and the team's Hubs route through it when direct connectivity fails.

- **Shape:** personal-egress (one person provisions, the rest connect).
- **Strengths:** no vendor lock-in; team controls the relay; relay is app-opaque so it can be swapped or rotated.
- **Weaknesses:** real operator burden — billing card, deploy click-through, certificate, long-term ownership; UX of candidate PaaS substrates is an open question.
- **Fit:** probably the most important thing to get right; default-live candidate if the PaaS path passes the "non-technical person can do it" UX bar, power-user otherwise.

### Whole team subscribes to some VPN service

Every team member joins the same mesh VPN product (Tailscale, ZeroTier, NetBird), and Hubs talk to each other as if on a flat private network.

- **Shape:** shared-network — every participant must be a member of the same instance for it to be useful.
- **Strengths:** very high quality once configured — direct paths, low latency, generic transport; products are mature.
- **Weaknesses:** every teammate must adopt the same vendor; useless across teams that picked different vendors; vendor failure blocks the entire team's live transport.
- **Fit:** power-user opt-in only; never the baseline path; the matched-membership cost must be documented.

### P2P overlay frameworks

A bundled stack — Iroh, libp2p (DCUtR + circuit relay), Hypercore/Holepunch, IPFS — handles peer discovery, NAT traversal, and fallback relay as one piece.

- **Shape:** typically depends on public bootstrap nodes; relays may be vendor- or community-operated.
- **Strengths:** integrates discovery, traversal, and relay; less hand-rolled glue than assembling STUN/TURN/relay separately.
- **Weaknesses:** ecosystems are young; bootstrap dependencies may not be replaceable on demand; libp2p was already demoted on operator-burden grounds.
- **Fit:** experimental; worth a survey, especially of Iroh; not a baseline candidate today.

### Anonymizing networks

Hubs communicate through a privacy-preserving overlay — Tor, I2P, Lokinet, or similar — instead of exposing direct peer addresses or ordinary relay metadata.

- **Shape:** shared public anonymity infrastructure, with unclear fit for Small Sea's provider model.
- **Strengths:** may reduce provider-visible metadata about who is talking to whom; potentially useful for especially sensitive teams.
- **Weaknesses:** latency and reliability are poor for interactive use; mix-hop overhead generally defeats datagrams even where the network supports them (Tor lacks them entirely; I2P and Lokinet provide them but slowly); economics, abuse constraints, mobile behavior, and Hub compatibility are unclear.
- **Fit:** exploratory only; worth an experiment, not a candidate baseline.

### App developer offers relay service for only their app traffic

An app's developer runs a relay sized and tuned for that app's traffic, and Hubs route the app's live payloads through it as an optimization.

- **Shape:** bespoke — app-specific by definition, even if app-opaque on the wire.
- **Strengths:** can deliver performance, reliability, and simplicity that generic relays cannot match for that app's traffic shape.
- **Weaknesses:** introduces a service whose disappearance must not break the app, and which the team must be able to walk away from at any time — not just survive the death of.
- **Fit:** optimization-only; never the baseline path; admissible only if the local-first boundary in [architecture.md](architecture.md) holds, including replaceability on demand.

### Federated messaging substrates

Use an existing federated network — Matrix, XMPP, possibly IMAP — as the transport pipe.

- **Shape:** federated; user picks a provider from a public network of providers.
- **Strengths:** large pre-existing provider base; federation aligns with Small Sea's anti-monoculture stance.
- **Weaknesses:** message-shaped not stream-shaped; federated reachability politics; identity bleed across systems.
- **Fit:** exploratory only; listed for completeness; probably not adopted, but the absence of an explicit rejection has been a gap.

### Platform-vendor realtime services

Hub uses a single vendor's realtime infrastructure — Apple PushKit, Firebase Realtime Database, Google Cloud Pub/Sub — directly.

- **Shape:** single-vendor; usually app-aware to some degree; lock-in is the value proposition.
- **Strengths:** mature, low-effort, often free at small scale.
- **Weaknesses:** fails the local-first boundary on its face — single vendor, not replaceable on demand, often sees app data.
- **Fit:** rejected; listed so the rejection is on the record. Revisit if a future variant is genuinely generic and replaceable.
