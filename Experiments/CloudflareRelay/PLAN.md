# Cloudflare Relay Experiment Plan

## Purpose

This experiment asks whether Cloudflare Realtime TURN is a good fit for Small Sea Live's managed relay path.

The narrow question:

> Can Cloudflare TURN serve as a Small Sea-compatible relay for live transport between authorized devices, without becoming a bespoke Small Sea service or exposing provider secrets to apps?

This experiment should not implement Small Sea Live.
It should validate provider fit, identify adapter requirements, and record the places where Cloudflare's model does or does not match Small Sea's Manager/Hub boundary.

## Background

Small Sea itself provides no internet services.
The Hub must build live communication on top of services and transports that users can choose, subscribe to, or operate.

Cloudflare Realtime TURN is interesting because it appears to offer:

- managed STUN/TURN infrastructure
- UDP, TCP, and TLS TURN endpoints on firewall-friendly ports
- short-lived TURN credentials generated from a long-lived TURN key
- ordinary Cloudflare account billing rather than a bespoke Small Sea backend
- WebRTC-compatible relay behavior for data channels

The important translation:

- Cloudflare docs say the long-lived TURN key must stay "server-side."
- In Small Sea, that means the key belongs in the Manager/Hub-controlled configuration path.
- Apps must never see the long-lived provider key.
- Apps should receive only Hub-mediated live transport capability.

## Non-Goals

- Do not build the final Small Sea Live adapter.
- Do not build a user-facing Manager configuration UI.
- Do not compare every TURN provider in depth.
- Do not build media calling.
- Do not treat Cloudflare as the canonical Small Sea relay provider.

## Phase 1: Docs-Only Fit Check

No Cloudflare account required.

Record:

- supported STUN/TURN endpoints and ports
- credential generation model
- maximum credential lifetime
- whether credentials can be refreshed during a WebRTC session
- pricing and free-tier shape
- provider-visible metadata
- encryption claims for WebRTC data channels
- limits that matter to Small Sea, especially any lack of arbitrary TCP relay support

Small Sea fit questions:

- Where does the long-lived provider key live?
- What does the Manager configure?
- What does the Hub mint at connection time?
- What, if anything, does an app ever learn about Cloudflare?
- Can the provider be replaced by another TURN service without changing app code?
- Which claims require a real smoke test?

## Phase 2: Account-Backed Smoke Test

Requires a Cloudflare account and TURN key.

Minimum scenario:

1. Generate short-lived TURN credentials locally from a Cloudflare TURN key.
2. Start two local WebRTC peers.
3. Exchange signaling locally, without a Cloudflare signaling service.
4. Force or verify use of a relayed TURN candidate.
5. Open a reliable ordered data channel.
6. Send payloads both directions.
7. Try an unordered/unreliable data channel if the test stack supports it.
8. Record the selected ICE candidate pair and transport mode.
9. Record failure behavior with missing, bad, expired, or revoked credentials.

Questions to answer:

- Can one configured provider account support both sides of a peer connection?
- Does the WebRTC stack expose enough candidate information for the Hub to report `direct` vs. `relayed`?
- Do reliable byte streams work through the relay in the shape Small Sea Live expects?
- Do unreliable datagram-like data channels work through the relay?
- Can credential refresh be tested without rebuilding the connection?
- Does failure surface cleanly enough for the Hub to fall back to mailbox-degraded mode?

## Phase 3: Small Sea Integration Notes

Translate the smoke-test results into architecture notes.

Expected outputs:

- proposed Cloudflare TURN provider config fields
- long-lived secret handling rules
- short-lived credential minting flow
- required WebRTC runtime capabilities
- mode-reporting requirements
- capability-reporting requirements for events, reliable streams, and datagrams
- metadata and privacy caveats
- UX notes for non-specialist account setup

## Validation Standard

This experiment should convince a skeptical reviewer that:

- Cloudflare TURN either can or cannot fit the Small Sea provider philosophy.
- The app never needs direct Cloudflare credentials.
- The Hub can mediate use of the provider.
- Cloudflare does not become a durable source of truth, identity authority, or app-specific service.
- The relay path can be replaced or disabled without losing durable collaboration.
- The limits of the provider are documented clearly enough to guide future implementation.

## Open Questions

- Is Cloudflare TURN a baseline managed relay candidate or only a power-user/provider option?
- Is one-sided provider provisioning real in practice, or do both participants need separately configured provider credentials?
- Does Cloudflare's credential model work cleanly for sibling devices and teammate devices?
- How should a Hub decide between direct, relayed, and mailbox-degraded paths?
- What setup steps would be confusing or scary for a non-specialist user?
- What additional local-only micro tests would a future adapter need?

## References To Collect

- Cloudflare Realtime TURN overview
- Cloudflare TURN credential generation docs
- Cloudflare Realtime pricing
- Cloudflare TURN FAQ, especially encryption and metadata claims
- WebRTC ICE candidate and selected candidate pair documentation
