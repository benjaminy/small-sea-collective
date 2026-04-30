# The Hedgerow

**Status:** concept-stage app. This package currently contains only enough
metadata to be a valid workspace member; it does not implement the app
yet. "The Hedgerow" is a working name (replacing an earlier placeholder,
"Word of Mouth") pending namespace and trademark collision checks; see
Challenging Question 2. A few load-bearing product hypotheses have firmed
up alongside the name; they are listed under "Current Product Shape"
below. They are stronger than loose brainstorms, but weaker than final
requirements. Where the naming discussion exposed real design tensions,
this document preserves the tension rather than pretending it has been
settled.

The Hedgerow is a Small Sea answer to public social media's discovery
problem. It is the *beyond-one-team* app: explicitly about carrying things
out of one team context and into another. Other Small Sea apps cover private
and intimate communication; this one is not trying to. But "beyond one team"
does not mean "to the whole world." The core idea is that posts do not
broadcast directly. They travel from team to team because someone with
standing in two teams deliberately carries a post between them. As the post
moves, it accumulates a signed path of social handoffs.

That path matters:

- It makes propagation socially meaningful. A specific person, in a specific
  pair of teams, decided this should cross.
- It gives recipients context for why the post reached them and through whom.
- It creates a verifiable path back toward the originator without requiring a
  central platform, global account registry, or universal feed index.

The structure is bipartite: people on one side, teams on the other. The
social act that matters is one person — with standing in two teams —
choosing to carry something across. That is not a "friend of a friend"
graph. It is a membership-overlap bridge, and the carrier's standing in
*both* team contexts is what gives the relay weight.

The product question is whether the experience can feel less like shouting
into a crowd and more like, "Someone in our book club brought this in from
their kid's school parents group." Note what that sentence does not say: it
claims no friendship and asserts no graph distance. It names two contexts
and the person who bridged them. That is the social object The Hedgerow
tries to make first-class.

## The Metaphor

A hedgerow is the dense edge ecosystem that sits between cultivated plots:
old, organic, hosting disproportionate biodiversity, and quietly doing the
work of connecting otherwise separate patches of habitat. A team's working
space is an **allotment** — the cultivated plot. The Hedgerow is what
surrounds, separates, and connects the allotments. Things that matter
across more than one allotment travel through the hedgerow, because
someone with standing in two allotments deliberately carries them across.
The point is not just transit. Like ecological edges, hedgerows keep
neighboring communities from becoming sealed monocultures: they are where
outside ideas, weak ties, and local surprises can cross without turning
every field into the same field.

The definite article ("*The* Hedgerow") gestures at a single emergent
fabric made of many overlapping team-allotments, in the same shape "The
Internet" gestures at a single thing made of many local networks. The
metaphor lines up with the engineering: every relay is a signed,
intentional, *bridging* act, not a broadcast.

One important caveat sharpened the metaphor: a hedgerow is a boundary,
not a room. That would be awkward if the product were framed as "each
team has its own Hedgerow." The current leaning is different: The Hedgerow
is the mixed edge habitat where a person sees items from all the team
contexts they are entitled to see, with provenance made explicit. Teams
remain the permission and meaning contexts; they are not the primary
navigation rooms. This is a design hypothesis, not a settled UI
specification.

## Current Product Shape

Four organizing hypotheses have firmed up alongside the name. The rest of
this document refines them, challenges them, and records open edges.
Specific UI/UX choices in later sections — vocabulary, provenance
rendering, notification model, feed ranking — remain exploratory and will
be settled later.

1. **The Hedgerow's job is the connective tissue between teams**, not
   the inside of any single team. Team-internal chat, files, structured
   data, and notes belong in other Small Sea apps. The Hedgerow is the
   *between*. Naming the connective system rather than any team's space
   is the same move "The Internet" made; it puts the product's emphasis
   on the value it uniquely creates.

2. **The primary user surface is a single mixed feed**, not per-team
   channels. Posts from many teams arrive in one place. Provenance —
   which team a post originated in, who carried it onward, how it
   reached this user — is rendered prominently on every post. Teams
   stay visible in the UI, but as origin/destination tags and filters,
   not as channels you navigate into.

3. **Every propagation is a deliberate human act.** Posts do not
   auto-broadcast across overlapping memberships, even when the
   membership graph would technically permit it. A person with standing
   in two teams must choose to carry a post from one to the other. The
   Hedgerow is meant to be a slow place with a high signal-to-noise
   ratio: each relay is someone deciding that the post is worth a
   team's attention.

4. **There should probably be no escape hatch.** The Hedgerow has no DMs,
   no private team mode, and no "post only to my team" flag. Content meant
   to stay inside a single team lives in a different Small Sea app —
   likely the in-team chat-equivalent, not here. Keeping a single, focused
   job for The Hedgerow is what prevents it from collapsing back into
   "group chat with a forward button."

## Sacred Invariant

The central primitive is a **membership-overlap bridge**:

> A person who is a member of Team A and Team B signs an intentional act of
> carrying a relayable artifact from Team A into Team B.

That bridge is the atom. Not follower edges. Not friends-of-friends. Not
server federation. Not algorithmic recommendations. A longer path is just a
chain of these overlap bridges, where each hop is locally meaningful because
the carrier has standing in the source context and the destination context.

This gives the app a different social geometry from ordinary social
networking:

- **People are carriers, not audiences.** The important action is not that
  Bob follows Alice; it is that Bob belongs to two teams and chooses to spend
  some of his standing in one team by bringing something from another.
- **Teams are contexts, not channels.** A team is not a topic subscription
  or feed bucket. It is the social setting that makes a bridge
  intelligible. In The Hedgerow specifically, teams do not appear as
  navigable rooms; they are origin and destination tags on every relay.
- **Edges are deliberate, not ambient.** The app should not infer a relay
  because two teams overlap. The overlap creates the possibility; the signed
  carrying act creates the propagation.
- **Local meaning beats global reach.** A relay that matters to one receiving
  team is more successful than a relay that spreads widely but loses the
  reason it was carried.

## The Shape

The Hedgerow lives between private team chat and broadcast social media.

Inside one Small Sea team, a post is ordinary app data — most of it
authored in other Small Sea apps, not here. When a member with standing
in another team decides the post should cross, they relay it: a signed
carrying act that takes the post from the source team into the
destination team's Hedgerow-visible set, where it appears in the mixed
feeds of people entitled to see that team context. The receiving team can
engage with it, ignore it, annotate it, or relay it onward. Each relay
adds a signed propagation hop.

The intended social units are existing Small Sea teams: families, projects,
clubs, neighborhood groups, small professional circles, and other groups
where membership has some real-world meaning. A team is not a hashtag,
subreddit, server, or algorithmic audience segment. It is a group of people
with a shared local history and a reason to trust each other at least a
little.

The interesting object is not the post itself but the *carrying act*. The
thing being carried may eventually be many kinds of Small Sea content — a
doc, a photo, a thread excerpt, a chat message — but only after it has been
turned into a **relayable artifact** by the source app/team policy. The
Hedgerow should not become a generic permission bypass for arbitrary
content in another berth. The thing The Hedgerow uniquely creates is the relay: the
signed, annotated act of carrying that artifact into a new team where the
carrier has standing. Designing around the relay (not the post) is what
keeps this from collapsing into "group chat with a forward button."

## Core Loop

1. Alice writes a post in Team A.
2. Bob, also in Team A and a member of Team B, thinks Team B should see it.
3. Bob relays the post into Team B with a note explaining why.
4. Team B sees the post with provenance: original author, origin team,
   Bob's relay act, and at least enough signed path context to verify the
   membership-overlap bridge that brought it here.
5. Someone in Team B may relay it onward, extending the path.

The interesting object is not only the post. It is the post plus the chain
of human decisions that caused it to arrive — and Bob, who is staking his
standing in Team B on bringing it.

## Open UI/UX Ideas

These are exploratory. The product-shape hypotheses above are the current
strong leanings; the specifics below are not commitments, and several
have known tensions and tradeoffs that need a real design pass.

### Vocabulary candidates

The hedgerow metaphor has obvious verbs and nouns; some may end up in the
product copy, some may stay metaphor-only. Whether to lean hard into the
ecological vocabulary or treat it as background tone is itself a design
choice.

- **Plant**, **sow**, **tuck**, **leave** — author or place a post
  directly into The Hedgerow. "Sow" is the strongest ecological verb;
  "plant" and "leave" are plainer; "tuck" has a local, note-in-a-wall
  feel that may be too cute or may be exactly right.
- **Pollinate** (or *carry*, *relay*, *pass along*, *bring through*) —
  bring a post across a membership-overlap bridge into another team.
  "Relay" stays the protocol word; "carry" is likely the clearest
  human-facing verb.
- **Cutting** — a relayable thing carried into a new context while
  preserving its origin. This is thematically rich, but it may imply
  mutability or propagation-by-cloning more strongly than the protocol
  should.
- **Stile** — the connection point a carrier crosses; possibly an
  in-product affordance ("met them at a stile").
- **Gap**, **gate**, **crossing** — plainer alternatives to "stile."
  "Gap" can sound like a security flaw; "gate" can sound like approval
  bureaucracy; "crossing" is broadly legible.
- **Pleach**, **quickset** — beautiful hedge-specific words that may be
  useful as internal language but are probably too obscure for primary
  UI copy.
- **Allotment** — a team's space when the metaphor is being surfaced; in
  most UI text, "team" is probably still the user-facing word.
- **Forage**, **glean**, **browse** — reading verbs.
- **Tend**, **nest**, **roost** — staying-with-a-thread verbs.

The protocol-level word stays **relay** (a signed, verifiable propagation
hop). User-facing copy is open.

### Mixed feed vs. team-first surface

Earlier sketches foregrounded teams the way most Small Sea thinking does:
pick a team, then read that team's space. The Hedgerow name makes a
different surface plausible. If the hedgerow is the living boundary
between cultivated plots, the primary experience may be a single mixed
feed across all of the user's relevant teams: "walk the edge where my
worlds overlap."

That shift solves one metaphor problem and creates several product
questions. It solves the awkwardness of "our Hedgerow" by making The
Hedgerow the connective system rather than any team's own room. It raises
new questions about discussion context, moderation ownership, and whether
a mixed feed can avoid becoming ordinary social media with better
provenance. Current leaning: posts arrive in a personal mixed feed, while
replies and local moderation still happen in a specific team context.
That split needs design proof.

### Provenance rendering

"Easy to see who said this and where it came from" is a baseline for
every post. The harder question is *how much path* to show. Floor:
original author, origin team, last carrier, destination team. Open:
whether to render the full upstream chain back to origin (richer "why
me?" context but visual noise) or only the most recent hop (cleaner but
loses upstream detail). Likely needs UI invention — neither a citation
list nor a simple breadcrumb is quite right.

The mixed-feed hypothesis makes provenance more important, not less. If
teams are not the navigation tree, then every post needs to explain why it
belongs in this user's view: source team, receiving team, carrier, relay
note, and enough signed path context to make the membership-overlap bridge
credible at a glance.

### How teams stay visible without being navigation

Since the primary surface is a mixed feed, teams cannot be the
navigation tree. They probably appear as: prominent tags on every post,
optional facets/filters, a sidebar listing the user's own teams, and
per-team filtering shortcuts. Specific patterns (chips, segmented
controls, sidebar lists) are open and will not be settled until a real
design pass.

### Forwarding with vs. without commentary

A silent forward keeps the "trust the post itself" energy and reads as
pure word-of-mouth ("hey, look at this"). A required note pushes the
carrier into curator/editor stance, which is more deliberate but heavier.
Both are coherent products with noticeably different cultures. Substantive
relay notes also do anti-spam and "why me?" work (see Challenging
Questions 7, 8, and 12). Likely the product supports both with one as
default; which one is the default decides a lot of the tone.

### Notification model

Slow + deliberate cuts hard against per-post push. A daily or weekly
digest is plausibly the right default, with push reserved for direct
mentions or carries that name a specific recipient. Engagement metrics
like daily active users will mislead — The Hedgerow succeeds if people
open it occasionally and find good stuff, not constantly.

### Feed ranking

Probably chronological. Algorithmic ranking solves a volume/noise problem
this app has already designed away, and unranked feeds resist the
engagement-game dynamics this app exists to escape. But "probably" is
not "definitely" — there may be a place for receiver-side per-edge
prioritization that does not look like a global score.

### Asymmetric friction

Slow propagation, fast forwarding. Pollination is the central act and
the most common action; it has to be one click/tap from the feed view.
Friction on a *new post being created* or on *something propagating
automatically* is fine; friction on *a person deciding to forward* is
bad. This asymmetry is worth keeping as a north star when specific UI
decisions come up.

### Cold start

Cross-pollination only works once there is enough density of teams,
members, and overlap. In a young Hedgerow with few users, deliberate-only
propagation means almost nothing propagates and the feed looks dead even
when the protocol is working. Launching into an already-dense seed
community (Small Sea Collective members, a deliberately recruited cohort)
is probably how this gets off the ground; "open it up and watch it grow"
will not work for a slow-by-design feed. See also Challenging Question 13.

## What This Is Not

The easy contrast cases:

- It is not a global Twitter/X clone.
- It is not a firehose protocol.
- It is not a popularity contest built around follower counts.
- It is not a generic ActivityPub server.
- It is not a DM app, a private-team mode, or a substitute for in-team
  chat. Those jobs belong to other Small Sea apps.
- It is not a moderation-free public square.

The harder adjacencies — the things users will actually mistake this for:

- It is not "group chat with a forward button." The carrying act is signed,
  annotated, and intentional, not a one-click reshare.
- It is not Slack Connect or a cross-org channel. Relays are bridge events,
  not a persistent shared room between two teams.
- It is not a mailing-list digest or a Facebook-Group crosspost. There is no
  global publishing surface, and subscribing is not the act that pulls
  content in — a teammate carrying it in is.

Small Sea is designed for human-scale coordination. The Hedgerow should
lean into that instead of trying to smuggle internet-scale social media
into the repo under a friendlier name.

## Why Small Sea Specifically

The reason this app needs Small Sea, rather than any other substrate, is
that team membership is itself a real, decentralized, cryptographic thing
in Small Sea — not a server's opinion. That is what makes a relay
authenticatable as a *team-internal act* without a platform vouching for
the team. "Bob, who really is a member of Team A, really did decide to
carry this into Team B" is a verifiable statement here in a way it cannot
be on ActivityPub, AT Protocol, or Nostr.

Everything else Small Sea provides — local-first storage, signed identity,
Hub-mediated transport, human-repair-over-false-certainty — is shared with
every other Small Sea app, necessary but not distinctive to this one.

## Comparison Points

None of these projects is the model — they are mirrors. Briar and Secure
Scuttlebutt take social trust, local storage, and peer-to-peer propagation
seriously, but both are organized around individual-to-individual graphs;
The Hedgerow is organized around team-to-team bridges. Nostr, ActivityPub,
AT Protocol, and Farcaster are mostly contrast cases — they show how
quickly "social protocol" turns into public infrastructure, global
identity, firehoses, and moderation pressure.

### Secure Scuttlebutt

[Secure Scuttlebutt](https://scuttlebot.io/more/protocols/secure-scuttlebutt.html)
shares the local-first, signed-feed instincts, but it is structurally a
*follow graph between individuals* with friends-of-friends propagation.
The Hedgerow is bipartite: people × teams, with bridging acts as the
propagation event. Different math, different politics, different failure
modes. SSB is useful as a substrate inspiration; it is not the social
model.

Borrow:

- Signed local-first social data.
- Socially bounded replication instead of universal indexing.

Reject or rethink:

- The Hedgerow is team-mediated, not individual-follow mediated.
- A global-ish gossip network of individual feeds is the wrong shape for
  the bipartite team-bridging model.

### Nostr

[Nostr](https://nips.nostr.com/1) is useful because it is radically simple:
signed events are published to relays, and clients subscribe by filters.

Borrow:

- A small, inspectable signed event envelope.
- The idea that relays can be dumb transport/storage infrastructure.
- Cryptographic event identity.

Reject or rethink:

- Public relays are the wrong trust shape for Small Sea.
- Nostr's global key identity is too account-centric for team-mediated
  context.
- Relay shopping should not bypass the local Hub.

### ActivityPub and Mastodon

[ActivityPub](https://www.w3.org/TR/activitypub/) is the standard
comparison for federated social networking: actors publish to outboxes and
servers deliver activities to inboxes.

Borrow:

- Clear verbs for social actions: create, announce, reply, like, delete,
  block.
- Separation between client-to-server and server-to-server concerns.
- Lessons from Mastodon moderation and federation failure modes.

Reject or rethink:

- Server-instance identity is not the right primitive; Small Sea teams are.
- Inbox delivery to arbitrary remote actors is broader than the desired
  first slice.
- Public web addressing creates discovery, moderation, and takedown
  pressures that The Hedgerow may not want at this stage.

### Bluesky AT Protocol

[AT Protocol](https://docs.bsky.app/docs/advanced-guides/atproto) is a
strong comparison for portable identity, signed repositories, and
separable app views.

Borrow:

- Signed data repositories as a durable user-data substrate.
- The distinction between raw protocol data and app-specific views.
- Account portability as a product value.

Reject or rethink:

- AT Protocol is built for large public networks; The Hedgerow is not.
- Big relays and firehose services would distort the team-bridging
  premise.
- Domain-based public identity is not the same as Small Sea team
  membership.

### Briar

[Briar](https://briarproject.org/how-it-works/) is a comparison for
resilient peer-to-peer communication.

Borrow:

- Direct, encrypted, device-to-device instincts.
- Offline and intermittent connectivity as normal.

Reject or rethink:

- The Hedgerow is not primarily a crisis-messaging or
  surveillance-resistant app. Privacy is not its design center; sharing
  outward is.
- Small Sea already routes communication through the Hub abstraction, so
  The Hedgerow should not choose transports directly.
- Contact-to-contact sync is a different social object than team-to-team
  relay.

### Farcaster

[Farcaster](https://docs.neynar.com/farcaster/learn/what-is-farcaster/messages)
is useful as a contrast case: signed social messages propagate through
hubs and eventually form a public social graph.

Borrow:

- Compact signed message types.
- Separation between custody identity and app signing keys.

Reject or rethink:

- Public global graph replication is not a Small Sea goal.
- Blockchain-backed identity and storage quotas are unnecessary here.
- "Hub" means something different in Farcaster; in Small Sea, the Hub is
  the local gateway and policy boundary.

## Challenging Questions

These are not polish questions. The answers could change the first real
implementation. The first one is the gate; nothing downstream can be
answered generically.

1. **What is the first niche?**
   Mutual-aid updates, neighborhood alerts, small professional referrals,
   reading-group discoveries, event invitations, trusted classifieds —
   these want opposite tradeoffs on consent, visible context, deletion,
   and anti-spam. Pick one and the rest of these questions sharpen; defer
   it and they stay generic. Currently deferred — but flag any feature
   debate that would be settled by a niche choice.
2. **Is "The Hedgerow" the final name?**
   It is the working name, chosen to put the social object — a dense
   edge ecosystem connecting otherwise separate patches — directly into
   the brand. The article ("*The* Hedgerow") parallels "The Internet"
   as the singular emergent thing made of overlapping local pieces.
   The naming search moved through several adjacent metaphors:

   - *The Mangrove* had a strong marine/ecosystem feel, but Mangrove DAO
     already has a real developer-facing crypto footprint. Even if the
     product categories differ, the name risks search confusion and
     accidental Web3/DAO associations.
   - *The Tidepool* captured bounded local worlds connected by a larger
     sea. It is warm, inspectable, and very Small Sea, but may sound too
     gentle or nature-app-like if the surrounding product language gets
     cute.
   - *The Eddies*, *The Shoals*, *The Sound*, and *The Third Places*
     each captured part of the desired shape: local circulation,
     gathering, signal/place, and semi-public social pockets. None
     captured the living edge between bounded teams as directly as
     hedgerow.
   - *The Hedgerow* became more compelling once the UI hypothesis shifted
     from "each team has a channel" to "the user walks one mixed edge
     feed where many team contexts overlap."

   What still needs to settle: namespace and trademark collisions (GitHub
   orgs, npm/PyPI, `.org` domains, USPTO). Known collision notes so far:
   Hedgerow Software appears to be a real software company in the
   environmental-health/regulatory domain; Healthy Hedgerows appears to
   be a conservation app; Hedgerow Theatre, Hedgerow Farms, and The
   Hedgerow Group occupy unrelated cultural, agricultural, or consulting
   space. Also check whether *hedge*-as-finance pollutes the brand for
   some audiences. Until those checks come back clean, treat the name as
   promising but revocable.
3. **Who consents to a relay?**
   If Alice posts inside Team A, can Bob carry it to Team B by default?
   Does Alice need to mark the post as relayable? Can Team A set a norm
   that posts are local-only unless explicitly released? Whatever the
   policy, it must compose with Small Sea's existing
   Admin/Contributor/Observer roles, not ignore them.
4. **How is the path shown?**
   The Hedgerow is not the privacy-focused app, but "make bridges
   visible" is the invariant, not "make every upstream detail visible in
   every context." Bridges *want* to be visible: being a known bridge
   between two communities is the social product. Receivers should at
   minimum see the carrier identity, source team, destination team, relay
   note, and membership proof for the hop that brought the artifact to
   them. Whether they also see the complete upstream chain back to origin
   is a product/policy decision, not a law of the protocol.
5. **What does deletion mean?**
   Once a post has crossed team boundaries, revocation cannot be magic.
   But "I posted something wrong, embarrassing, or dangerous, please get
   it back" is a practical floor, not a research question. Whatever
   ships needs a credible deletion story before the first real slice:
   tombstone, request, key rotation, local moderation — likely all of
   these in different layers.
6. **What prevents laundering?**
   A harmful post could gain legitimacy by passing through a respected
   team. Recipients need to see each relay's annotation and dissent, not
   just the path. Cryptographic provenance (who signed what) and social
   provenance (why this carries weight here) must be distinguished in
   the UI; conflating them recreates the blockchain pitch's "we proved a
   hash, therefore trust it" failure mode.
7. **How does meaning emerge without a score?**
   A relay through a small, normally-quiet team should feel more
   meaningful than one through a big, chatty team that forwards
   everything. The trap is to compute a "team weight" — any aggregate
   becomes farmable, becomes a leaderboard, and recreates the engagement
   game this app exists to escape. Instead, surface the raw conditions
   per relay and let the receiver eyeball them:

   - The carrier's history of relays into *this* team (last N, with
     timestamps).
   - The carrier's fan-out at relay time — only us, or also five other
     teams in the same act?
   - The substance of the relay note. A team that forwards everything
     will not write personalized notes; an effortful note is itself the
     scarcity signal, and unlike a counter it cannot be farmed.
   - The source team's recent traffic into this receiver, drawn from
     what is already locally visible.

   No aggregation, no ranking. The "rare relay from a tight team" reads
   as one entry in eight months with a real note; the "firehose team"
   reads as seven entries this week with no notes. As a companion lever,
   receiver-side per-edge budgets ("more than 3 relays/week from Team A
   goes to a review queue") let receivers throttle locally without
   anyone having to compute a global metric.

8. **What is the anti-spam primitive?**
   Social scarcity is the obvious answer: only team members can relay
   into their teams. Combined with mandatory substantive relay notes
   (slow UI, minimum length) and the receiver-side per-edge budgets from
   question 7, that may be enough without quotas, allowlists, or
   quarantine states.
9. **Can a team refuse a relay before it lands?**
   If Team B gets a post because Bob is a member, does it appear
   immediately, or does it enter a review queue? Is the answer different
   for read-write members and observers? Receiver-side budgets push some
   of this into a configurable per-edge policy.
10. **What counts as authorship?**
    A relay note can change meaning dramatically. Is a relay-with-comment
    a new post that cites the old one, or the same post with attached
    commentary?
11. **Can teams survive being routing infrastructure?**
    If a team becomes valuable as a bridge to another audience, social
    pressure may change the group itself. Does the app need per-topic
    relay channels so the family chat does not become a news distribution
    hub?
12. **How does a recipient know "why me"?**
    A signed path answers where the post came from. It does not
    necessarily answer why it reached *this* team. Substantive relay
    notes — possibly mandatory — are the only honest answer.

13. **How does The Hedgerow bootstrap?**
    Cross-pollination needs density: enough teams, enough members,
    enough membership overlap that posts have somewhere to go and
    reasons to be carried. In a sparse early network, deliberate-only
    propagation produces a near-empty feed, which reads as broken even
    when the protocol is working. The first real deployment likely
    needs a deliberately recruited seed community rather than open
    signup. Open: how to graduate from seeded into self-sustaining
    without diluting the trust properties that make deliberate
    propagation meaningful in the first place.

## First Data Model Sketch

This is intentionally provisional. The unique data type is the relay; the
post can be any explicitly relayable Small Sea artifact.

- `relay`: the central object. Signer, signer's source-team membership at
  signing time, signer's destination-team membership at signing time,
  content reference (a relayable artifact hash, not necessarily a
  Hedgerow-native post), source path hash, destination team berth,
  relay note, fan-out (other destinations being relayed to in the same
  act), timestamp, and signature.
- `relayable_artifact`: the export boundary from source content into The
  Hedgerow. It records the content hash or snapshot, source team context,
  author/source-app policy, allowed relay scope, and any tombstone/revocation
  pointer. It is the place where "can this be carried?" is decided before a
  relay exists.
- `path`: ordered relay entries, each signed by the relay actor and
  verifiable against the previous path hash.
- `post` (optional, app-native): if The Hedgerow needs a content type of
  its own — for posts authored directly inside a Hedgerow berth — it
  is author-signed content with an origin team context. The relay graph
  should also work over any addressable Small Sea content.
- `local_moderation`: team-local hide, pin, annotate, quarantine, or block
  decisions. These are not global truth.
- `receipt`: local record that a team saw a post/path, useful for dedupe
  and future sync.

The important design constraint is that app-visible session and team
identity must come through the Hub API, not direct reads from Manager
databases.

## Possible First Slice

A useful first implementation slice, when the time comes, might be:

1. Create a post in one team.
2. Relay it into a second team where the same local sandbox has
   membership.
3. Verify the relay path locally.
4. Show both teams' local copies with different local moderation state.
5. Render the receiver-side context surfacing from question 7 (carrier
   history into receiver, fan-out at relay time, note substance) using
   local fixture data.
6. Keep all communication local or mocked in micro tests.

That slice avoids public discovery, cross-device sync weirdness, global
search, and full moderation policy while still testing the unique idea:
signed team-to-team propagation with no aggregate score.

## Micro Test Ideas

When real code starts, the first micro tests should make the
social/protocol boundary hard to blur:

- A relay path verifies when each hop signs the previous path hash.
- Tampering with a prior hop invalidates later path verification.
- A post marked local-only cannot be relayed by the app.
- A recipient team can hide or quarantine a post without mutating the
  origin post or the signed relay path.
- Receiver-side per-edge budgets correctly route over-budget relays into
  a review queue.
- The app obtains session/team information via the Hub API only.
- Tests use local fixtures or mocked Hub services, never internet
  services.

## Open Product Bets

The app is worth pursuing only if at least one of these bets is true:

- People want social discovery through accountable human relays, not
  opaque recommendation systems.
- Being a known bridge between two communities is itself a positive
  social identity — bridges *want* to be visible.
- The extra friction of substantive relay notes and team-scoped sharing
  improves quality more than it reduces participation.
- Meaning can be surfaced without being scored: receivers will read raw
  context (carrier history, fan-out, note substance) more honestly than
  they would read a number.
- Small groups can be bridges without being consumed by the dynamics of
  public platforms.

If those bets are false, The Hedgerow should stay a sketch rather than
becoming another social feed with better cryptography and the same old
problems.
