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

- When a new linked device comes online, what older encrypted bundles should it
  be able to decrypt?
- What sender-key snapshot or replayable-key export is required to make that
  promise honest?

### 5. Scalability

- What work scales with number of recipient devices?
- What work scales only on control-plane events rather than every bundle upload?
- What upper-bound assumptions about device count are acceptable for Small Sea?

### 6. Hub and Manager boundary

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
- the plan distinguishes confirmed public Signal evidence from Small
  Sea-specific inference
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
