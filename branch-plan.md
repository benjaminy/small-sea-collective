# Branch Plan: Sender-Key Runtime Roadmap

**Branch:** `issue-44-sender-key-runtime`  
**Base:** `main`  
**Original trigger:** #44 "Revisit sender-key storage once multi-device design is clearer"  
**Active roadmap issues:** #59, #69, #43, #48, #4  
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

This branch is therefore intentionally an **organizational** branch first. It
should settle the written model, clean up the tracker shape, and leave behind a
multi-branch roadmap before a larger implementation pass starts.

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

Important corollary: join-time-forward is only honest if the bootstrap flow also
ensures the new device can obtain a readable **current baseline** for the team
after join. For Small Sea's git-based team state, that likely means some form
of post-join resealed snapshot or equivalent current-state export, so the new
device does not depend on decrypting pre-join artifacts just to see the latest
team state.

More generally, this suggests a default Small Sea bootstrap invariant:

- whenever something new joins and needs persistent shared state — a new team
  member, a newly linked device, or an already-linked device joining a specific
  team — the sponsoring device is responsible for ensuring that a readable
  current baseline is available in cloud storage under access the joiner can
  legitimately use
- for git-backed state, that may mean publishing a fresh Cod Sync snapshot,
  bundle chain tip, or equivalent resealed current-state export after admission
- future encrypted deltas then continue from that fresh baseline; they do not
  require handing over old long-lived sender-key history

### P3. Sender key distribution requires cryptographic membership verification

A device must verify wrasse-trust certs (a valid `membership` or `device_link`
chain back to the team root) before accepting a SenderKeyDistributionMessage or
distributing its own sender key to a new device. Cloud storage contents alone
are not sufficient evidence of membership.

Rationale: the largest class of vulnerability in the 2023 Matrix audit was
membership changes authenticated by the server rather than cryptographically.
Our cloud storage is equally untrustworthy.

### P4. Manager serializes control-plane decisions; distribution is device-to-device

The Manager's role as the single writer to team DBs is load-bearing for crypto
ordering, not just a DB-access pattern. The Manager owns the *decisions* —
rotate, admit, remove — and the team DB writes that record them. This provides a
natural serialization point that avoids MLS's hardest open problem (concurrent
commit ordering in decentralized deployments).

The actual pairwise SenderKeyDistributionMessages travel over encrypted
device-to-device logical channels, but their transport must still respect Small
Sea's architecture: in production all internet-facing traffic goes through the
Hub. That still allows direct Hub-to-Hub transport between devices. The Manager
triggers the rotation; each device then distributes its new sender key to each
peer device via Hub-mediated transport, not by bypassing the local Hub with
ad-hoc application-level network paths.

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

## Why This Branch Should Stay Organizational

The issue graph is currently less clear than the technical direction:

- `#44` is still framed around the old single-DB storage question, but `#61`
  already moved sender-key runtime to device-local NoteToSelf storage
- `#59` is the real open runtime umbrella, but it currently mixes at least
  three future branches: sender identity semantics, historical/bootstrap
  encrypted access for linked devices, and peer routing / watch behavior
- `#43` is already the natural home for encrypted sender-key rotation and
  redistribution once the runtime model is clearer
- `#48` is now more clearly about steady-state NoteToSelf refresh and discovery,
  not encrypted team runtime itself

If this branch jumped directly into code, it would likely blur those boundaries
again.

## Proposed Goal

After this planning branch:

1. the repo has one coherent written model for multi-device encrypted team
   runtime
2. the design clearly distinguishes control plane from data plane
3. the design clearly distinguishes device-scoped sender runtime from synced
   team metadata
4. the issue tracker reflects current reality rather than outdated milestone
   assumptions
5. follow-on implementation work is split into reviewable branches with
   explicit validation criteria and cleaner issue ownership

## Non-Goals

- landing the full multi-device sender-key runtime
- landing device-aware peer routing
- finalizing joining-device UX
- deciding the full OS-keychain integration story
- claiming certainty about private Signal implementation details beyond public
  evidence

## Questions To Lock Early

### 1. Sender identity shape

P1 commits to device-scoped. Remaining question:

- What concrete device identifier should the Hub and Manager use in stored
  ciphertext metadata and local lookup tables? (Per-team device UUID from
  wrasse-trust? Something else?)

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

P2 commits to join-time-forward only. P5 commits to fresh-distribution for
rejoin. These are now design constraints, not open questions. Remaining
questions (owned by #69):

- What is the concrete bootstrap sequence? Proposed: the existing device sends
  fresh SenderKeyDistributionMessages over pairwise channels for each active
  sender device in the team. The new device can decrypt any bundle uploaded after
  it receives those distributions.
- What is the default **baseline publication** strategy? Current direction: the
  inviter / sponsor is responsible for making the relevant persistent shared
  state available in cloud storage under access the joiner will have after
  admission. For git-backed state, that may mean publishing a fresh Cod Sync
  snapshot or equivalent resealed baseline.
- What is the concrete **current baseline** mechanism for git-backed team data?
  Join-time-forward only works if a newly linked device can read the latest team
  state without needing pre-join sender keys. `#69` must therefore include
  either:
  - a resealed fresh baseline / full snapshot after join, or
  - an equivalent current-state export mechanism
  A design that omits this would strand the new device behind unreadable
  history.
- How does the new device establish pairwise channels with every other device
  async? Pre-published key bundles (analogous to MLS KeyPackages) in cloud
  storage are the likely mechanism. Note that `cuttlefish.prekeys` already has
  the right shape.
- Is the rejoining-after-absence case (P5) identical to first-time team
  bootstrap, or does it differ in practice?

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

## Issue Topology After Tracker Cleanup

`#44` is now closed as superseded (`#61` handled the original storage split).
The remaining work is distributed across four open issues:

- **#59** — steady-state runtime identity and peer routing: sender-device
  identity semantics, Hub routing/notification/watch behavior for sibling
  devices
- **#69** — encrypted team bootstrap for a newly linked device: how a device
  that already belongs to an identity becomes an honest recipient for an
  already-encrypted team (owns the P2/P5 implementation)
- **#43** — encrypted sender-key rotation and redistribution: moving beyond
  invitation-token bootstrap to real control-plane rekey (owns the Q6 rotation
  policy implementation)
- **#48** — steady-state NoteToSelf refresh and team discovery across devices:
  not encrypted-team-runtime per se, but the Manager-level plumbing that lets a
  second device learn about teams created elsewhere

These four issues are intentionally non-overlapping. If future work reveals that
two of them are inseparable in practice, the branch doing the work should say so
explicitly rather than silently merging scopes.

## Planned Outputs

1. keep iterating on `branch-plan.md` until the model is crisp enough to survive
   skeptical review
2. comment on the key GitHub issues so their current scope matches the repo's
   implemented state and the updated design direction
3. close or supersede stale issues where the original problem statement is no
   longer the real problem
4. open any missing follow-up issue needed to keep future implementation
   branches narrow and honest
5. leave behind a concrete multi-branch roadmap, including the micro tests that
   should prove each implementation slice

## Tracker Actions Taken

All completed in this branch:

1. ~~update `#59`~~ — commented with clarified mental model (pairwise control
   plane, sender-key data plane, device-scoped sender identity)
2. ~~close `#44`~~ — superseded; `#61` handled storage split, remaining work
   belongs to #59/#69/#43
3. ~~open `#69`~~ — encrypted team bootstrap for newly linked devices, distinct
   from identity bootstrap (#58), NoteToSelf refresh (#48), and runtime
   identity (#59)
4. ~~comment on `#4`~~ — noted that the old synced-schema sketch is superseded by
   device-local-only direction
5. ~~comment on `#43`~~ — scoped to rotation/redistribution only

## Likely Future Branch Sequence

### 1. This branch: roadmap and issue cleanup

- clarify the steady-state model
- align issue scopes with current repo reality
- identify the next smallest honest implementation slice

### 2. Sender-device runtime identity (#59, first slice)

- choose and implement the concrete sender-device identifier model
- update local sender / receiver runtime lookups
- add micro tests for multiple linked devices encrypting independently

### 3. Encrypted team access for a newly linked device (#69)

- implement P2 (join-time-forward) and P5 (rejoin = fresh distribution)
- design the async pairwise channel setup (pre-published key bundles)
- add micro tests for a new linked device decrypting future team bundles
- add micro tests proving the historical-access boundary is enforced

### 4. Encrypted sender-key rotation and redistribution (#43)

- move beyond invitation-token bootstrap
- implement control-plane redistribution over the intended encrypted path
- implement the rotation trigger policy settled in Q6
- add micro tests for routine rotation and membership-change rekey

### 5. Device-aware peer routing and watches (#59, second slice)

- distinguish sibling devices as runtime endpoints where needed
- settle Hub routing / notification / watch semantics
- add micro tests proving multiple linked devices stay live without conflation

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
- the issue tracker would read as a roadmap instead of a pile of partially
  outdated placeholders

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

## Notes For Existing Issues

Design details surfaced here that should be folded into the relevant existing
issues when those branches start, rather than filed as separate issues:

- **#69** should own the pre-published key bundle mechanism for async pairwise
  channel setup (MLS-KeyPackage-style, building on `cuttlefish.prekeys`). It
  should also own the rejoin-after-absence flow (P5), since the mechanism is
  likely identical to first-time team bootstrap.
- **#43** should own the rotation trigger policy (Q6: membership change +
  periodic for PCS). Concrete thresholds belong there.
- **#4** already has a comment noting the stale synced-schema sketch. When #4
  is next touched, the schema section should be rewritten to match device-local-
  only direction.

## Deferred Design Questions

Not owned by any current issue. File new issues only if/when these become
blocking:

- **Historical key-sharing mechanism**: a carefully authenticated protocol for
  sharing old sender keys with new devices, if join-time-forward (P2) proves
  too restrictive in practice. Must learn from Matrix's CVE-2021-40823/40824.
  Explicitly deferred.
