# Cloudflare Relay Experiment Notes

This file records the results of the Cloudflare Relay experiment.
Keep entries factual and specific enough that later TURN-provider experiments can compare against them.

## Setup Log

- Date: 2026-05-06
- Experimenter: Codex
- Cloudflare account state: not used for docs-only pass
- Billing requirement: not verified through account setup

## Docs-Only Findings

Sources checked:

- Cloudflare Realtime TURN overview: https://developers.cloudflare.com/realtime/turn/
- Cloudflare TURN credential generation: https://developers.cloudflare.com/realtime/turn/generate-credentials/
- Cloudflare TURN FAQ: https://developers.cloudflare.com/realtime/turn/faq/
- Cloudflare Realtime pricing: https://developers.cloudflare.com/realtime/pricing/

Cloudflare Realtime TURN is documented as a managed TURN service separate from Cloudflare's SFU.
It is meant to relay WebRTC traffic when direct communication is blocked by NATs or firewalls.

Supported endpoints:

- STUN over UDP at `stun.cloudflare.com:3478` and alternate port `53`.
- TURN over UDP at `turn.cloudflare.com:3478` and alternate port `53`.
- TURN over TCP at `turn.cloudflare.com:3478` and alternate port `80`.
- TURN over TLS at `turn.cloudflare.com:5349` and alternate port `443`.

Cloudflare warns that alternate port `53` is often blocked by ISPs and browsers.
For Small Sea, port `53` should probably be treated as a last-choice candidate and possibly filtered in browser tests if it causes non-trickle ICE delays.

Credential model:

- A Cloudflare TURN key is a long-lived secret.
- The TURN key cannot be used directly as a TURN credential.
- The TURN key can create short-lived TURN credentials with an explicit TTL.
- The credential generation API returns a WebRTC `iceServers` object.
- Cloudflare says the long-lived TURN key should stay server-side.

Small Sea translation:

- The Manager should own provider account configuration.
- The Hub should use that configuration to mint short-lived TURN credentials.
- Apps should not receive the long-lived Cloudflare TURN key or the Cloudflare API token.
- Whether apps ever see provider-derived material like `iceServers` at all is an open Small Sea Live design question, not a Cloudflare question.
  Promoting WebRTC-specific concepts into the app-facing surface would work against the goal of a generic live-transport layer.
  See Unresolved Questions.
  Cloudflare TURN fits either way; this experiment does not need to choose.

Credential lifetime and refresh:

- TURN credentials can expire up to 48 hours in the future.
- Longer allocations require generating new credentials at least every 48 hours.
- Cloudflare says WebRTC credentials can be refreshed during a session with `RTCPeerConnection.setConfiguration()`.
- Cloudflare says expired in-use credentials stop billing and analytics immediately, then disconnect after a short delay.

The `setConfiguration()` refresh path is a meaningful fit-check positive.
Cloudflare's docs suggest long-lived Small Sea Live sessions may be able to rotate Cloudflare credentials without rebuilding the WebRTC connection.
That still needs to be verified in the account-backed smoke test, especially around existing TURN allocations and credential expiry.
The shape matches the pattern already in the experiments doc: long-lived provider config rides over the sync layer, while short-lived per-session credentials get minted on connect.

Limits and caveats:

- Cloudflare documents per-allocation packet-rate limits around `5-10 kpps`.
- Cloudflare documents per-allocation data-rate limits around `50-100 Mbps`.
- Cloudflare documents per-allocation new-destination behavior around `>5 new IP/sec`.
- Hitting these limits may result in packet drops.
- Cloudflare Realtime TURN does not implement RFC6062 TCP relaying.
  This rules out using Cloudflare to relay arbitrary TCP connections between peers.
  It does not affect WebRTC-over-TURN-via-TCP-transport, which is the path Small Sea Live actually needs.
- Cloudflare supports TURN-client-to-TURN-server communication over IPv4 and IPv6, but relayed addresses are IPv4 only.
- Cloudflare recommends ICE restart support because allocations can be disrupted by maintenance or network topology changes.

Docs-only fit:

- Cloudflare looks plausible as a managed TURN candidate for Small Sea Live.
- The docs support the desired Manager/Hub/app secret boundary.
- The docs do not answer one-sided provisioning.
- The docs do not answer actual latency, throughput, candidate selection, or failure-surface behavior.
- Those questions need the account-backed smoke test.

## Cost Model

Cloudflare prices TURN by data sent from Cloudflare edge to the TURN client.
Cloudflare says STUN is free and unlimited.
Realtime TURN costs `$0.05/GB` after a shared Realtime free tier of `1,000 GB`.
The free tier is shared across Realtime TURN and SFU, not separate per service.

The estimates below count application payload bytes sent from the relay to receivers.
Actual billed traffic includes TURN overhead, so these are optimistic lower bounds.

Scenario: cursor or activity-indicator updates for a 5-person team.
Assumption: each of 5 devices sends 5 small events/sec, each event is 100 bytes, and each event fans out to the other 4 devices.
That is about `10 KB/sec`, `36 MB/hour`, and `2.2 GB/month` at 2 active hours/day for 30 days.
Nominal cost after the free tier would be about `$0.11/month`.

Scenario: text-collaboration control traffic for a 5-person team.
Assumption: the team produces 20 events/sec total, each event is 1 KB, and each event fans out to 4 receivers.
That is about `80 KB/sec`, `288 MB/hour`, and `17.3 GB/month` at 2 active hours/day for 30 days.
Nominal cost after the free tier would be about `$0.86/month`.

Scenario: modest sustained reliable-stream traffic.
Assumption: one relayed stream sends `1 Mbps` of payload to one receiver for 2 active hours/day for 30 days.
That is about `450 MB/hour` and `27 GB/month`.
Nominal cost after the free tier would be about `$1.35/month`.

Across all three scenarios, traffic stays well inside Cloudflare's currently documented 1000 GB shared free tier.
The nominal-cost numbers above are what teams would pay if no free tier existed.
They should be read as rough ceilings for these traffic assumptions, not expected bills.
Account setup still needs to verify billing-card requirements, plan requirements, and whether other Realtime usage shares the same free-tier pool.

Media workloads are not modeled here.
They could easily dominate traffic, and Small Sea Live is not trying to provide media semantics in this experiment.

## Privacy And Metadata

Payload visibility:

- Cloudflare says that when Realtime TURN is used with WebRTC, Cloudflare cannot access relayed payloads because WebRTC encrypts them end-to-end (DTLS for data channels, SRTP keyed by DTLS for media) before they reach the TURN server.
- Cloudflare's statement explicitly includes audio, video, and data-channel information.
- This supports the Small Sea assumption that app payloads sent over WebRTC data channels are opaque to Cloudflare.

Metadata visibility:

- TURN necessarily exposes operational metadata to the relay provider.
- At minimum, Cloudflare can observe relay allocations, credential use, client IPs, ports, timing, traffic volume, and packet-rate/data-rate behavior.
- Cloudflare analytics can report TURN usage, and the FAQ says TURN usage appears in analytics after about 30 seconds.
- Cloudflare also necessarily knows whether traffic is using Cloudflare TURN rather than a direct peer path.

Small Sea implication:

- Cloudflare TURN does not appear to violate app-payload privacy when used through WebRTC data channels.
- It does expose who is using the relay, when, and roughly how much traffic is moving.
- Small Sea docs should avoid equating "encrypted payload" with "private relationship metadata."

## Account Setup UX

Record exact setup steps, confusing screens, terminology, and where the long-lived TURN key appears.

Docs-only note:
Cloudflare says TURN keys can be created in the Dashboard or through the API.
The FAQ refers to self-serve plans that can be paid by credit card and says self-serve and enterprise plans do not differ in TURN performance or features.
The actual account setup flow, billing-card requirement, and confusing screens still need a human account walkthrough.

Creating a TURN key seemed to require entering a credit card.
It showed that it was charging $0 (until you use a lot, I guess), but you need the card.
Navigating the dashboard menus was a little confusing.
Ended up using the search feature and searched for TURN.

things that might be secret in the following have been replaced by [NAME]:

curl -H "Authorization: Bearer [API Token]" -H "Content-Type: application/json" -d '{"ttl": 86400}' https://rtc.live.cloudflare.com/v1/turn/keys/[Turn Token ID]/credentials/generate-ice-servers

{"iceServers":[{"urls":["stun:stun.cloudflare.com:3478","stun:stun.cloudflare.com:53"]},{"urls":["turn:turn.cloudflare.com:3478?transport=udp","turn:turn.cloudflare.com:3478?transport=tcp","turns:turn.cloudflare.com:5349?transport=tcp","turn:turn.cloudflare.com:53?transport=udp","turn:turn.cloudflare.com:80?transport=tcp","turns:turn.cloudflare.com:443?transport=tcp"],"username":"[Big random number]","credential":"[Big random number]"}]}


## Smoke-Test Results

Record account-backed WebRTC/TURN results here.
Include selected ICE candidate pairs and whether each tested path was direct or relayed.

## Performance Measurements

Record rough RTT samples and sustained-throughput measurements here.
Include whether each measurement used a direct or relayed selected candidate pair.

## One-Sided Provisioning

Record whether one peer can use Cloudflare TURN credentials while the other peer has no TURN credentials.

## Sustained Session

Record the roughly 30-minute session result and any credential, network, or fallback behavior observed.

## Integration Notes

Record proposed Manager configuration fields, Hub credential-minting behavior, capability reporting, and fallback implications.

## Unresolved Questions

- Open architectural question for small-sea-live, not for this experiment: does the app or the Hub act as the WebRTC peer? If the Hub does, apps see send/receive verbs and never touch ice-servers material. If the app does, the Hub passes ice-servers down for the app to configure its own peer connection. Cloudflare TURN works for either model; the choice belongs to the small-sea-live package, and notes here should be careful not to drift toward one option just because the provider docs are written for browser-side WebRTC.
