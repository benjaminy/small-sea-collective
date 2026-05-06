# Small Sea Live Architecture

This document is a stub.
It records the questions that need careful design before `small-sea-live` grows real behavior.

## Ownership Boundary

The Manager owns provider account configuration.
That includes user-facing setup, long-lived provider credentials, relay lists, and provider rotation policy.

The Hub owns live communication.
That includes opening paths, minting short-lived session credentials when needed, sending and receiving app-opaque payloads, and reporting transport quality to apps.

This package should provide reusable mechanics for the Hub without taking over either role.

## Layering Rationale

Small Sea Live owns transport and a thin information layer immediately above it; everything more semantic lives above the line.

In scope:

- per-device reachability state and current transport mode
- membership-aware addressing, derived from Small Sea's authorization model
- app-opaque event delivery to a device, to a member's reachable devices, to all reachable devices in a team, or to a caller-supplied scope within that team
- explicit reporting of mode and degradation

Above the line, deliberately not in scope:

- presence semantics — online vs. away vs. idle vs. typing, what counts as activity, when "online" expires
- heartbeat policy and expiry
- durable rooms, channel membership, or subscription state
- reconciliation across multiple devices reporting different states for the same member
- app-specific liveness inference

Routing scopes are named, app-defined labels for best-effort live fanout.
They give apps a generic fanout target for document sessions, chat channels, lobbies, or cursor streams without making Small Sea Live own those concepts.
A scope is only a routing label.
It is not durable, not a permission boundary, not a presence set, and not a room membership model.

Scope interest is ephemeral state owned by the live session.
It is connection-bound: when an app's live session disconnects from its local Hub, its scope interest ends.
Small Sea Live should not add wildcard matching or parse scope hierarchy.
Apps may encode hierarchy in their own scope strings, but the package treats the scope as an opaque label.

Mailbox-degraded transport is allowed to overdeliver physically.
In mailbox mode, a scoped event may be written broadly enough that every reachable team mailbox can fetch it, and the receiving Hub filters by app and scope before delivery to connected app sessions.
The mode signal tells apps that scope delivery is no longer a low-latency routing guarantee.
Scopes are therefore convenience and fanout hints, not privacy or cost boundaries.

Two reasons to draw the line there.

Presence semantics are easy to design wrong and hard to change.
Whichever model gets baked in — heartbeat vs. event-based, per-device vs. per-member, eventual vs. last-writer-wins, what "online" means when the only path is mailbox polling — apps will depend on it, and reversing later breaks them.
A separate layer is reversible; a primitive baked into the Hub abstraction is not.

The wider the package's contract, the harder it is to honestly represent transport reality across modes.
Pure transport hides STUN, TURN, relay, and mailbox behind one stream plus a mode signal.
Add presence and the package has to define what presence *means* in each mode, including when it is a 30-second-old polling result.
Keeping presence above the line keeps the awkwardness above the line.

### Hard Boundary

Even within the in-scope side, Small Sea Live is not a CRDT engine, document model, or durable sync layer.
CRDT libraries and realtime apps are expected customers, but durable truth lives in app state and Cod Sync.

### Where The Layer Above Lives

Open question. Three plausible homes:

- a sibling Small Sea package — strongest default for app authors, but pulls semantics back into the project this line is meant to keep out
- a third-party local-first library — keeps the line meaningful, depends on someone building one
- left to each app — maximum flexibility, but everyone reinvents it

Committee has not picked.

### App-facing Primitives (provisional)

- send an app-opaque event to a device
- send an app-opaque event to a member's reachable devices
- broadcast an app-opaque event to reachable devices in a team
- broadcast an app-opaque event to reachable devices currently interested in an app-defined scope
- register connection-bound interest in an app-defined scope
- report whether the current path is direct, relayed, mailbox-degraded, or unavailable

## Delivery Semantics

Small Sea Live is best-effort.
On live transports (LAN, STUN, TURN, user-operated relays) per-connection ordering is preserved, duplicates are rare, subscription state is current, and scope routing is honored — events go only to currently interested devices.
Mailbox mode has no live subscription state to consult, which forces a choice.

Two options for how broadcast and scope routing degrade in mailbox mode:

A. **Deliver-then-filter.** Broadcast and scope-broadcast both write to every team mailbox in mailbox mode; receivers filter by scope locally.
The package's contract stays simple — recipients always get the events meant for them — and degradation is visible only as latency and mode signals.
The cost is bandwidth and storage for narrow scopes that few devices care about.

B. **Degrade-to-team.** Scope routing collapses to team-broadcast in mailbox mode; apps see a different fanout shape between modes and filter the rest themselves.
This shifts cost to apps and asks them to handle a mode-dependent contract.

Current lean is (A).
(B) doesn't actually save anyone work — apps end up filtering received events anyway — and it breaks the property that recipient correctness is invariant across modes.
Committee has not formally picked.

Whichever option lands, app-level dedup and ordering are the layer above's problem.
Apps that need ordering or deduplication attach app-level event IDs and reconcile on receive.
The package promises only best-effort delivery.

## Prior Art To Study

**Realtime channels and subjects.**
Ably channels, Pusher channels, Supabase Realtime channels, NATS subjects, MQTT topics, and libp2p pubsub topics all point to the same small reusable primitive: named scopes for publish/subscribe fanout.
They are useful because app authors need more than whole-team broadcast, but they stay generic when the scope is just a routing label.
Small Sea Live should consider borrowing that thin core without adopting durable rooms, channel-specific permissions, or provider-owned presence semantics.

**Yjs Awareness.**
Yjs ships an Awareness protocol that providers commonly implement alongside document sync.
It demonstrates that presence can reasonably live near the network provider boundary rather than inside every app.
Caveat: Awareness piggybacks on the same provider that already understands Yjs documents — it is coupled to a specific data model.
For Small Sea Live, transport is app-opaque, so the analogue is presence as a separate channel, not presence riding the data path.

**Automerge Repo.**
Automerge Repo separates storage adapters from network adapters, and treats ephemeral messages and presence as non-durable collaboration support.
That boundary is a useful analogue for Small Sea: Cod Sync remains the durable path, while Small Sea Live handles live coordination.
Borrow the adapter split and the ephemeral/durable distinction, not Automerge's document model.

**Liveblocks.**
Liveblocks exposes Presence and Broadcast as core realtime primitives, and developers reach for them eagerly.
That is evidence of demand for higher-level live coordination primitives.
It is not a shape Small Sea can adopt — Liveblocks itself is exactly the kind of canonical app-server Small Sea avoids.
Useful as market signal, not as a model.

**libp2p pubsub.**
libp2p includes publish/subscribe as a network-layer primitive, fully app-opaque and decoupled from any data model.
This is the closest structural analogue to what Small Sea Live's broader scope would offer.
Caveat: it lives inside a stack with significant adoption costs, and the experiments doc has already demoted libp2p from the default tier on the basis of operator burden.
Borrow the shape, not necessarily the implementation.

These examples do not decide the scope for Small Sea Live.
They only make the broader scope less obviously excessive, and they hint at where the seams should be.

## Risky Providers

Some live providers may be more bespoke or app-specific than Small Sea usually prefers.
They are not automatically forbidden.

The local-first boundary is:

- provider failure must not destroy durable team history
- provider failure must not break team identity or membership
- provider failure must not prevent degraded non-live collaboration
- the provider must be replaceable on demand — a team that wants to stop using it must be able to, without losing data, identity, or the ability to keep collaborating; provider survival is not enough, optionality is
- providers should not see app plaintext where end-to-end encryption is practical
- apps should still go through the Hub

The open design question is how much performance and simplicity Small Sea can accept from risky live providers without letting them become the project.

## Claims To Validate

Some claims in the README are working hypotheses, not settled facts.

- Validate whether vendor TURN can work as one-sided personal-egress provisioning.
  One participant may be able to bring the provider account, but both peers still need usable ICE credentials during connection setup.
- Validate what a TURN provider observes for WebRTC data-channel traffic.
  Payloads should remain encrypted between peers, but who-is-talking-to-whom metadata is probably visible and should be documented.
- Validate the tier labels.
  Cloud storage plus notifications are baseline default; vendor TURN may be the best default-live candidate rather than baseline infrastructure.
- Validate whether anonymizing networks such as Tor are practical enough to deserve any place in the implementation landscape.
  The experiment should consider latency, reliability, abuse controls, provider economics, mobile behavior, and compatibility with Hub-mediated traffic.
