# Word of Mouth

**Status:** concept-stage app. This package currently contains only enough
metadata to be a valid workspace member; it does not implement the app yet.

Word of Mouth is a Small Sea reinterpretation of public social media. The core
idea is that posts do not broadcast directly to the world. They travel from team
to team because someone in one team deliberately carries a post to another team.
As the post moves, it accumulates a signed path of social handoffs.

That path matters:

- It makes propagation socially meaningful. A person thought the post was worth
  bringing to a particular team's attention.
- It gives recipients context for why the post reached them.
- It creates a verifiable path back toward the originator without requiring a
  central platform, global account registry, or universal feed index.

The product question is whether this can feel less like shouting into a crowd
and more like hearing, "A friend of a friend thought you should see this."

## The Shape

Word of Mouth lives between private team chat and broadcast social media.

Inside one Small Sea team, a post is ordinary app data in that team's Word of
Mouth berth. If a member wants another team to see it, they re-share it into
that other team's berth. The receiving team can discuss it, ignore it, annotate
it, or relay it onward. Each relay adds a signed propagation hop.

The intended social units are existing Small Sea teams: families, projects,
clubs, neighborhood groups, small professional circles, and other groups where
membership has some real-world meaning. A team is not a hashtag, subreddit,
server, or algorithmic audience segment. It is a group of people with a shared
local history and a reason to trust each other at least a little.

## Core Loop

1. Alice writes a post in Team A.
2. Bob, also in Team A, thinks Team B should see it.
3. Bob relays the post into Team B with an optional note explaining why.
4. Team B sees the post with provenance: original author, origin team if
   visible, Bob's relay act, and the prior signed path that Bob is allowed to
   reveal.
5. Someone in Team B may relay it onward, extending the path.

The interesting object is not only the post. It is the post plus the chain of
human decisions that caused it to arrive.

## What This Is Not

- It is not a global Twitter/X clone.
- It is not a firehose protocol.
- It is not a popularity contest built around follower counts.
- It is not a generic ActivityPub server.
- It is not a replacement for private team chat.
- It is not a moderation-free public square.

Small Sea is designed for human-scale coordination. Word of Mouth should lean
into that instead of trying to smuggle internet-scale social media into the
repo under a friendlier name.

## Why Small Sea Might Be Good At This

Word of Mouth needs pieces Small Sea already cares about:

- **Local-first storage:** teams keep their own copy of the conversations that
  matter to them.
- **Signed identity:** posts and relay acts can be attributed to concrete Small
  Sea identities and devices.
- **Team-scoped sharing:** distribution follows social groups rather than
  global platform accounts.
- **Hub-mediated communication:** the app should never talk directly to storage,
  notification services, peers, or relays. It asks the local Hub.
- **Human repair over false certainty:** if two histories, identities, or paths
  conflict, preserve the ambiguity and show it to people.

## Comparison Points

These projects are the most useful mirrors. The point is not to copy one; it is
to know which prior art is pulling on the design.

The best first comparisons are Secure Scuttlebutt and Briar, because both take
social trust, local storage, and peer-to-peer propagation seriously. Nostr,
ActivityPub, AT Protocol, and Farcaster are still important, but mostly as
contrast cases: they show how quickly "social protocol" turns into public
infrastructure, global identity, firehoses, and moderation pressure.

### Secure Scuttlebutt

[Secure Scuttlebutt](https://scuttlebot.io/more/protocols/secure-scuttlebutt.html)
is the closest spiritual comparison. It uses signed append-only feeds,
local storage, peer-to-peer replication, and social discovery through followed
users and friends-of-friends.

Borrow:

- Signed local-first social data.
- Socially bounded replication instead of universal indexing.
- Web-of-trust instincts around identity.

Reject or rethink:

- Word of Mouth should be team-mediated, not primarily individual-follow
  mediated.
- A global-ish gossip network of individual feeds is probably too broad for the
  Small Sea model.
- Indefinite replication of everything a friend-of-a-friend posts may leak too
  much social context.

### Nostr

[Nostr](https://nips.nostr.com/1) is useful because it is radically simple:
signed events are published to relays, and clients subscribe by filters.

Borrow:

- A small, inspectable signed event envelope.
- The idea that relays can be dumb transport/storage infrastructure.
- Cryptographic event identity.

Reject or rethink:

- Public relays are the wrong trust and privacy shape for Small Sea.
- Nostr's global key identity is too account-centric for team-mediated context.
- Relay shopping should not bypass the local Hub.

### ActivityPub and Mastodon

[ActivityPub](https://www.w3.org/TR/activitypub/) is the standard comparison
for federated social networking: actors publish to outboxes and servers deliver
activities to inboxes.

Borrow:

- Clear verbs for social actions: create, announce, reply, like, delete, block.
- Separation between client-to-server and server-to-server concerns.
- Lessons from Mastodon moderation and federation failure modes.

Reject or rethink:

- Server-instance identity is not the right primitive; Small Sea teams are.
- Inbox delivery to arbitrary remote actors is broader than the desired first
  slice.
- Public web addressing creates discovery, moderation, and takedown pressures
  that Word of Mouth may not want.

### Bluesky AT Protocol

[AT Protocol](https://docs.bsky.app/docs/advanced-guides/atproto) is a strong
comparison for portable identity, signed repositories, and separable app views.

Borrow:

- Signed data repositories as a durable user-data substrate.
- The distinction between raw protocol data and app-specific views.
- Account portability as a product value.

Reject or rethink:

- AT Protocol is built for large public networks; Word of Mouth is not.
- Big relays and firehose services would distort the social relay premise.
- Domain-based public identity is not the same as Small Sea team membership.

### Briar

[Briar](https://briarproject.org/how-it-works/) is a strong comparison for
resilient peer-to-peer communication under surveillance and unreliable internet.
It also supports forums and blogs synchronized directly between users.

Borrow:

- Direct, encrypted, device-to-device instincts.
- Offline and intermittent connectivity as normal.
- Careful thinking about metadata surveillance and social-graph exposure.

Reject or rethink:

- Word of Mouth is not primarily a crisis-messaging app.
- Small Sea already routes communication through the Hub abstraction, so Word of
  Mouth should not choose transports directly.
- Team-to-team relay is a different social object than contact-to-contact sync.

### Farcaster

[Farcaster](https://docs.neynar.com/farcaster/learn/what-is-farcaster/messages)
is useful as a contrast case: signed social messages propagate through Hubs and
eventually form a public social graph.

Borrow:

- Compact signed message types.
- Separation between custody identity and app signing keys.
- The idea that many clients can read the same social substrate.

Reject or rethink:

- Public global graph replication is not a Small Sea goal.
- Blockchain-backed identity and storage quotas are unnecessary here.
- "Hub" means something different in Farcaster; in Small Sea, the Hub is the
  local gateway and policy boundary.

## Challenging Questions

These are not polish questions. The answers could change the first real
implementation.

1. **Is the product actually "Word of Mouth" or "Word or Mouth"?**
   The package and README currently use "Word of Mouth." If the pun is meant to
   be "Word or Mouth," decide early because naming will shape the tone.
2. **Who consents to a relay?**
   If Alice posts inside Team A, can Bob carry it to Team B by default? Does
   Alice need to mark the post as relayable? Can Team A set a norm that posts
   are local-only unless explicitly released?
3. **How much path is visible?**
   A full signed path is useful provenance, but it may reveal private team names,
   memberships, relationships, and political/social affiliations. Should relays
   be able to redact intermediate team names while preserving verifiability?
4. **What does deletion mean?**
   Once a post has crossed team boundaries, revocation cannot be magic. Is
   deletion a tombstone, a request, a key rotation, a local moderation act, or
   all of those in different layers?
5. **What prevents laundering?**
   A harmful post could gain legitimacy by passing through a respected team. Do
   recipients need to see each relay's annotation and dissent, not just the path?
6. **What is the anti-spam primitive?**
   The obvious answer is social scarcity: only team members can relay into their
   teams. Is that enough, or do teams need quotas, inbox review, relay allowlists,
   or quarantine states?
7. **Can a team refuse a relay before it lands?**
   If Team B gets a post because Bob is a member, does it appear immediately, or
   does it enter a review queue? Is the answer different for read-write members
   and observers?
8. **What counts as authorship?**
   A relay note can change meaning dramatically. Is a relay-with-comment a new
   post that cites the old one, or the same post with attached commentary?
9. **Can provenance become a popularity metric?**
   If the UI counts hops, teams, or "distance from origin," it may recreate the
   attention games this app is trying to avoid.
10. **What is the first niche where this is clearly better?**
    Possibilities: mutual-aid updates, local recommendations, small professional
    referrals, neighborhood alerts, event invitations, reading-group discoveries,
    or trusted classifieds. The first slice should pick one.
11. **Can teams survive being routing infrastructure?**
    If a team becomes valuable as a bridge to another audience, social pressure
    may change the group itself. Does the app need per-topic relay channels so
    the family chat does not become a news distribution hub?
12. **How does a recipient know "why me"?**
    A signed path answers where the post came from. It does not necessarily
    answer why it reached this team. Relay notes may be mandatory.

## First Data Model Sketch

This is intentionally provisional.

- `post`: author-signed content, origin app/team context, content hash,
  creation time, optional relay policy, optional attachments.
- `relay`: signer, source post hash, source path hash, destination team berth,
  relay note, timestamp, and signature.
- `path`: ordered relay entries, each signed by the relay actor and verifiable
  against the previous path hash.
- `local_moderation`: team-local hide, pin, annotate, quarantine, or block
  decisions. These are not global truth.
- `receipt`: local record that a team saw a post/path, useful for dedupe and
  future sync.

The important design constraint is that app-visible session and team identity
must come through the Hub API, not direct reads from Manager databases.

## Possible First Slice

A useful first implementation slice, when the time comes, might be:

1. Create a post in one team.
2. Relay it into a second team where the same local sandbox has membership.
3. Verify the relay path locally.
4. Show both teams' local copies with different local moderation state.
5. Keep all communication local or mocked in micro tests.

That slice avoids public discovery, cross-device sync weirdness, global search,
and full moderation policy while still testing the unique idea: signed
team-to-team propagation.

## Micro Test Ideas

When real code starts, the first micro tests should make the social/protocol
boundary hard to blur:

- A relay path verifies when each hop signs the previous path hash.
- Tampering with a prior hop invalidates later path verification.
- A post marked local-only cannot be relayed by the app.
- A recipient team can hide or quarantine a post without mutating the origin
  post or the signed relay path.
- The app obtains session/team information via the Hub API only.
- Tests use local fixtures or mocked Hub services, never internet services.

## Open Product Bets

The app is worth pursuing only if at least one of these bets is true:

- People want social discovery through accountable human relays, not opaque
  recommendation systems.
- The extra friction of relay notes and team-scoped sharing improves quality
  more than it reduces participation.
- Provenance is valuable even when partial or privacy-preserving.
- Small groups can be bridges without being consumed by the dynamics of public
  platforms.

If those bets are false, Word of Mouth should stay a sketch rather than becoming
another social feed with better cryptography and the same old problems.
