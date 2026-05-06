# Cloudflare Relay Experiment Plan

## Purpose

This experiment asks whether Cloudflare Realtime TURN is a good fit for Small Sea Live's managed relay path.

The narrow question:

> Can Cloudflare TURN serve as a Small Sea-compatible relay for live transport between authorized devices, without becoming a bespoke Small Sea service or exposing provider secrets to apps?

This experiment should not implement Small Sea Live.
It should validate provider fit, identify adapter requirements, and record the places where Cloudflare's model does or does not match Small Sea's Manager/Hub boundary.

Record experiment findings in `Experiments/CloudflareRelay/NOTES.md`.
Keep the notes factual enough that later provider experiments can compare against them.

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
- Do not test cross-provider TURN composability in this experiment.
- Do not build media calling.
- Do not treat Cloudflare as the canonical Small Sea relay provider.

## Phase 1: Docs-Only Fit Check

No Cloudflare account required.

Record:

- supported STUN/TURN endpoints and ports
- credential generation model
- maximum credential lifetime
- whether credentials can be refreshed during a WebRTC session
- pricing, free-tier shape, and concrete back-of-envelope cost models
- payload visibility claims for WebRTC data channels
- metadata visibility claims, including IPs, ports, timing, traffic volume, and candidate/relay information
- limits that matter to Small Sea, especially any lack of arbitrary TCP relay support
- a record format that future Twilio, Metered, Xirsys, or other TURN-provider surveys can reuse

Small Sea fit questions:

- Where does the long-lived provider key live?
- What does the Manager configure?
- What does the Hub mint at connection time?
- What, if anything, does an app ever learn about Cloudflare?
- Can the provider be replaced by another TURN service without changing app code?
- Which claims require a real smoke test?

Cost scenarios to model:

- low-rate cursor or activity-indicator updates for a 5-person team
- text-collaboration control traffic for a 5-person team
- a reliable-stream scenario with modest sustained throughput
- a clear "not modeled here" note for media workloads

## Phase 2: Account-Backed Smoke Test

Requires a Cloudflare account and TURN key.

Minimum scenario:

1. Generate short-lived TURN credentials locally from a Cloudflare TURN key.
2. Start two local WebRTC peers.
3. Exchange signaling locally, without introducing any Cloudflare Realtime product beyond TURN.
4. Force or verify use of a relayed TURN candidate.
5. Open a reliable ordered data channel.
6. Send payloads both directions.
7. Try an unordered/unreliable WebRTC data channel if the test stack supports it.
8. Record the selected ICE candidate pair and transport mode.
9. Record rough latency and throughput measurements.
10. Record failure behavior with missing, bad, expired, or revoked credentials.

Performance measurements:

- Sample end-to-end RTT for small payloads on the reliable data channel.
- Measure one modest sustained-throughput run through the reliable data channel.
- Record whether each measurement used a direct or relayed selected candidate pair.
- Treat the numbers as fit-check evidence, not as a benchmark suite.

Credential scenarios:

- **Cloudflare on both peers.** Both peers use short-lived TURN credentials minted from the same Cloudflare account.
- **One-sided provisioning.** One peer uses Cloudflare TURN credentials and the other peer uses no TURN credentials, if technically testable.
- **Bad or missing credentials.** One or both peers have missing, malformed, expired, or revoked credentials.

Sustained-session scenario:

- Keep a data channel open for roughly 30 minutes.
- Send periodic payloads during the session.
- Revoke, expire, or rotate credentials mid-session if Cloudflare and the test stack make that practical.
- Interrupt and restore the network path if practical.
- Record whether failure surfaces cleanly enough for the Hub to switch to mailbox-degraded behavior.

Questions to answer:

- Can one configured provider account support both sides of a peer connection?
- Can one configured provider account support a connection when only one peer has Cloudflare TURN credentials?
- Does the WebRTC stack expose enough candidate information for the Hub to report `direct` vs. `relayed`?
- Do reliable byte streams work through the relay in the shape Small Sea Live expects?
- Do unordered/unreliable WebRTC data channels work through the relay?
- Can credential refresh be tested without rebuilding the connection?
- Does failure surface cleanly enough for the Hub to fall back to mailbox-degraded mode?

Terminology note:
In this experiment, "datagram-like" means WebRTC data channels configured as unordered and/or unreliable.
It does not mean QUIC datagrams, raw UDP, or a final Small Sea Live datagram API.

## Phase 3: Account Setup And UX Notes

Record the Cloudflare account setup experience as an experiment deliverable, not just a side impression.

Record:

- exact steps to enable or find Cloudflare Realtime TURN
- exact steps to create or locate the long-lived TURN key
- whether a billing card or paid plan is required
- terminology that is likely to confuse non-specialist users
- screens or steps where the experimenter hesitated
- where the long-lived provider secret appears and how easy it would be to paste into a future Manager UI
- whether setup feels baseline-usable, default-live-candidate usable, or power-user-only

Do not build a Manager UI.
The output is a UX/account-setup assessment for future design.

## Phase 4: Small Sea Integration Notes

Translate the smoke-test results into architecture notes.

Expected outputs:

- `NOTES.md` in this directory with docs findings, smoke-test results, UX notes, cost estimates, and unresolved questions
- proposed Cloudflare TURN provider config fields
- long-lived secret handling rules
- short-lived credential minting flow
- required WebRTC runtime capabilities
- mode-reporting requirements
- capability-reporting requirements for events and reliable streams
- capability-reporting requirements for datagram-like behavior in the unordered/unreliable WebRTC data-channel sense
- metadata and privacy caveats
- UX notes for non-specialist account setup

## Follow-Up Work

Cross-provider TURN composability is explicitly out of scope for this experiment.

Future experiment:

> Test whether peers using different managed TURN providers, such as Cloudflare vs. Twilio, Metered, or Xirsys, compose cleanly in the Small Sea personal-egress model.

Questions for that future experiment:

- Can Alice's Hub use Cloudflare while Bob's Hub uses another provider?
- Does ICE select a usable path when peers advertise relay candidates from different vendors?
- Does provider heterogeneity affect mode reporting, credential refresh, or failure behavior?
- Does cross-provider behavior preserve the "no matched-membership provider" goal?

## Validation Standard

This experiment should convince a skeptical reviewer that:

- Cloudflare TURN either can or cannot fit the Small Sea provider philosophy.
- The app never needs direct Cloudflare credentials.
- The Hub can mediate use of the provider.
- One-sided Cloudflare provisioning is either validated, rejected, or marked not technically testable with reasons.
- Cloudflare does not become a durable source of truth, identity authority, or app-specific service.
- The relay path can be replaced or disabled without losing durable collaboration.
- Payload visibility and metadata visibility are documented separately.
- Rough latency and throughput numbers are recorded well enough to inform baseline-vs-power-user classification.
- The setup UX is documented well enough to classify Cloudflare as baseline, default-live-candidate, or power-user.
- The limits of the provider are documented clearly enough to guide future implementation.

## Questions This Experiment Is Designed To Answer

- Is Cloudflare TURN a baseline managed relay candidate or only a power-user/provider option?
- Is one-sided Cloudflare provisioning real in practice, or do both participants need TURN credentials?
- Does Cloudflare's credential model work cleanly for sibling devices and teammate devices?
- How should a Hub decide between direct, relayed, and mailbox-degraded paths?
- What additional local-only micro tests would a future adapter need?

## References To Collect

- Cloudflare Realtime TURN overview
- Cloudflare TURN credential generation docs
- Cloudflare Realtime pricing
- Cloudflare TURN FAQ, especially encryption and metadata claims
- WebRTC ICE candidate and selected candidate pair documentation
