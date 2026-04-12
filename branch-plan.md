# Branch Plan: Sender-Key Runtime Rethink

**Branch:** `issue-44-sender-key-runtime`  
**Base:** `main`  
**Primary issue:** #44 "Revisit sender-key storage once multi-device design is clearer"  
**Related existing issues:** #59, #58, #48, #4  
**Related docs:** `architecture.md`, `packages/cuttlefish/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`, `packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-pr-45.md`,
`Archive/branch-plan-note-to-self-shared-device-local-split.md`,
`Archive/branch-plan-device-linking.md`,
`Archive/branch-plan-joining-device-bootstrap.md`,
`Archive/branch-plan-identity-model-rethink.md`

## Context

Issue #44 exists because the current sender-key storage story was knowingly a
first-milestone shortcut.

Current code keeps `team_sender_key` and `peer_sender_key` in device-local
NoteToSelf storage and lets the Hub read that runtime state directly. Invitation
flows also still distribute sender-key material out of band.

That was acceptable for the first encrypted path, but the repo has since
clarified two deeper facts:

- the trust model is now device-only and per-team
- linked devices are expected to become first-class runtime actors (#59), not
  just first-class Git signers

Recent research against public Signal material also sharpens the mental model we
should pressure-test:

- pairwise control-plane sessions are device-specific
- group data-plane encryption uses sender-key style broadcast
- public Signal sender-key code appears to key group sender sessions by group +
  sender + device, not just by group + participant

Post-Signal protocols confirm and refine this direction:

- **Matrix / Megolm**: uses the same sender-key-over-pairwise-channel pattern.
  Their key-forwarding vulnerability (CVE-2021-40823/40824) and the 2023
  Nebuchadnezzar audit findings are cautionary evidence for specific design
  choices below (history access, membership authentication).
- **MLS (RFC 9420)**: introduces pre-published KeyPackages for fully-async
  session establishment and treats each device as a separate leaf (no built-in
  user-groups-devices abstraction). Its commit-ordering requirement confirms
  that a serialization point (our Manager) is load-bearing, not just
  convenient.
- **Wire**: migrated from full-pairwise to MLS after confirming that O(devices²)
  ciphertext fan-out doesn't scale. Validates the sender-key-for-data-plane
  choice even at modest group sizes.

This branch is therefore a planning branch first. It should settle the written
model and the cut lines before a larger implementation pass starts.

## Working Direction To Pressure-Test

Start from this provisional direction and try to falsify it:

1. use pairwise device-specific channels for the control plane: sender-key
   distribution, rotation, linked-device bootstrap, and membership-change
   notices
2. use sender-key broadcast for the data plane: steady-state encrypted team-app
   bundles
3. treat sender-key ownership as device-scoped rather than member-scoped unless
   review uncovers a compelling reason otherwise
4. keep mutable sender-key runtime device-local; do not sync it through team
   DBs or shared NoteToSelf DBs
5. distinguish durable local secrets from high-churn runtime state:
   - OS secret store or enclave may hold long-lived secrets or a wrapping key
   - a device-local encrypted state store may hold sender-key chains, receiver
     chains, skipped keys, and pairwise ratchet state

## Design Positions

Positions this branch takes based on the working direction and protocol
evidence. These are the answers the plan commits to unless review falsifies
them.

### P1. Device-scoped sender keys, no syncing of sender chain state

Each device owns its own sender key chain. Devices never share or merge a
mutable sender chain. This is confirmed by Signal's actual implementation,
Megolm's design, and MLS's per-device-leaf model.

### P2. New devices decrypt from join-time forward only

When a new linked device joins, it receives fresh SenderKeyDistributionMessages
from each active sender device. It does **not** receive historical sender keys
and cannot decrypt bundles uploaded before it joined.

Rationale: Matrix's key-forwarding mechanism (sharing old session keys with new
devices) was the source of their most serious E2EE vulnerability. A device that
didn't exist yet has no business decrypting pre-join data. Historical access, if
ever needed, should be a separate carefully-authenticated mechanism (future
issue).

### P3. Sender key distribution requires cryptographic membership verification

A device must verify wrasse-trust certs (a valid `membership` or `device_link`
chain back to the team root) before accepting a SenderKeyDistributionMessage or
distributing its own sender key to a new device. Cloud storage contents alone
are not sufficient evidence of membership.

Rationale: the largest class of vulnerability in the 2023 Matrix audit was
membership changes authenticated by the server rather than cryptographically.
Our cloud storage is equally untrustworthy.

### P4. Manager serializes control-plane state changes

The Manager's role as the single writer to team DBs is load-bearing for crypto
ordering, not just a DB-access pattern. Sender key rotations and membership
changes go through the Manager, which provides a natural serialization point.
This avoids MLS's hardest open problem (concurrent commit ordering in
decentralized deployments).

### P5. Rejoin after extended absence is fresh distribution, not replay

If a device has been offline through multiple sender key rotations, it should
not attempt to reconstruct intermediate states. On reconnect, it requests fresh
SenderKeyDistributionMessages from each active sender device. This follows
MLS's Quarantined-TreeKEM insight: blanking absent devices and re-bootstrapping
is cheaper and more robust than maintaining replay chains.

### P6. Issue #4's schema proposal for sender key storage is superseded

Issue #4 proposed `own_sender_key` in NoteToSelf (synced) and `peer_sender_key`
in team DB (synced). This branch's direction supersedes that: both belong in the
device-local store only. The schema sketches in #4 should be updated once this
plan stabilizes.

## Concrete Steady State To Explain Clearly

Use this as the baseline example the branch must keep returning to:

- Alice has devices `D` and `G`
- Bob has devices `E` and `F`
- `D` and `G` each have their own team sender-key state
- `E` and `F` each keep local receiver state for `D`'s sender-key stream and
  for `G`'s sender-key stream
- `D<->E`, `D<->F`, `D<->G`, `G<->E`, and `G<->F` pairwise channels exist only
  for control-plane work, not for every bundle upload
- when `D` uploads a bundle, it encrypts once with `D`'s sender-key state;
  `E`, `F`, and `G` decrypt that same ciphertext using their own local receiver
  state for sender device `D`
- `D<->G` (intra-Alice) is not special: `G` also has its own sender-key stream,
  and `D` keeps receiver state for `G` the same way `E` does. Same-user
  device-to-device is just another instance of the cross-device pattern, not a
  separate mechanism.
- the Megolm/Sender-Key chain ratchet is one-way (HMAC-based), so sharing state
  at position N naturally gives access from N forward without exposing history.
  This is what makes P2 (join-time-forward) honest at the crypto level.

If this story turns out to be wrong or too costly, the branch should say
exactly where it breaks.

## Proposed Goal

After this planning branch:

1. the repo has one coherent written model for multi-device encrypted team
   runtime
2. the design clearly distinguishes control plane from data plane
3. the design clearly distinguishes device-scoped sender runtime from synced
   team metadata
4. the branch records what current code is temporary scaffolding versus
   intended long-term direction
5. follow-on implementation work can be split into reviewable branches with
   explicit validation criteria

## Non-Goals

- landing the full multi-device sender-key runtime
- landing device-aware peer routing
- finalizing joining-device UX
- deciding the full OS-keychain integration story
- claiming certainty about private Signal implementation details beyond public
  evidence

## Questions To Lock Early

### 1. Sender identity shape

- Is the steady-state sender-key namespace keyed by device, by member, or by
  some hybrid?
- If device-scoped, what concrete identifier should the Hub and Manager use in
  stored ciphertext metadata and local lookup tables?

### 2. Local storage boundary

- Which fields count as durable secrets?
- Which fields count as mutable runtime?
- Which of those belong in a device-local encrypted DB versus a platform secret
  store?

### 3. Recipient-state requirements

- What exact receiver state must each device keep per sender device?
- What replay or skipped-key retention is needed for honest historical
  decryptability?

### 4. Bootstrap and history

Position P2 says: new devices decrypt from join-time forward only. Questions
that remain:

- What is the concrete bootstrap sequence? Proposed: the existing device sends
  fresh SenderKeyDistributionMessages over pairwise channels for each active
  sender device in the team. The new device can decrypt any bundle uploaded after
  it receives those distributions.
- How does the new device establish pairwise channels with every other device
  async? Pre-published key bundles (analogous to MLS KeyPackages) in cloud
  storage are the likely mechanism. Note that `cuttlefish.prekeys` already has
  the right shape. (Designing and implementing this is a follow-on issue, not
  this branch.)
- What about the rejoining-after-absence case (P5)? Is it identical to new
  device bootstrap, or does it differ?

### 5. Scalability

- What work scales with number of recipient devices?
- What work scales only on control-plane events rather than every bundle upload?
- What upper-bound assumptions about device count are acceptable for Small Sea?

### 6. Sender key rotation triggers

- Membership changes (add or remove) clearly require rotation — all protocols
  agree on this.
- Should sender keys also rotate periodically (every N messages or every T
  hours) for post-compromise security? A 2023 formal analysis of Signal's
  Sender Keys found that rotating only on removal is insufficient for PCS. At
  Small Sea's scale (small teams, infrequent messages), periodic rotation is
  cheap.
- This branch should take a position on the trigger policy even if the
  implementation is a follow-on issue.

### 7. Hub and Manager boundary

- Should the Hub continue to read local crypto runtime state directly?
- Or should this branch define a narrower local crypto-session interface even if
  the first implementation still uses SQLite-backed helpers underneath?

## Cut Line Between Issues

Provisional split:

- `#44` should settle where sender-key runtime lives, what parts are
  device-local, and whether the steady-state sender identity is device-scoped or
  member-scoped
- `#59` should settle the operational consequences once multiple linked devices
  are fully live: peer routing, notification or watch behavior, sibling-device
  download policy, and any schema or API changes needed to name device endpoints
  cleanly

If, during planning, those questions prove inseparable in practice, the branch
should say so explicitly rather than forcing an artificial split.

## Planned Outputs

1. keep iterating on `branch-plan.md` until the model is crisp enough to survive
   skeptical review
2. capture a short repo-local design note or issue-ready summary once the
   control-plane and data-plane model is stable
3. identify the smallest honest follow-on implementation slice or slices,
   including the micro tests that would prove them

## Validation

This planning branch should convince a skeptical reader that it improved the
repo if all of the following are true:

- the plan explains, concretely, how Bob's devices `E` and `F` decrypt a bundle
  uploaded by Alice's device `D`
- the plan makes clear where pairwise fanout happens and where it does not
- the plan does not require syncing or merging one mutable sender chain across
  multiple devices
- the plan respects the repo's architectural rules: Hub as gateway,
  Manager-owned team DB writes, and local-only testing where possible
- the plan makes clear that sender key distribution is gated on cryptographic
  membership verification (wrasse-trust certs), not on cloud storage contents
- the plan distinguishes confirmed public Signal evidence from Small
  Sea-specific inference; it also names post-Signal protocol evidence (Megolm,
  MLS) where relevant
- the plan leaves behind a small explicit set of unresolved questions instead of
  a fuzzy "figure it out later"
- the next implementation branch would have clear success criteria and micro
  test expectations

## Validation Evidence To Gather In This Branch

- public Signal references for Sesame, linked-device behavior, and sender-key
  naming
- repo references showing current temporary storage and bootstrap behavior
- a worked steady-state example with:
  - one sender device
  - two recipient devices
  - one second device for the sender identity
- a brief scalability accounting for:
  - steady-state bundle upload
  - adding a linked device
  - rotating a sender key
  - admitting a new teammate

## Follow-On Issues To File

Issues to create after this planning branch stabilizes. These are explicitly
out of scope for this branch but surfaced by the design work.

- **Pre-published key bundles for async session establishment**: design and
  implement MLS-KeyPackage-style prekey publishing in cloud storage so pairwise
  channels can be established without both devices being online. Builds on
  `cuttlefish.prekeys`.
- **Sender key rotation policy**: implement the rotation trigger policy settled
  in this plan (membership change + periodic). Decide concrete thresholds.
- **Historical key-sharing mechanism** (if ever needed): a carefully
  authenticated protocol for sharing old sender keys with new devices. Must
  learn from Matrix's CVE-2021-40823/40824. Explicitly deferred; P2 says
  join-time-forward for now.
- **Update #4's schema proposal**: the Cuttlefish integration issue (#4) has
  stale schema sketches that put sender keys in synced stores. Update to match
  device-local-only direction.
- **Device rejoin protocol**: implement the rejoin-after-absence flow (P5).
  May overlap with the new-device bootstrap issue (#58 follow-on).
