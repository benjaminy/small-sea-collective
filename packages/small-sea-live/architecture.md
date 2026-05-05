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

## Prior Art To Study

Yjs has an Awareness protocol that providers commonly implement alongside document sync.
That suggests presence can reasonably live near the network provider boundary rather than inside every app.

Liveblocks exposes Presence and Broadcast as core realtime primitives.
That is not Small Sea's trust model, but it is evidence that app developers benefit from higher-level live coordination primitives.

libp2p includes publish/subscribe as a network-layer primitive.
That suggests team or topic broadcast can be treated as transport infrastructure rather than only app behavior.

These examples do not decide the scope for Small Sea Live.
They only make the broader scope less obviously excessive.

## Risky Providers

Some live providers may be more bespoke or app-specific than Small Sea usually prefers.
They are not automatically forbidden.

The local-first boundary is:

- provider failure must not destroy durable team history
- provider failure must not break team identity or membership
- provider failure must not prevent degraded non-live collaboration
- providers should not see app plaintext where end-to-end encryption is practical
- apps should still go through the Hub

The open design question is how much performance and simplicity Small Sea can accept from risky live providers without letting them become the project.
