# Bootstrap NoteToSelf Through Hub-Owned Transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Context

Identity bootstrap now works end-to-end in the local-only case:

1. the joining device creates a join request
2. the existing device admits it
3. the existing device returns an encrypted, signed welcome bundle
4. the joining device pulls NoteToSelf
5. the joining device verifies that the bundle was signed by a device in the
   pulled identity

That flow currently relies on `LocalFolderRemote`, which is useful for proving
the trust model but not for real cloud-backed use.

The joining device still has the same important starting constraints:

- no existing identity
- no normal NoteToSelf DB/session state
- no device-local cloud credentials yet
- only the welcome bundle and its `remote_descriptor`

The authorizing device, by contrast, is a normal live installation with a Hub,
local credentials, and a NoteToSelf repo it can already push and read.

## Branch Goal

Refactor the architecture so that the **Hub** can perform NoteToSelf bootstrap
cloud transport before normal participant/session initialization exists, and
prove that path with a cloud-shaped local test harness.

More concretely, after this branch:

- the joining device's **Manager** still does not talk to the internet directly
- the joining device can ask its local **Hub** to fetch NoteToSelf bootstrap
  data using only the welcome bundle's remote descriptor
- the authorizing device can push the relevant NoteToSelf state through normal
  Hub-owned cloud transport
- the flow is proven with local MinIO/S3-style tests rather than
  `LocalFolderRemote`

## What This Branch Is Really About

This branch is **not** mainly about S3.

S3/MinIO is just the safest proof environment because it lets us validate the
transport architecture without also solving OAuth bootstrap.

The real problem is:

> How can Hub perform the cloud read/write work needed for identity bootstrap
> before normal NoteToSelf participant/session state exists on the joining
> device?

That is the load-bearing design question for this branch.

## Key Decisions

### Decision 1: Manager does not read cloud storage directly

This branch should preserve the repo's architectural rule:

- the Hub is the only Small Sea component that talks to the internet
- Manager may orchestrate bootstrap, but it must do so through Hub-owned APIs
  or Hub-owned library surfaces

So this branch should **not** solve bootstrap by teaching Manager to use cloud
adapters directly.

### Decision 2: The branch centers on a bootstrap-capable Hub path

The branch goal is to create a **bootstrap-only transport path** in or for the
Hub that:

- does not require a preexisting NoteToSelf session
- does not require NoteToSelf DB reads on the joining device
- can consume the welcome bundle's remote descriptor directly
- is clearly separated from normal post-bootstrap session flows

This is the main architectural outcome we want from the branch.

### Decision 3: S3/MinIO is the required proof

This branch should prove the new transport path with MinIO / S3-shaped local
tests.

That is a scope limiter, but more importantly it is a **validation strategy**:

- MinIO gives us a real cloud-shaped path
- the tests stay local and deterministic
- we avoid mixing the Hub refactor with OAuth bootstrap policy

### Decision 4: OAuth bootstrap is explicitly deferred

Dropbox / GDrive / other credential-bearing bootstrap flows are out of scope
for this branch.

This branch should leave them as a follow-up, not pretend they are almost
solved.

## Non-Goals

- no Dropbox bootstrap
- no GDrive bootstrap
- no new OAuth UX
- no redesign of the identity-join trust model
- no changes to the signed welcome-bundle verification logic beyond whatever
  small descriptor additions are required
- no general “make bootstrap work for every cloud backend” claim

## Result We Want

At the end of this branch, a critic should be able to say:

- yes, Hub can now handle NoteToSelf bootstrap transport before normal
  identity/session state exists
- yes, Manager still obeys the “Hub as gateway” rule
- yes, the result works in a real cloud-shaped environment, not just shared
  local filesystem tests
- yes, the branch stays honest about what remains unsolved for OAuth providers

## Architectural Invariants

These should stay true throughout the branch:

- joining-device bootstrap transport must not depend on `GET /session/info`
- joining-device bootstrap transport must not require an already-initialized
  NoteToSelf DB/session
- Manager must not absorb duplicate cloud-adapter logic
- the bootstrap transport path must be clearly narrower than ordinary Hub
  session behavior
- device-local secrets still stay local; the welcome bundle should not become a
  general credential dump

## Required Descriptor Change

The welcome bundle's `remote_descriptor` must carry enough information for the
Hub bootstrap path to locate the NoteToSelf CodSync remote.

For this branch that at minimum means:

- `protocol`
- `url`
- `bucket`

The current descriptor shape is too thin because the joining device cannot
derive the NoteToSelf bucket name before it has fetched NoteToSelf itself.

So this branch should explicitly include `bucket` in the bootstrap descriptor.

## Scope Shape

### In scope

- define the branch around a bootstrap-capable Hub transport path
- add whatever bootstrap-specific Hub API/library surface is needed so Manager
  can request NoteToSelf fetch without a normal NoteToSelf session
- include `bucket` in the welcome bundle's `remote_descriptor`
- make the authorizing-side NoteToSelf push use real Hub/cloud transport for
  the MinIO proof path
- add MinIO/S3 micro tests for the joining-device bootstrap flow
- update docs/specs to state the new boundary clearly

### Out of scope

- designing the OAuth bootstrap credential story
- solving provider auth for a brand-new device
- broad Hub session redesign
- background NoteToSelf refresh/discovery UX after bootstrap
- unifying every bootstrap transport under one final abstraction if that adds
  unnecessary churn

## Recommended Plan Shape

This branch plan should be judged against these questions:

1. Does it make Hub, not Manager, the owner of bootstrap cloud transport?
2. Does it avoid requiring fake/throwaway NoteToSelf identity state on the
   joining device?
3. Does it prove the architecture with MinIO/S3 rather than just another local
   filesystem shortcut?
4. Does it stay honest that OAuth bootstrap remains unsolved?

If the answer to any of those is “no”, the branch has drifted.

## Validation

The branch should not be considered complete unless it proves all of these:

- the welcome bundle carries a `remote_descriptor` with `bucket`
- the authorizing device can publish the relevant NoteToSelf state through a
  real cloud-shaped path
- the joining device can fetch NoteToSelf through Hub-owned bootstrap
  transport without a preexisting NoteToSelf session
- the joining device does not need a fake fully initialized NoteToSelf DB
  before the fetch
- the bootstrap flow works against local MinIO/S3 tests
- unsupported bootstrap providers fail clearly rather than silently falling
  back to local-only assumptions

## Questions To Defer Until The Next Planning Step

These are real design questions, but they belong in the **implementation/refactor
planning** step, not this branch-shape step:

- what exact Hub API or library surface should expose bootstrap transport?
- how much of the current cloud-adapter logic should be extracted vs wrapped?
- should the bootstrap path be HTTP-only, in-process-only, or support both?
- should the existing `LocalFolderRemote` bootstrap path be routed through the
  same new abstraction later, or left alone for now?
- what is the cleanest authorizing-side push path through Hub for the MinIO
  proof flow?

Those are important, but they are downstream of the branch goal. This draft is
intentionally stopping short of solving them.
