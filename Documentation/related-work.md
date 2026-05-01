# Related Work

A working list of projects whose contributors might reasonably say "we have
been doing local-first teams for years," and how Small Sea positions itself
relative to each. This doc exists because Small Sea is not the first project
in this neighborhood, and engaging the closest neighbors directly is a
prerequisite both for design honesty and for not getting blindsided in
public venues.

The orienting question for each entry: **if a contributor to this project
saw a Small Sea talk or pitch, what would they say?** Followed by: what is
the honest, specific differentiation that survives that pushback?

The single sharpest positioning Small Sea can defensibly hold is the
conjunction of three constraints. Each constraint excludes a different
piece of the prior art:

- **No bespoke services in the stack, not even sync servers.** Apps run only
  on generic infrastructure users already pay for (cloud storage,
  notification services, VPN-style transports).
- **Per-team-scoped identity by default.** A person does not have one global
  sovereign identity; they have several, one per durable team they belong
  to. Cross-team linking is a deliberate act, not an automatic one.
- **Team membership as the cryptographic primitive.** The team is the social
  unit and governance unit, not a permission scope on individually-owned
  data.

What follows is the prior art, organized by how directly each project
contests that ground.

---

## 1. Jazz (Garden Computing)

<https://jazz.tools/>

**What they do.** A toolkit for building apps with distributed state, built
around CoValues (custom CRDTs) and explicit Groups & Accounts as
permission/identity primitives. Every CoValue is owned by either an Account
(private) or a Group (shared); cryptographic permissions are handled by
local crypto. Documentation explicitly states that Groups are how Jazz
controls who gets access and what they can do. Active development through
2026; Jazz appeared at Local-First Conf 2024.

**What they would say.** "We have first-class Groups in our type system.
We have had this for years. What is novel here?"

**Differentiation.**

- Jazz Groups are *permissions on data* — a scoping mechanism for who can
  read/write a CoValue. Small Sea Teams are the *social primitive itself*:
  identity is per-team, apps are scoped to teams, the team is the unit of
  governance, not just an access-control attribute.
- Jazz uses Jazz Cloud (a hosted sync mesh) by default, even though it is
  self-hostable. Small Sea's "no bespoke services even for sync" rule is a
  sharper constraint than Jazz adopts.
- Jazz is CRDT-centric (CoValues are CRDTs); Small Sea's bet on
  version-control-style history with 3-way merge is explicitly the
  alternative path on the longevity-vs-immediacy axis.
- Accounts in Jazz are global to the Jazz substrate. Small Sea identity is
  per-team-scoped by default.

**Risk.** This is the strongest territorial claim in the landscape. Any
public Small Sea pitch should engage Jazz by name rather than ignore it.

---

## 2. Earthstar

<https://earthstar-project.org/>

**What they do.** A local-first, peer-to-peer protocol and library for
small-scale decentralized apps. Each *share* (their group concept) has an
address and a secret; knowledge of the secret grants write access. Storage
is offline-first, syncing happens between trusted peers via append-only
feeds. Active maintainer (Cinnamon Bun) with standing in the local-first
community. Recent versions added local-network peer discovery via DNS-SD.

**What they would say.** "We have been doing local-first decentralized
groups since before this conversation started."

**Differentiation.**

- Earthstar shares are intentionally minimal — knowledge of a share secret
  equals access. Small Sea has elaborate membership protocols: Signal-style
  X3DH/Double Ratchet certification, governance-snapshot-anchored admin
  quorums, Admin/Contributor/Observer roles, transcript-bound admission.
  Earthstar is "bring your own group concept"; Small Sea ships a substantial
  governance structure.
- Earthstar treats one keypair as one author across all shares; Small Sea
  defaults to per-team-scoped identity, so the same person presents
  separately in different teams unless they deliberately link.
- Earthstar is intentionally small-scope. Small Sea is also small-scope but
  expects to host meaningful applications (file vaults, structured data, the
  social bridging app) rather than primarily journals and chats.

**Risk.** Real but limited. Earthstar's stance is sufficiently minimalist
that the differentiation on governance and identity scoping is genuine, not
cosmetic.

---

## 3. Radicle

<https://radicle.dev/>

**What they do.** Local-first, peer-to-peer code collaboration on Git. Each
participant runs a node; nodes gossip and replicate Git repositories via a
peer-to-peer network. Their *Collaborative Objects* (COBs) put issues, code
reviews, and discussions inside the repo, replicated alongside source.
Active in 2026 (v1.8.0), well-funded, two FOSDEM 2026 talks.

**What they would say.** "Code review is teamwork. We have been doing
local-first peer-to-peer team collaboration on Git for years."

**Differentiation.**

- Radicle is code-collaboration-specific (project teams of developers).
  Small Sea is a general framework for diverse team types — families,
  neighborhoods, hobby groups, professional circles — most of which never
  touch a code repository.
- Radicle nodes gossip directly between peers. Small Sea routes
  communication through users' generic cloud storage and notification
  services, not bespoke nodes.
- Identity in Radicle is one keypair per developer, used across all
  repositories. Small Sea is per-team-scoped.

**Risk.** Lower than Jazz or Earthstar. Different domain, but the team is
visible and respected; cite them rather than ignore them.

---

## 4. Spritely Institute

<https://spritely.institute/> · <https://spritely.institute/goblins/>

**What they do.** A nonprofit research institute building Goblins, a
distributed object-capability programming environment, as the substrate for
secure decentralized applications. The full institute name is "Spritely
Networked Communities Institute"; their stated mission centers on
communities organizing, governing, and protecting their members. Christine
Lemmer-Webber (co-author of ActivityPub) is the lead. Active in 2026,
presenting at QCon London.

**What they would say.** "Communities are the entire point of our
institute. We have been thinking about decentralized communities at the
foundational layer for years."

**Differentiation.**

- Spritely operates at the *programming environment* layer (object
  capabilities, distributed objects, OCapN protocols). Small Sea operates
  at the *application framework* layer (apps backed by user-owned cloud
  storage). Different abstraction levels, potentially complementary.
- Spritely's "communities" are a research target framed in capability-secure
  terms. Small Sea's teams are concrete deployment artifacts with file-level
  semantics, governance roles, and a specific admission protocol.

**Risk.** Branding-adjacent rather than design-adjacent, but the visibility
of the Spritely team in the decentralized-web scene means they should be
named and credited explicitly when the conversation turns to community-scale
decentralized infrastructure.

---

## 5. Adjacent but More Distant

These projects are part of the surrounding conversation but do not directly
contest the "local-first teams" ground.

- **Secure Scuttlebutt / Manyverse / Patchwork.** Friend-graph rather than
  team-graph. Treated in detail in `packages/the-hedgerow/README.md`.
  Loud, recognizable community; worth citing whenever the social-data
  topic arises.
- **Briar.** Peer-to-peer encrypted messaging with private groups.
  Surveillance-resistant focus. Different threat model and different social
  primitive (contact rather than team).
- **Iroh (n0-computer).** Substrate-level peer-to-peer networking. Not
  group-centric, but used to build group-shaped things. Relevant if Small
  Sea ever revisits direct peer transports.
- **Matrix P2P / Pinecone.** Federated chat with experimental local-first
  variants. Rooms are team-shaped but the primary deployment model is
  homeserver-federated.
- **MLS (Messaging Layer Security) ecosystem (Wire, parts of Webex).**
  Defines a group key-agreement protocol; useful as a comparison for
  Cuttlefish's group-message handling, less so for the social architecture.
- **OrbitDB, Berty, Holepunch/Pears.** Each lives in the
  decentralized-collaboration neighborhood with various group concepts;
  none clearly contests the team-as-primitive framing.

---

## How to Use This Doc

- **Before any public talk or written pitch**, scan the top four entries and
  make sure the framing does not implicitly erase Jazz, Earthstar, Radicle,
  or Spritely. Engaging the strongest comparisons up front signals
  confidence; ignoring them signals incomplete homework.
- **When making architectural decisions**, the differentiations listed are
  load-bearing claims about Small Sea's identity. Decisions that erode any
  of the three constraints (no bespoke services / per-team-scoped identity /
  team as primitive) shrink the gap with Jazz in particular and weaken the
  case for Small Sea existing at all.
- **Treat this list as living.** Update entries as projects shift, and add
  new entries when the local-first landscape grows. The status snapshots
  here are accurate as of late April 2026 and will date.
