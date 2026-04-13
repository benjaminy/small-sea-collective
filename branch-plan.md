# Branch Plan: Device-Aware Peer Routing and Watches

**Branch:** `issue-59-peer-routing-watches`  
**Base:** `main`  
**Primary issue:** #59 "Make linked devices first-class for sender keys and peer routing"  
**Related issues:** #43, #69, #48, #73  
**Related docs:** `architecture.md`, `packages/small-sea-manager/spec.md`,
`packages/small-sea-hub/spec.md`  
**Related archive plans:** `Archive/branch-plan-issue-59-sender-device-runtime-identity.md`,
`Archive/branch-plan-issue-69-linked-device-encrypted-team-bootstrap.md`,
`Archive/branch-plan-issue-43-sender-key-rotation.md`

## Context

Three earlier branches have already landed the core crypto/runtime primitives:

1. `#59` first slice: sender-key runtime identity is device-scoped rather than
   member-scoped.
2. `#69`: a same-member linked device can bootstrap into an encrypted team and
   become a live sender/receiver.
3. `#43`: a device can rotate its own sender key, publish redistribution
   prekeys, create encrypted redistribution artifacts, and receive them.

What is still missing is the honest steady-state runtime behavior after those
primitives exist:

- the Hub still tends to treat one team member as one runtime endpoint
- newly visible sibling devices are not promoted automatically into routine
  sender-key exchange / watch behavior
- non-removing devices do not yet notice a membership-removal adoption and then
  rotate + redistribute on their own
- `#43` currently stops at manual delivery artifacts rather than production
  transport / watch-triggered follow-through

That means the crypto pieces exist, but the repo still lacks the runtime glue
that makes multiple linked devices stay live without manual intervention.

## Proposed Goal

After this branch lands:

1. the Hub treats trusted team-device keys as runtime endpoints where that
   distinction matters, rather than collapsing everything to one endpoint per
   member
2. when a device adopts a newer team DB view that reveals a newly trusted peer
   device, it can automatically fan out its current sender-key distribution to
   that device
3. when a non-removing device adopts a membership removal in the team DB, it
   rotates its own sender key and redistributes to the still-trusted remaining
   devices
4. the runtime keeps the branch-43 transport boundary honest: production
   internet traffic still goes through the Hub, while Manager remains the only
   writer of the team DB
5. multiple linked devices for one teammate can stay live in watch / routing
   behavior without overwriting or hiding one another

## Why This Slice

This is the next natural implementation branch after `#43`.

The branch should focus on runtime orchestration, not new crypto:

- the sender-key rotation primitive already exists
- the redistribution artifact format already exists
- the linked-device bootstrap path already exists

What remains is deciding when the system should invoke those primitives and
which component owns that responsibility.

## Scope Decisions

### S1. Team DB remains the source of truth for membership and trusted devices

This branch should not invent a second synced registry for runtime endpoints.

The device set that matters for redistribution comes from:

- the locally adopted team DB
- trusted `membership` / `device_link` cert resolution
- locally available device-local sender-key runtime state

### S2. Production transport stays Hub-mediated

The branch should move beyond purely manual artifact exchange for normal runtime
behavior.

Manager still owns:

- mutating the team DB
- computing trusted-device views
- creating redistribution artifacts when asked
- owning the reconciliation logic that decides whether local rotation and/or
  redistribution is needed after an adopted team-view change

The Hub should own:

- detecting that a local adopted team view changed and scheduling a runtime
  reconciliation pass
- moving runtime payloads over Small Sea transport
- watch / notification plumbing
- device-aware endpoint fanout where internet communication is involved

Recommended seam for this branch:

- Hub detects a relevant adopted team DB change
- Hub calls a narrow Manager-owned runtime reconciliation helper
- Manager returns the actions/artifacts required
- Hub handles transport / scheduling for those artifacts

### S3. Runtime adoption is local, not globally coordinated

The repo's git-based model is eventually consistent. A device acts on the team
DB view it has adopted locally.

So this branch should be explicit:

- a non-removing device rotates only after it has pulled and adopted the
  removal locally
- a device redistributes only to the trusted peer-device set visible in its
  current local view
- there is no promise of instantaneous team-wide convergence
- after one adopted removal, multiple remaining devices may rotate
  independently; that "rotation storm" is acceptable at Small Sea scale

### S4. Automatic behavior should be narrow and inspectable

This branch should prefer a small number of explicit runtime triggers over a
general background automation jungle.

The two key triggers are:

1. newly trusted peer device appears in the adopted team view
2. trusted peer/member disappears from the adopted team view in a way that
   means this device must rotate

### S5. No revocation-cert work in this branch

This branch should react to the current local trust graph. It should not try to
solve cryptographic revocation semantics, which remain separate follow-up work.

## In Scope

### 1. Detect runtime-relevant team-view changes

Add a narrow runtime reconciliation path that compares:

- locally known sender-key / peer-device runtime state
- currently trusted peer-device keys from the adopted team DB

The reconciliation should answer at least:

- which newly trusted devices need this device's current sender key
- whether a locally adopted member removal means this device must rotate

### 2. Trigger redistribution to newly visible peer devices

When reconciliation discovers a newly trusted target device with a published
bundle, the runtime should invoke the existing redistribution primitive and hand
transport off to the Hub.

Important details:

- same-member sibling devices count as real targets
- targets without a published bundle should be skipped cleanly and retried on a
  later reconciliation pass
- redistribution should use the existing encrypted payload format from `#43`,
  not invent a second delivery format
- reconciliation should rerun on any later adopted team DB change that could
  make delivery possible, including `device_prekey_bundle` publication

### 3. Trigger local rotation after adopted member removal

For non-removing devices, this branch should add the runtime path that says:

- "I adopted a team DB view where trusted device/member set shrank"
- "that means my current sender key is no longer appropriate"
- "rotate locally and redistribute to the remaining trusted devices"

This is the missing follow-through from `#43`.

### 4. Device-aware Hub watch / routing semantics

Update the Hub's runtime handling so linked devices are not conflated when:

- watching for relevant team changes
- routing runtime fanout
- tracking which peer devices are currently live recipients
- keying in-memory watch/runtime state where `member_id` would otherwise merge
  multiple linked devices into one runtime endpoint

This does not require a full redesign of the `peer` table. It does require the
Hub's in-memory model and watch loop to stop assuming one runtime endpoint per
member.

### 5. Specs and micro tests

Update specs and add skeptical micro tests that prove the runtime orchestration
works as designed.

Minimum expected coverage:

- newly linked peer device becomes a redistribution target after the local team
  view adopts its trusted device key
- same-member sibling device is treated as a real runtime target, not skipped
- non-removing device rotates after adopting a member removal
- missing prekey bundles do not break the reconciliation round
- Hub/runtime watch logic can keep multiple linked devices for one member live
  without conflation
- local-only test setup remains sufficient; no internet dependency required

## Out Of Scope

- new sender-key crypto primitives
- invitation-flow redesign
- revocation certificates or device-removal cryptographic semantics
- periodic sender-key rotation policy (`#73`)
- NoteToSelf sync and team discovery (`#48`)
- large peer-table schema redesign unless a tiny supporting change is truly
  required
- historical sender-key access or grace-period multi-chain receive support

## Concrete Change Areas

### 1. `packages/small-sea-hub`

- watch / reconciliation loop
- runtime endpoint modeling for linked devices
- Hub-mediated delivery seam for redistribution artifacts

### 2. `packages/small-sea-manager/small_sea_manager`

- explicit reconciliation / orchestration entry points
- trusted-device enumeration for runtime decisions
- glue from adopted team view to existing `rotate_team_sender_key(...)` and
  `redistribute_sender_key(...)`

### 3. Specs

- `packages/small-sea-manager/spec.md`
- `packages/small-sea-hub/spec.md`

### 4. Tests

- new focused runtime/watch micro tests in Manager and/or Hub
- updates to linked-device / sender-key tests where runtime behavior changed

## Validation

This branch should convince a skeptical reviewer if all of the following are
true:

- the repo now has one clear runtime path from "team view changed locally" to
  "rotate and/or redistribute as needed"
- same-member linked devices and cross-member linked devices are both treated as
  real runtime recipients once trusted
- the branch does not reintroduce member-scoped conflation in Hub watches or
  runtime routing
- Manager remains the only direct writer of team DBs
- production network transport still goes through the Hub
- the branch stays honest about eventual consistency: behavior is triggered by
  local adoption, not by imaginary global consensus
- tests prove the nasty cases, not just the happy path:
  - same-member linked-device fanout
  - adopted-removal-triggered rotation on a non-removing device
  - missing-prekey skip / retry behavior
  - multiple linked devices for one member remaining distinct in runtime logic

## Open Questions

### Q1. Where should reconciliation live?

Recommended answer for this branch:

- Manager owns reconciliation logic and runtime decisions
- Hub owns watch-triggering, scheduling, and network transport

The remaining implementation question is how narrow and explicit the seam can
be, not which side should own the trust/runtime decision-making.

### Q2. What is the minimal persisted state for "already redistributed"?

If runtime reconciliation is automatic, the code likely needs a small local seam
to avoid re-sending the same current sender key to the same target forever.

The branch should choose the smallest honest state model that still allows:

- retry after missed delivery
- no conflation across different sender devices
- no dependence on synced mutable state

This state should remain device-local. It should not be stored in the shared
team DB. The exact table/location is still open and should not be prematurely
folded into unrelated prekey-material tables without a clear reason.

### Q3. How do watch-triggered retries surface partial progress?

When some trusted targets have bundles and others do not, the branch should
decide how partial completion is represented and retried without hiding failure.

At minimum, the plan should assume retries can be triggered by later adopted
team DB changes that affect deliverability, including newly published
`device_prekey_bundle` rows.

### Q4. Does device-aware runtime require shared peer-schema changes?

The runtime/watch layer definitely needs to distinguish linked devices as
separate runtime endpoints. What remains open is whether that requires:

- only an in-memory / watch-model change in the Hub, or
- a small supporting shared-schema change to peer metadata

This branch should answer that explicitly instead of drifting into an accidental
schema change mid-implementation.
