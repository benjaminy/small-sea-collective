# Linked Teams

Status: exploratory design note.
This is expected to become a feature, but the protocol is not settled.

Small Sea teams are intended to stay human-sized.
A team with several dozen people is already near the upper edge of the intended operating shape.
When a group gets large enough to need departments, nested roles, visibility matrices, delegated administrators, and policy engines, the first answer should usually be "more than one team," not "one team with an organization system inside it."

Linked teams are the candidate answer to that pressure.
A linked-team protocol would let small constitutional units recognize each other for bounded purposes without merging their membership, governance, identity, storage, or app histories.

Two working slogans carry this draft:

> Teams are constitutional units.
> Links are signed, purpose-scoped relationships between those units.

> A link between two teams is carried by people who belong to both.
> Without those people, the connection is meaningless.

This document is deliberately argumentative.
It should preserve competing possibilities until the design has enough shape to justify implementation.

---

## Why This Exists

Small Sea's current model has one modest built-in answer for medium-sized teams:
per-berth integration modes.
Automatic teammates are expected to monitor and integrate ordinary publications.
Proposal-only teammates can read, author, and sign, but their ordinary publications are not monitored by default; they submit proposals instead.

That is probably enough for a larger family, a small studio, a volunteer group, or a neighborhood circle.
It is not trying to become an enterprise role system.

The linked-team idea starts from a different claim:

- Keep each team's Core history small enough for ordinary people to reason about.
- Let larger social structures emerge as graphs of teams.
- Make cross-team relationships explicit, signed, and inspectable.
- Avoid importing another team's entire constitution into local policy.

The desired outcome is not "Small Sea supports huge teams."
The desired outcome is "Small Sea supports many small teams that can cooperate deliberately."

---

## The Central Question: What Is Another Team?

Every other question in this document depends on one hard problem.
When Team A wants a relationship with Team B, what is "Team B"?

Small Sea makes this deliberately awkward, and recent protocol work made it more so.
A team has no central server and no single key.
A team is its signed, append-only Core history: admissions, device links, revocations, exclusions, integration-mode changes, and endorsements.
The team "does not speak"; individual teammates speak, and the team's voice is the aggregate of that history.
Worse, a Core lineage can fork.
Different participants can hold internally consistent but divergent accepted tips.

So "Team B" is not a single state that Team A can point at.
It is a forkable lineage with no built-in spokesperson.
Naming it by genesis hash gives a stable, immutable handle, but a bare handle says nothing about who is in Team B *now* or who may speak for it.

### The bridge answer

The most promising answer does not try to give Team B a cryptographic voice.
It dissolves the problem instead.

Team A does not reason about an abstract "Team B" principal.
Team A reasons about **a person who belongs to both teams** — a *bridge teammate*.
That person is already a first-class principal in Team A's own Core: a recognized teammate with device keys Team A can verify, trust, and exclude.
The cross-team relationship is carried entirely by people Team A already knows how to talk about.

This is not a trick.
It is the same principle the rest of Small Sea already commits to, lifted one level:

- The team does not speak, so do not make Team B speak.
  A bridge teammate speaks, and they are an ordinary local principal.
- Identity accretes through interaction, not from a one-time record.
  Team A knows Team B because Team A knows specific people who live in Team B.
  An opaque genesis hash with no human straddling the boundary is, correctly, meaningless to Team A.
- A bridge teammate is, cryptographically, a person who has published an `identity_link` between their Team A identity and their Team B identity, plus a Team A designation of a bridging role and scope.
  Much of the machinery may already exist.
  Be sharp about what that `identity_link` proves to Team A, though: under the witness model it is Sarah's own assertion that she is also in Team B, trusted because Team A trusts Sarah, not an independently verified fact about Team B's membership.
  There is no hidden cryptographic remote-membership check here.
  Team A is trusting a local person, and the design should say so plainly everywhere it relies on it.

The fork problem does not disappear, but it relocates to something answerable.
"Which Team B?" becomes "which Team B does this bridge person see?"
A bridge teammate's client has a definite Core tip, so the question has a concrete answer.
If Team A has several bridges and they report different Team B tips, Team A *sees the fork through its own bridges* rather than discovering it by independently replaying a foreign constitution.
Surfacing disagreement instead of hiding it is already the house style.

---

## Bridge Teammates

A bridge teammate is a recognized Team A teammate whom Team A has designated as a liaison to Team B for a bounded purpose.

Key properties this draft is leaning toward:

- **Local and (initially) unilateral.**
  Designating a bridge is a Team A Core record about Team A's *own* teammate.
  It does not require Team B's cooperation to exist.
  Team B may reciprocate by designating its own bridges to Team A, but reciprocity is a separate, later optimization, not a precondition.
- **Scoped.**
  A bridge designation names a purpose, a berth or app-defined subset, and a policy.
  "Sarah is our bridge to the Design team for proposals into the Roadmap berth" is a complete, legible grant.
- **Plural by design.**
  A single bridge is a single point of failure and a single point of trust.
  Team A may require a quorum of bridges — k-of-n — before acting on cross-team information.
  This maps directly onto the existing endorsement-threshold concept; it is the same shape as automatic-integrator quorum, applied to cross-team facts.
  "Independent" here means distinct **Team A teammate identities**, not distinct device signatures: as with admission endorsements, multiple devices of the same bridge person dedupe to one.
  (Whether a quorum should also require distinct *Team B* identities, or tolerate several Team A people bridging through the same Team B person, is left open.)
- **Accountable.**
  A bridge's attestations are signed and append-only.
  A bridge that misreports Team B leaves an inspectable record, and Team A can revoke the bridge role by exclusion the same way it manages any other teammate standing.

### Bootstrapping the first bridge

A bridge teammate cannot be conjured by a link protocol.
The first cross-team tie is created by ordinary admission: a person is admitted to both Team A and Team B through each team's normal flow, then publishes the cross-team `identity_link` that makes the dual membership legible.

This is a feature, not a gap.
It means the minimal linked-team feature needs no new bilateral ceremony.
The link *is* the existence of one or more dual-member people, plus a local designation of their bridging role.

---

## The Core Decision: Witness or Router

The single most important unresolved question is what a bridge actually *attests*, and how much Team A independently verifies.

**Witness (oracle).**
The bridge attests, into Team A's Core, that an artifact is meaningful in the Team B view that bridge currently holds:
"Sarah attests that this artifact is a real proposal in her Team B view," or "Sarah attests that the Team B tip she holds is X."
Team A trusts the attestation because Sarah is a recognized Team A teammate, not because Team A has independently verified anything about Team B.
Team A does *not* replay Team B's constitution.

Be careful with the phrasing here, because it is easy to smuggle the team-principal back in.
Since the team does not speak, the honest claim is never "Team B did X" but "a trusted bridge attests that X holds in the Team B view they carry."
This document calls such an artifact a **bridge-witnessed remote proposal**, and uses looser phrases like "a Team B proposal" only as defined shorthand for that — never as a claim that Team B is a principal with a voice of its own.
Router mode does not close this gap.
Router lets Team A independently verify Team B's signatures and history, but the strongest honest claim it buys is still person-level — "this artifact was signed by a recognized Team B teammate at remote anchor X" — not "Team B did X."
Router changes who verifies, not who speaks.
A literal "Team B did X" would require a future team-voice model that does not yet exist.

- Cheap and simple.
- Makes the remote-signer and fork-choice problems vanish from Team A's machinery, because that work happens inside the bridge's own client as a real Team B participant.
- Explicit cost: Team A's view of Team B is mediated, and is exactly as good and as honest as its bridges.
  A quorum of bridges is the mitigation, not independent verification.

**Router (translator).**
The bridge only carries Team B's signed artifacts across the boundary.
Team A still verifies Team B's signatures and history itself.

- Preserves independent verification.
- Reintroduces the cost the witness model avoids: Team A must track enough of Team B's Core to validate signers and choose a tip.

The current leaning is to default to **witness** and offer **router** as a stronger, costlier mode selected per purpose.
Witness is the option consistent with the whole project ethos — trust runs through people, not oracles — and it is the option that actually buys the simplification.
The tradeoff must be stated out loud wherever this is documented, never slipped in:
under the witness model, Team A trusts its bridges' summary of Team B and accepts being only as well-informed as those people are.

---

## Provisional Design Principle

Do not treat a remote team as a bag of local teammates, and do not treat it as a standalone principal either.
Reach a remote team *through* bridge teammates, who are local principals.

A Team A bridge record evaluates locally and might say, in effect:

```text
Team A designates teammate Sarah
as a bridge to Team B (named by Team B's stable identity)
for purpose P
scoped to berth/app-subset S
under policy R
endorsed under Team A's Core rules at Team A Core state Y
```

This does not make Team B's members into Team A teammates.
It does not import Team B's roles.
It does not let Team B mutate Team A's Core by fiat.
It records that a trusted local person carries a bounded relationship, which Team A can evaluate entirely within its own constitution.

---

## Candidate Link Purposes

The first vocabulary should be small.
Every new purpose adds validation rules, UX, and failure cases.

It helps to split purposes by whether a bridge can carry them without sharing plaintext, and by whether a shared child team (see below) would do the job just as well.

- **Recognition.** Team A records that Team B is the team it claims to be, grounded by a bridge person who vouches "yes, that identity is the team we know."
  This supports display, discovery, or later policy without granting data access.
  *Irreducibly cross-team; a bridge can witness it without sharing any Team B content.*
- **Proposal routing.** A bridge carries a bridge-witnessed remote proposal into a Team A berth.
  Team A automatic integrators still decide whether to endorse and merge it.
  *Irreducibly cross-team; works under the witness model with no plaintext sharing.*
- **Read sharing.** Team A grants Team B read access to a particular berth, object set, or app-defined view.
  This immediately raises key-distribution questions and is where a shared child team competes hardest.
- **Mirror relationship.** Team A and Team B agree to replicate selected berth history for shared work.
  Stronger than read sharing; a shared child team is often the cleaner answer.
- **Delegation.** Team A trusts Team B to make a bounded decision, such as vouching for contributors or maintaining a shared app berth.
  Powerful, and probably not in the first version.

The likely first serious version is recognition plus witness-mediated proposal routing.
Both are purposes a shared child team cannot provide, because their whole point is preserving two distinct constitutions.
Read sharing is important, but it should not be allowed to drag in an accidental organization system, and it has a strong alternative.

---

## The Bridge / Shared-Child-Team Duality

There is a second way to connect two teams: create a third team C that the relevant people from A and B both join, and do the shared work there.

Bridge teammates and shared child teams are duals.
In both, the cross-team tie is carried by people who belong to more than one team.
The only difference is *where those dual-member people do their shared work*:

- **Bridge:** keep two constitutions; a co-member mediates between them.
- **Shared child team:** the co-members do their shared work inside a new constitution, reusing all existing machinery — admission, integration modes, recovery, storage — with zero new protocol.

This suggests a deeper invariant:
the connection between two teams is always dual-member people; the only question is whether their shared work gets its own room.

That makes "bridge versus shared team" a per-relationship choice rather than two unrelated features.
A standing, content-heavy collaboration probably wants its own room.
A bounded recognition or a trickle of proposals probably wants a bridge.

---

## The Mortality Boundary

The strong form of the central principle has a deliberate consequence worth naming.

A linked-team relationship is exactly as mortal as the relationships between specific people.
No bridge person, no link.
Small Sea is choosing not to support purely institutional, person-independent standing relationships that survive everyone leaving.

For an enterprise federation product that would be a defect.
For this project — regular people, human-scale, no operator trust — it is the right scope, and it rhymes with the device-recovery philosophy:
do not manufacture continuity that cannot be grounded in people.
If no human continuity exists, the honest move is to rebuild the relationship, not to claim a standing that no living person backs.

This boundary is about live authority, not historical provenance.
When a bridge person leaves or is excluded, the link dies as a *route*: no new cross-team input flows through them.
It does not retroactively invalidate what already happened.
Proposals Team A already accepted keep their explanation in signed Core history — including which bridge witnessed them — and data already shared does not become un-shared.
The bridge's past attestations remain evidence of why earlier actions were legitimate at the time; only the live channel closes.

This should be written as a chosen boundary, not left as an apparent omission, because a skeptical reader will otherwise read it as one.

---

## What a Link Is Not

A link is not a team merge.
Each team keeps its own Core lineage, teammate identities, device records, storage announcements, integration modes, and recovery practices.

A link is not transitive.
If A links to B and B links to C, that implies nothing about A and C.
Any delegation depth must be an explicit, opt-in property of a specific grant, never a default.

A link is not a person's own cross-team identity link.
`identity_link` is one human asserting that two per-team identities are the same person.
A team link is a relationship between two teams, carried by such people but distinct from any one person's identity claim.

A link is not teammate unification.
Unification reconciles multiple UUIDs for one person inside a single team.
A team link spans two teams.

A link is not a global namespace.
Two teams may use the same display name.
Recognition must bind to stable cryptographic team identity, not to friendly labels.

A link is not a guarantee that every member of the remote team is known locally.
Under the witness model the opposite is usually true: a bridge reports bounded facts about Team B without exposing its full roster.

A link is not a remote administration channel.
Team B being recognized by Team A does not make Team B an administrator of Team A.

A link is not an access-control substitute for encryption.
As elsewhere in Small Sea, read access is endpoint-trust-scoped.
Once plaintext or receiver state reaches a trusted endpoint, the protocol cannot prevent that endpoint from revealing it elsewhere.

---

## Sketch: Records

This is only a sketch.
Names are placeholders.

A bridge designation is a Team A-local Core record:

```yaml
record_type: team_bridge
schema_version: 1
bridge_id: uuidv7
local_team_id: team-a-id
remote_team_id: team-b-id          # stable cryptographic identity, not a label
bridge_teammate_id: sarah          # a current Team A teammate
purpose: proposal-routing
mode: witness                      # witness | router
scope:
  berth_id: team-a-berth-id
  proposal_kinds:
    - app-defined-kind
policy:
  expires_at: null
  quorum: 1                        # distinct bridge teammate identities required to act
  remote_membership_expansion: none
local_core_anchor: team-a-core-commit
author_teammate_id: alice
signature: ...
endorsements:
  - ...
```

A witnessed cross-team proposal might then look like a Team A proposal whose provenance includes a bridge attestation:

```yaml
record_type: cross_team_proposal
bridge_id: <the bridge record above>
attesting_teammate_id: sarah       # the bridge, signing as a Team A teammate
remote_origin:                     # what the bridge claims about Team B
  remote_team_id: team-b-id
  remote_core_anchor: team-b-core-commit
  remote_author: team-b-uuid
payload_or_commitment: ...
signature: ...
```

Questions this sketch deliberately leaves open:

- What is `remote_team_id` exactly — a root key, a Core genesis hash, a stable team UUID signed by the genesis record, or something else?
- Under the witness model, does Team A store any of Team B's own signatures, or only the bridge's attestation over them?
- Is a bridge designation append-only-amendable, a new record per change, or a normal Core proposal?
- Can one team revoke a bridge unilaterally?
  It almost certainly can, because the bridge is its own teammate; the semantics should still be explicit.
- How much bridge and link metadata is personal data?
  Friendly team names, descriptions, and reasons may need the same commitment-and-payload treatment as other human-readable identity material.

---

## The Hard Part: Readability

Recognition and proposal routing can be done by a witness bridge with no plaintext crossing the boundary.
Read sharing cannot, and it remains the dangerous part.

The most dangerous shortcut is to say:
"Team A grants Team B access, therefore all Team B members can read."

That hides several different mechanisms:

- Team A could encrypt to every current Team B member at a specific Core anchor.
- Team A could encrypt to a Team B-published group recipient key.
- Team A could share only through a bridge teammate who belongs to both teams.
- Team A could grant proposal routing first and defer plaintext sharing entirely.
- Team A could create a shared child team instead of sharing across existing teams.

Each choice has different consequences.

Encrypting to every current Team B member makes Team A track Team B membership churn.
That risks turning the link into a shadow membership import — exactly the thing the bridge model is trying to avoid.

Encrypting to a Team B-published group recipient key keeps Team A from expanding Team B membership locally.
It also means Team A trusts Team B's internal key management and exclusion practices, which needs a clear threat model.

Sharing through a bridge teammate is operationally simple and socially legible, and it composes with the witness model.
It also concentrates responsibility and exposure in specific people, which is why a quorum of bridges matters.

Deferring read sharing is unsatisfying, but it may be the cleanest first move.
Recognition and witnessed routing can prove the link model before the cryptographic readability problem hardens into a bad abstraction.

---

## Relationship to Existing Small Sea Concepts

### Core

Bridge designations and any durable link facts are governance-bearing.
If implemented, they belong in signed, append-only Core history.
Mutable tables may project current effective bridges and links, but the durable record should remain inspectable.
A past question such as "why did Team A accept this proposal from Team B?" must be answerable from accepted Core history, including which bridge witnessed it.

### identity_link

A bridge teammate's dual membership is expressed by an `identity_link` between their two per-team identities.
The bridge designation adds the role and scope on top of that existing cross-team identity claim.
This is the most likely place to reuse existing machinery rather than invent new cert types.

### Berths

Most useful links will be scoped to a berth or to an app-defined subset inside a berth.
A whole-team, all-apps link is likely too broad for a first version.

### Integration Modes

Linked teams should not multiply the current two local teammate integration modes into a full role system.
Witnessed proposal routing is the natural fit:
a bridge produces a signed proposal, and local automatic integrators retain responsibility for endorsement and merge under unchanged local rules.

### Hub

The Hub must remain the gateway for Small Sea internet traffic.
When a bridge does read Team B data to form an attestation, that I/O still flows through that bridge's local Hub.
Team links must not create a path where an app bypasses the local Hub to talk directly to a remote team's storage, notification endpoint, or peer device.

### Manager

Only Small Sea Manager reads and writes Core berth databases directly.
If bridges and links become a Manager feature, other apps should observe link-derived session or proposal information through the Hub rather than opening Core themselves.

---

## Related Work to Learn From

### SPKI/SDSI

SPKI/SDSI is a strong conceptual ancestor for decentralized authorization.
It has signed delegation, *local names*, threshold subjects, validity conditions, and no need for a single global naming authority.
The local-names idea is especially apt here: Team A's name for Team B is really Team A's name for the people it reaches Team B through.

Reference: <https://www.rfc-editor.org/rfc/rfc2693.html>

### Keybase Teams

Keybase teams use signed chains for team operations, subteams, membership, and key rotation.
The useful lesson is that team membership and key-management facts can be represented as inspectable signed history.
The caution is that subteam hierarchies can become organization machinery.
Small Sea wants signed team relationships without making hierarchy the default answer.

Reference: <https://book.keybase.io/docs/teams/sigchain>

### Matrix Rooms and Restricted Rooms

Matrix has concrete machinery where membership in one room can affect eligibility to join another room.
That resembles one possible linked-team purpose: "membership over there satisfies a condition over here."
The caution is complexity.
Small Sea should learn from the shape without inheriting full federated room-state authorization.

Reference: <https://spec.matrix.org/latest/client-server-api/#restricted-rooms>

### Tahoe-LAFS

Tahoe-LAFS is useful for thinking about capability-shaped access.
Read and write authority can be represented by cryptographic capabilities rather than centralized accounts.
The caution is that bearer capabilities are socially blunt.
Small Sea likely needs signed constitutional grants and auditable bridge history, not only possession of a string.

Reference: <https://tahoe-lafs.readthedocs.io/en/latest/architecture.html>

### Macaroons

Macaroons are useful for caveated delegation.
A grant can be attenuated with conditions such as scope, time, or additional checks.
That maps naturally to bridge policies, even if Small Sea's durable authority should be signed Core history rather than opaque bearer tokens alone.

Reference: <https://research.google/pubs/macaroons-cookies-with-contextual-caveats-for-decentralized-authorization-in-the-cloud/>

### Secure Scuttlebutt

Secure Scuttlebutt is relevant for subjective replication.
Participants choose whom to follow and replicate based on local social trust rather than a central server.
That follow-the-people model is close to the bridge idea: you replicate across a boundary because a person you trust sits on it.

Reference: <https://ssbc.github.io/scuttlebutt-protocol-guide/>

### Radicle

Radicle is relevant because it is Git-native, peer-to-peer, and collaboration artifacts such as issues and patches are replicated as part of the collaboration substrate.
Small Sea can learn from proposal and review artifacts that do not require a central project server.

Reference: <https://radicle.xyz/>

### AT Protocol

AT Protocol is useful for its separation of personal data repositories, identity, routing, indexing, and application views.
It is less directly about private team governance, but it is a good reference for separating durable signed data from discovery and service roles.

Reference: <https://atproto.com/guides/overview>

### MLS

Messaging Layer Security is relevant to group key management, especially if read sharing eventually requires efficient encryption across changing membership.
It should not be mistaken for a governance system.
Small Sea would still need to define how team Core history authorizes any MLS-like group changes.

Reference: <https://www.rfc-editor.org/rfc/rfc9420.html>

---

## Open Arguments

These should stay unresolved until they are forced by a concrete design.

1. Witness or router by default, and is router ever needed for the first useful version?
2. What is the stable cryptographic name of a team, and is it ever anything other than the thing a bridge person vouches for?
3. Should v1 support read sharing at all, or only recognition and witnessed proposal routing?
4. How many bridges should a purpose require, and how is bridge quorum expressed relative to ordinary endorsement thresholds?
5. When a bridge person leaves, loses a device, or is excluded, what happens to the links they carried?
6. If Team B forks, and Team A's bridges land on different tips, how does Team A present and resolve that?
7. Should cross-team input ever be an automatic local publication, or must it always arrive as a proposal?
8. How should a broken or revoked bridge surface?
   It should probably become a visible blocked relationship, not a silent fallback.
9. Are bridges and links per-team only, or first-class per-app or per-berth records?
10. How much bridge and link metadata is personal data deserving commitment-and-payload handling?
11. When is a shared child team the better answer than a bridge, and can Manager guide that choice?
12. What is the minimum useful UX?
    If Manager cannot explain a bridge clearly to a non-protocol person — who is trusted, for what, and why — the design is too abstract.

---

## Candidate First Protocol Slice

A deliberately narrow first version might be:

- A team has a stable cryptographic identity that a person can vouch for.
- A dual-member person publishes the `identity_link` that makes them a candidate bridge.
- Team A records a unilateral, scoped bridge designation for that person to Team B.
- A bridge carries bridge-witnessed remote proposals into a specific Team A berth under the witness model.
- Cross-team input is never an ordinary automatic publication.
- Team A automatic integrators endorse or reject those proposals under unchanged local rules.
- No bilateral link treaty is required.
- No cross-team plaintext sharing is included yet.
- No remote membership expansion is required.

This would test the core idea:
small teams can create meaningful, signed relationships through trusted people without becoming one team.

Only after this works should the design add bilateral links, read sharing, or delegation.

---

## Design Smells

These are warning signs that the feature is drifting away from the Small Sea model.

- The link requires Team A to import Team B's whole membership roster by default.
- A remote team can mutate local Core history without a local endorsement step.
- A team is reified as a standalone principal with its own key, instead of being reached through people.
- A bridge becomes an unaccountable oracle whose attestations are not signed, inspectable, or revocable.
- Link policy becomes a general-purpose role language.
- Recognition or routing is treated as transitive without an explicit grant.
- Apps learn to bypass the Hub because "the link already says they can."
- Friendly names become authority.
- A link is valid because of latest-arrival row order rather than signed, anchored history.
- Read sharing is treated as enforceable after plaintext reaches a remote endpoint.
- The Manager UI cannot explain who is trusted, for what, and why.

---

## Current Leaning

The most promising shape is:

> Another team is reached through the people who belong to both teams.
> A bridge teammate is a recognized local principal who witnesses bounded facts about the remote team into local Core history.
> The first version should support recognition and witnessed proposal routing, with a quorum of bridges where the stakes warrant it.
> Read sharing, bilateral treaties, and delegation should come later, after the bridge model is solid.

This keeps team size small while making room for larger human structures.
It dissolves the team-identity problem instead of inventing a team principal to solve it.
It accepts, on purpose, that a link lives and dies with the people who carry it.
