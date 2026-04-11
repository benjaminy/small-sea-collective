# Bootstrap NoteToSelf Through Hub-Owned Transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Context

Identity bootstrap now works end-to-end in the local-only case:

1. the joining device creates a join request
2. the existing device admits it
3. the existing device returns an encrypted, signed welcome bundle
4. the joining device pulls NoteToSelf
5. the joining device verifies that the bundle was signed by a device in the pulled identity

That flow currently relies on `LocalFolderRemote`, which is useful for proving the trust model but not for real cloud-backed use.

The joining device still has the same important starting constraints:

- no existing identity
- no normal NoteToSelf DB/session state
- no device-local cloud credentials yet
- only the welcome bundle and its `remote_descriptor`

The authorizing device, by contrast, is a normal live installation with a Hub, local credentials, and a NoteToSelf repo it can already push and read.

## Branch Goal

Refactor the architecture so that the **Hub** can perform NoteToSelf bootstrap cloud transport before normal participant/session initialization exists, and prove that path with a cloud-shaped local test harness.

More concretely, after this branch:

- the joining device's **Manager** still does not talk to the internet directly
- the joining device can ask its local **Hub** to fetch NoteToSelf bootstrap data using only the welcome bundle's remote descriptor
- the authorizing device can push the relevant NoteToSelf state through normal Hub-owned cloud transport
- the flow is proven with local MinIO/S3-style tests rather than `LocalFolderRemote`

## What This Branch Is Really About

This branch is **not** mainly about S3.

S3/MinIO is just the safest proof environment because it lets us validate the transport architecture without also solving OAuth bootstrap.

The real problem is:

> How can Hub perform the cloud read/write work needed for identity bootstrap before normal NoteToSelf participant/session state exists on the joining device?

That is the load-bearing design question for this branch.

## Key Decisions

### Decision 1: Manager does not read cloud storage directly

This branch should preserve the repo's architectural rule:

- the Hub is the only Small Sea component that talks to the internet
- Manager may orchestrate bootstrap, but it must do so through Hub-owned APIs or Hub-owned library surfaces

So this branch should **not** solve bootstrap by teaching Manager to use cloud adapters directly.

### Decision 2: The branch centers on a bootstrap-capable Hub path

The branch goal is to create a **bootstrap-only transport path** in or for the Hub that:

- does not require a preexisting NoteToSelf session
- does not require NoteToSelf DB reads on the joining device
- can consume the welcome bundle's remote descriptor directly
- is clearly separated from normal post-bootstrap session flows

This is the main architectural outcome we want from the branch.

### Decision 3: S3/MinIO is the required proof

This branch should prove the new transport path with MinIO / S3-shaped local tests.

That is a scope limiter, but more importantly it is a **validation strategy**:

- MinIO gives us a real cloud-shaped path
- the tests stay local and deterministic
- we avoid mixing the Hub refactor with OAuth bootstrap policy

### Decision 4: OAuth bootstrap is explicitly deferred

Dropbox / GDrive / other credential-bearing bootstrap flows are out of scope for this branch.

This branch should leave them as a follow-up, not pretend they are almost solved.

## Non-Goals

- no Dropbox bootstrap
- no GDrive bootstrap
- no new OAuth UX
- no redesign of the identity-join trust model
- no changes to the signed welcome-bundle verification logic beyond whatever small descriptor additions are required
- no general “make bootstrap work for every cloud backend” claim

## Result We Want

At the end of this branch, a critic should be able to say:

- yes, Hub can now handle NoteToSelf bootstrap transport before normal identity/session state exists
- yes, Manager still obeys the “Hub as gateway” rule
- yes, the result works in a real cloud-shaped environment, not just shared local filesystem tests
- yes, the branch stays honest about what remains unsolved for OAuth providers

## Architectural Invariants

These should stay true throughout the branch:

- joining-device bootstrap transport must not depend on `GET /session/info`
- joining-device bootstrap transport must not require an already-initialized NoteToSelf DB/session
- Manager must not absorb duplicate cloud-adapter logic
- the bootstrap transport path must be clearly narrower than ordinary Hub session behavior
- device-local secrets still stay local; the welcome bundle should not become a general credential dump

## Required Descriptor Change

The welcome bundle's `remote_descriptor` must carry enough information for the Hub bootstrap path to locate the NoteToSelf CodSync remote.

For this branch that at minimum means:

- `protocol`
- `url`
- `bucket` (which is protocol specific, and will eventually generalize, but we're limiting the scope to minio/s3)

The current descriptor shape is too thin because the joining device cannot derive the NoteToSelf bucket name before it has fetched NoteToSelf itself.

So this branch should explicitly include `bucket` in the bootstrap descriptor.

## Scope Shape

### In scope

- define the branch around a bootstrap-capable Hub transport path
- add whatever bootstrap-specific Hub API/library surface is needed so Manager can request NoteToSelf fetch without a normal NoteToSelf session
- include `bucket` in the welcome bundle's `remote_descriptor`
- make the authorizing-side NoteToSelf push use real Hub/cloud transport for the MinIO proof path
- add MinIO/S3 micro tests for the joining-device bootstrap flow
- update docs/specs to state the new boundary clearly

### Out of scope

- designing the OAuth bootstrap credential story
- solving provider auth for a brand-new device
- broad Hub session redesign
- background NoteToSelf refresh/discovery UX after bootstrap
- unifying every bootstrap transport under one final abstraction if that adds unnecessary churn

## Recommended Plan Shape

This branch plan should be judged against these questions:

1. Does it make Hub, not Manager, the owner of bootstrap cloud transport?
2. Does it avoid requiring fake/throwaway NoteToSelf identity state on the  joining device?
3. Does it prove the architecture with MinIO/S3 rather than just another local filesystem shortcut?
4. Does it stay honest that OAuth bootstrap remains unsolved?

If the answer to any of those is “no”, the branch has drifted.

## Validation

The branch should not be considered complete unless it proves all of these:

- the welcome bundle carries a `remote_descriptor` with `bucket`
- the authorizing device can publish the relevant NoteToSelf state through a real cloud-shaped path
- the joining device can fetch NoteToSelf through Hub-owned bootstrap transport without a preexisting NoteToSelf session
- the joining device does not need a fake fully initialized NoteToSelf DB before the fetch
- the bootstrap flow works against local MinIO/S3 tests
- unsupported bootstrap providers fail clearly rather than silently falling back to local-only assumptions
- bootstrap-scoped auth, whatever form it takes, is rejected by ordinary
  session-only endpoints such as `/session/info` and `/cloud_file`

## Repo Findings From The Current Code

After digging through the Hub/Manager/CodSync code, the important findings are:

### 1. Hub startup is not the hard part

`SmallSeaBackend.__init__` can already start with zero participant state.
It creates:

- `root_dir`
- `Logging/`
- `small_sea_collective_local.db`

It does **not** require an existing `Participants/` tree.

So the branch does **not** need to invent a “fake Hub startup” story.

### 2. Session creation is the actual choke point

`_lookup_session(...)` is cheap: it reads one row from the Hub's local DB.

But normal session creation is tightly coupled to live participant state:

- `request_session(...)` calls `_find_participant(...)`
- `request_session(...)` validates berth existence through `_resolve_berth(...)`
- `confirm_session(...)` resolves real `participant_id`, `team_id`, `app_id`,
  and `berth_id`

That is the real problem this branch has to solve.

### 3. The S3 read path already exists

`proxy_cloud_file(...)` already has an anonymous S3 code path:

- no credentials required
- no participant NoteToSelf DB reads required
- no special adapter extraction required just to prove the transport path

That is good news: this branch does **not** need a deep S3 adapter refactor.

### 4. Authorizing-side push is a supporting task, not the main refactor

The authorizing side already has the ingredients for real Hub-backed push:

- `TeamManager.push_team(...)` already uses `SmallSeaRemote` through Hub
- NoteToSelf sessions already exist and are used in invitation flows
- `SmallSeaRemote` + `/cloud_file` + `/cloud/setup` already work with MinIO

So this branch probably only needs a NoteToSelf version of that pattern, not a
second deep transport design on the authorizing side.

### 5. Provisioning is still supposed to stay local-only

The current draft overreaches here.

Per [packages/small-sea-manager/spec.md](/Users/ben8/Repos/small-sea-collective/packages/small-sea-manager/spec.md#L23), `provisioning.py` is still the
local-only layer. Cloud I/O belongs in the session/orchestration layer.

So this branch should **not** move network behavior into:

- `_push_note_to_self_to_local_remote(...)`
- `_remote_from_descriptor(...)`
- `provisioning.bootstrap_existing_identity(...)`

Those functions may need to be split or slimmed down, but they should not grow
network responsibilities.

## What The Current Proposal Gets Wrong

### Problem 1: A placeholder row in the normal `session` table is too risky

The draft's `create_bootstrap_session(...)` idea is directionally useful, but
the specific “insert a fake normal session row with placeholder IDs” shape is
too optimistic.

Why:

- every protected HTTP endpoint currently uses the same generic
  `_require_session(...)` dependency
- `/session/info`, `/cloud_file`, `/peer_cloud_file`, `/notifications/watch`,
  and other routes would all accept that token unless explicitly changed
- some of those paths would then try to interpret placeholder
  `participant_id`/`berth_id` values as real state
- several backend methods call `attached_note_to_self_connection(...)`, which
  would happily create fake local DBs if pointed at nonexistent participant
  paths

So the plan should **not** commit to “normal session row + placeholder IDs”
unless the branch also commits to real scope separation.

### Problem 2: “No changes to existing session logic” is not believable

The draft says:

- add bootstrap session creation
- no changes to `_lookup_session`, `proxy_cloud_file`, or existing session logic

Repo research says that is too optimistic.

At minimum, the branch must introduce one of these:

- a distinct bootstrap auth dependency / endpoint path
- or an explicit session kind/scope check that ordinary routes reject

Either way, the bootstrap capability must be **narrower than ordinary
sessions in enforceable code**, not just by convention.

### Problem 3: The draft pushes network orchestration into provisioning

That conflicts with the current Manager architecture and would make the branch
harder to reason about later.

The plan should keep this split:

- **Hub / client / manager session layer**: bootstrap transport orchestration
- **provisioning.py**: decrypt/init/verify/local DB writes only

### Problem 4: The bootstrap auth surface is broader than it needs to be

Even for S3-only, a generic unauthenticated `POST /sessions/bootstrap` that
creates a token usable against arbitrary `{protocol,url,bucket,path}` is
broader than necessary.

For this branch, the plan should require the bootstrap auth artifact to be
scoped at least to:

- bootstrap transport only
- supported bootstrap protocols only
- ideally the specific descriptor carried in the welcome bundle, or something
  very close to that

We do **not** have to decide the exact mechanism yet, but the scope should be
an explicit branch requirement.

## Refined Direction For This Branch

The repo research points to a better branch shape:

### 1. Add a bootstrap-scoped Hub auth path

The branch probably does need some bootstrap auth/session concept.

But the plan should describe it like this:

- a Hub-issued bootstrap-scoped auth artifact
- usable only for bootstrap transport
- not accepted by ordinary session routes
- not dependent on a preexisting participant/session/berth lookup

Whether that is implemented as:

- a dedicated table
- a typed row in the existing session table
- a separate endpoint family
- or some combination

should be left for the next refactor-planning pass.

### 2. Keep bootstrap transport in the session layer

The joining-side network flow should move upward, not downward:

- Manager/session layer or a new bootstrap-orchestration helper should:
  - ask Hub for bootstrap-scoped transport
  - build `ExplicitProxyRemote`-like fetches through Hub
  - run CodSync fetch
- provisioning should remain responsible for:
  - join-state persistence
  - local filesystem setup
  - welcome-bundle verification
  - final local DB writes

### 3. Treat authorizing-side push as a modest supporting change

This branch should make NoteToSelf push use the already-established
`SmallSeaRemote` / Hub pattern for the MinIO proof path.

That should be framed as:

- add a NoteToSelf push path in the session layer
- keep it separate from the deeper joining-side bootstrap refactor

### 4. Prove the whole thing with MinIO/S3

MinIO remains the required proof because it exercises:

- real cloud-shaped transport
- real Hub-owned read/write paths
- no local filesystem shortcut
- no OAuth expansion

## Revised Concrete Change Areas

### `small-sea-hub/backend.py` and `server.py`

- add a bootstrap-scoped transport/auth path
- ensure ordinary session-only routes reject bootstrap-scoped auth
- reuse existing anonymous S3 read logic where possible
- keep bootstrap support explicit and narrow

### `small-sea-client/client.py`

- add whatever client call is needed to obtain bootstrap-scoped Hub transport

### `small-sea-manager/manager.py`

- own the network/orchestration side of identity bootstrap
- own the NoteToSelf-through-Hub push path for the authorizing device
- keep provisioning local-only

### `small-sea-manager/provisioning.py`

- update welcome-bundle descriptor construction to include `bucket`
- if needed, split current bootstrap code into:
  - local-only finalize logic
  - session-layer transport orchestration elsewhere

### Tests

- MinIO proof for authorizing-side NoteToSelf push through Hub
- MinIO proof for joining-side bootstrap fetch through Hub
- rejection tests showing bootstrap-scoped auth cannot use ordinary
  session-only endpoints
- existing localfolder bootstrap tests remain valid

## Revised Implementation Order

### Phase 1: Lock the transport boundary

- decide and implement the narrow bootstrap-scoped Hub auth surface
- prove that it works on an otherwise blank Hub root
- prove that ordinary session routes reject it

### Phase 2: Authorizing-side NoteToSelf push through Hub

- add the NoteToSelf push path in the Manager/session layer
- prove NoteToSelf can be published to MinIO through Hub

### Phase 3: Joining-side bootstrap fetch through Hub

- move joining-side fetch orchestration into the session layer
- keep provisioning local-only
- fetch NoteToSelf through Hub-owned bootstrap transport

### Phase 4: End-to-end MinIO bootstrap

- full round-trip through Hub-owned transport
- verify that the existing signed welcome-bundle checks still pass

### Phase 5: Docs

- update specs to describe the narrowed bootstrap transport boundary
- document that S3/MinIO is supported for bootstrap in this branch
- document that OAuth bootstrap remains deferred

## Risks

- **Bootstrap auth accidentally becomes “just another session.”**
  Mitigation: make scope separation a branch requirement and test it.
- **Network code leaks into provisioning.**
  Mitigation: keep the Manager/session-layer boundary explicit in the plan and
  validation.
- **The branch quietly turns into an OAuth design exercise.**
  Mitigation: MinIO/S3-only proof remains the hard scope boundary.
- **Authorizing-side push and joining-side fetch become entangled.**
  Mitigation: treat authorizing-side push as a supporting task, not the core
  refactor.

## Questions To Carry Into The Next Refactor Pass

These are now the real next-step design questions:

- should bootstrap-scoped auth live in a dedicated table, a typed session row,
  or a separate endpoint family?
- should `/cloud_proxy` be reused directly, or should bootstrap get its own
  narrower route?
- how should the session layer expose joining-side bootstrap fetch without
  bloating `TeamManager`?
- what is the cleanest NoteToSelf push helper on the authorizing side?

Those are implementation/refactor questions. They are intentionally not locked
by this branch-shape draft.
