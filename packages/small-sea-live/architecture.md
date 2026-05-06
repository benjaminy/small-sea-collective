# Small Sea Live Architecture

This document is a stub.
It records the questions that need careful design before `small-sea-live` grows real behavior.

## Ownership Boundary

The Manager owns provider account configuration.
That includes user-facing setup, long-lived provider credentials, relay lists, and provider rotation policy.

The Hub owns live communication.
That includes opening paths, minting short-lived session credentials when needed, sending and receiving app-opaque payloads, and reporting transport quality to apps.

This package should provide reusable mechanics for the Hub without taking over either role.

## Scope Question

The largest unresolved question is whether this package should stop at transport or also include higher-level live coordination.

Narrow scope:

- point-to-point byte transport
- transport mode reporting
- degradation reporting
- local fake transports for micro tests

Broader scope:

- presence
- multi-device awareness
- team-scoped broadcast
- app-opaque ephemeral events

The current lean is broader, but not settled.
The reason to consider the broader scope is that presence and broadcast depend on the same transport-quality information that raw streams do.
If every app builds those features separately, they will probably repeat the same mistakes.

The broader scope still has a hard boundary.
Small Sea Live should not become a CRDT engine, document model, or durable sync layer.
It should give apps and CRDT libraries a best-effort way to move app-opaque live events between authorized team devices.
Durable truth remains in app state and Cod Sync.

Possible app-facing primitives:

- send an app-opaque event to a device
- send an app-opaque event to a member's reachable devices
- broadcast an app-opaque event to reachable devices in a team or topic
- publish ephemeral presence or awareness state
- report whether the current path is direct, relayed, mailbox-degraded, or unavailable

## Prior Art To Study

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
