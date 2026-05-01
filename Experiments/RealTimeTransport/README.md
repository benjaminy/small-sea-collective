# Real-Time Transport Experiments

Small Sea wants rich collaboration without bespoke application services.
That goal gets uncomfortable as soon as an app wants live interaction between two edge devices on different private networks.

This experiment track exists to make that discomfort concrete.
Before building a chat app, cursor sharing, voice rooms, or any other real-time-facing product, we need to understand what live-ish transport Small Sea can honestly provide through generic, replaceable, user-chosen services.

## Core Question

If edge devices are not assumed to be internet-reachable, what can Small Sea offer for low-latency communication while preserving the project's service philosophy?

The likely answer is not one transport.
It is a layered capability model:

- Try direct or NAT-traversed peer connectivity when possible.
- Use a generic relay when direct connectivity is not possible.
- Fall back to cloud storage plus notifications when no streaming path is available.
- Always route Small Sea traffic through the local Hub.
- Never require a bespoke app backend.

## Non-Assumptions

These experiments should not assume:

- edge devices have public IP addresses
- users can configure port forwarding
- two peers are on the same LAN
- a direct peer-to-peer path will exist
- the app can open its own internet sockets
- Small Sea operates any canonical remote service

Internet-reachable edge devices may become an optimization later.
They should not be required for the first real-time transport design.

## Service Philosophy

Small Sea can use services.
It should avoid services that become application-specific authorities.

Good candidate service shapes:

- generic cloud storage
- generic notification delivery
- generic NAT traversal or rendezvous
- generic packet relay
- generic mesh networking
- user-controlled relay infrastructure

Bad candidate service shapes:

- a canonical Small Sea chat server
- a server that understands app-specific chat semantics
- a server that is the source of truth for membership or message history
- a server that apps contact directly, bypassing the Hub

The guiding line:

> Generic connectivity services are acceptable.
> Bespoke application services are not.

## Transport Modes

### NAT-Traversed Direct Path

The ideal live path is device-to-device after rendezvous and candidate exchange.
The external service helps peers discover each other, but the steady-state data path is direct.

Candidate provider families:

- WebRTC data channels with ICE/STUN
- mesh VPN systems such as Tailscale or ZeroTier
- future Hub-to-Hub transports over user-managed private networking

Questions:

- How often does this work across realistic home, office, mobile, and cafe networks?
- Can the Hub abstract the provider cleanly enough that apps do not care?
- Does the transport expose enough information to report quality and degradation honestly?
- What identities are bound into the transport setup?
- What metadata leaks to the rendezvous provider?

### Generic Relay

When direct connectivity fails, a relay can provide a live path through an internet-visible service.
The relay should move encrypted bytes or packets and should not understand app semantics.

Candidate provider families:

- TURN-style relay
- mesh VPN relay paths
- a user-controlled VPS relay running a generic protocol
- future Small Sea-compatible relay software that is deliberately app-opaque

Questions:

- What is the smallest relay contract Small Sea needs?
- Can a relay be generic enough to feel like storage or notifications rather than a Small Sea backend?
- How should relay credentials be provisioned and rotated?
- Can a team use multiple relays?
- How does the Hub report relayed mode to apps?

### Mailbox Fallback

The fallback path is not true streaming.
It is durable or semi-durable message exchange through storage, optionally woken by notifications.

Basic shape:

1. Sender writes an encrypted event batch to generic cloud storage.
2. Sender asks the Hub to send a generic notification.
3. Receiver wakes, fetches, verifies, deduplicates, and applies the event.
4. If notification delivery fails, polling eventually discovers the event.

Candidate provider families:

- S3-like object storage
- Dropbox-like file storage
- local MinIO for experiments
- ntfy-like notification delivery
- local notification mocks for experiments

Questions:

- How fast can this feel in the common case?
- What polling interval is tolerable for battery and network use?
- Can notification payloads stay opaque and minimal?
- How should duplicates, delays, and missed notifications be modeled?
- Which apps are acceptable on mailbox fallback, and which must report that live mode is unavailable?

## Candidate Abstraction

The Hub probably should not promise "a stream."
It should promise a live-transport capability with explicit mode and degradation reporting.

Very rough shape:

```text
connect(peer_device_id, berth_id, purpose) -> connection
connection.mode -> direct | nat_traversed | relayed | mailbox | unavailable
connection.send(bytes)
connection.events -> bytes | mode_changed | delayed | closed | failed
connection.close()
```

The exact API will change.
The important design property is that apps learn what quality of transport they have instead of assuming every connection is real-time.

## Ownership Boundaries

The app owns:

- app-specific event schema
- local durable state
- conflict presentation
- product-specific degradation behavior

The Hub owns:

- session authorization
- identity/session information
- all Small Sea internet traffic
- transport provider configuration
- cloud storage access
- notification access
- live transport access

Shared client libraries might own:

- Hub session helpers
- stream connection helpers
- envelope helpers
- retry/backoff helpers
- local test adapters

Open question:
How much app-level validation should the Hub perform for live payloads?
The default should be app-opaque payloads unless an experiment shows a concrete reason to do more.

## Experiment Branches

These branches are proposed as investigation units.
Names are provisional.

### `codex/real-time-transport-survey`

Goal:
Create a capability matrix for candidate generic services.

Scope:

- WebRTC ICE/STUN/TURN
- Tailscale-style mesh connectivity
- ZeroTier-style mesh connectivity
- generic VPS relay
- storage plus notification fallback

Validation:

- Confirm claims against current provider documentation.
- Record what must be self-hosted, user-subscribed, or vendor-operated.
- Record what metadata each provider can observe.
- Record whether the provider can be hidden behind the Hub.

### `codex/live-transport-interface-sketch`

Goal:
Draft the minimal Hub-facing live transport API and state model.

Scope:

- connection lifecycle
- transport mode reporting
- degradation states
- send/receive semantics
- duplicate and retry semantics
- app-opaque payload envelopes

Validation:

- Walk through direct, relayed, mailbox, and unavailable cases.
- Check that no app needs direct internet access.
- Check that the API can support a future chat app without becoming chat-specific.

### `codex/mailbox-fallback-experiment`

Goal:
Measure how tolerable storage plus notifications can be as the universal fallback.

Scope:

- local filesystem or MinIO-backed storage adapter
- local notification mock, then possibly an ntfy-style adapter
- encrypted opaque event batches
- fetch, dedupe, retry, and polling behavior

Validation:

- Micro tests for duplicate delivery.
- Micro tests for missed notification recovery.
- Micro tests for delayed delivery and polling catch-up.
- Scenario script for offline sender and offline receiver.
- Latency measurements for notification-driven and polling-only paths.

### `codex/dumb-relay-experiment`

Goal:
Prototype the smallest useful app-opaque relay.

Scope:

- relay accepts encrypted packets or frames
- relay does not know app event types
- Hub-to-Hub clients authenticate and exchange payloads
- relay can be replaced without changing app code

Validation:

- Micro tests for frame routing and duplicate handling.
- Multi-process local scenario with two Hubs and one relay.
- Simulated relay outage.
- Confirm that app code never contacts the relay directly.

### `codex/nat-traversal-spike`

Goal:
Determine whether an existing NAT traversal stack can fit cleanly behind the Hub abstraction.

Scope:

- one candidate stack, selected after the survey
- local and cross-network smoke tests if practical
- mode reporting into the draft transport abstraction

Validation:

- Record setup complexity.
- Record success and failure modes.
- Record fallback behavior when direct connectivity fails.
- Record metadata exposed to the rendezvous or traversal provider.

### `codex/live-transport-validation-harness`

Goal:
Build reusable local scenarios for transport behavior.

Scope:

- fake transport adapters
- deterministic delay
- duplicate delivery
- disconnect and reconnect
- relay unavailable
- notification missed
- mailbox polling catch-up

Validation:

- Micro tests run locally without internet communication.
- Scenario scripts are understandable enough to reuse in future app work.
- The harness can test a future chat reducer without depending on a chat app.

## Measurement Targets

Each experiment should try to report:

- median and worst-case delivery latency in the tested scenario
- whether delivery is ordered, unordered, or explicitly unordered
- duplicate behavior
- offline behavior
- reconnect behavior
- provider-visible metadata
- whether the service is generic or app-specific
- whether apps can remain unaware of the provider
- whether the Hub remains the only internet-facing component

## Mailbox Fallback Tolerance

The fallback path deserves special attention because it may be the only mode available for some teams.

Useful categories:

- **Feels live**
  - Usually under a few seconds.
  - Probably requires notifications to work well.
- **Feels responsive**
  - Several seconds to tens of seconds.
  - Acceptable for many small-team asynchronous workflows.
- **Feels delayed**
  - Polling-only or missed notifications.
  - Still useful for durable collaboration.
- **Not acceptable**
  - Voice, live cursors, games, pair programming, and other interactions that require continuous low latency.

The goal is not to pretend mailbox fallback is streaming.
The goal is to make degradation explicit and humane.

## Early Decision Posture

The current leaning is:

- Design for a layered transport model.
- Treat direct paths as an optimization, not a requirement.
- Treat relays as acceptable only when they are generic and app-opaque.
- Treat mailbox fallback as a first-class degraded mode, not an afterthought.
- Require the Hub to mediate every provider.
- Let apps adapt to the reported mode instead of assuming live delivery.

## Open Questions

- Is there one abstraction that cleanly covers direct, relayed, and mailbox modes?
- Should mailbox fallback use the same API as live transport, or a related but visibly different API?
- How much provider configuration belongs to the Manager versus the Hub?
- How should teams advertise supported live transport providers to each other?
- Can relay choice be per-team, per-device, or both?
- What is the minimum viable security envelope for opaque live payloads?
- Should live payloads be durable by default, or should durability always be an app-level choice?
- How should users understand "online" if transport quality is partial or asymmetric?
- Which first app should consume this after the experiments are informative enough?
