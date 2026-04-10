# Join Existing Identity Bootstrap

Branch plan for `joining-device-bootstrap`.
Primary tracker: #58.

Related trackers:

- #48 — multi-device NoteToSelf sync and team discovery
- #61 — NoteToSelf shared/device-local split (landed in PR #62)
- #59 — multi-device sender-key / peer-routing runtime
- #57 — persist trusted device sets with admission pipeline

## Context

When Small Sea is installed on a fresh device, there are two flows:

1. **Start a new identity** — most prior work
2. **Join an existing identity** — this branch

The identity model that emerged during #61 planning:

- **Identity join** gets a device into NoteToSelf. After that, the device
  knows about the identity's teams, devices, apps, and cloud remotes.
- **Team join** is separate. Any subset of devices can participate in any
  subset of teams. Joining an identity does NOT auto-join teams.
- NoteToSelf shared state is now safe to sync (secrets are device-local since
  #61). The `small-sea-note-to-self` package owns the ATTACH helper,
  schemas, and connection management.

The `device-linking` branch proved the authorizing side: an existing device
can issue a `device_link` cert for an externally supplied public key and sync
it through a team repo. But it tested that with keys conjured in a single
process — no real second installation.

This branch makes the joining side real.

## Branch Goal

A fresh installation goes from "blank Small Sea install" to "live device in
an existing identity" through an honest flow:

1. joining device generates a NoteToSelf device keypair; only the public key
   leaves
2. public key reaches an existing device through an out-of-band channel
3. existing device admits the new device into NoteToSelf shared state and
   prepares a welcome bundle
4. joining device receives the welcome bundle, initializes minimal local
   state, and pulls NoteToSelf
5. after sync, the new device knows the identity's devices, teams, and apps
6. joining any team is a separate later flow

The branch succeeds when a two-installation test proves that sequence with
separate keystores, separate NoteToSelf repos, and no assumption of automatic
team membership. The minimum required proof is local transport
(`LocalFolderRemote`); a Hub-routed MinIO/S3 proof is desirable if it lands
cleanly without distorting the architecture.

## What This Branch Is Not

- clone an existing installation wholesale
- auto-join every team on identity join
- solve sender-key or peer-routing multi-device runtime (#59)
- solve routine NoteToSelf sync policy (#48)
- solve provider-specific cloud auth on the new device
- a trust-model redesign

## Key Design Questions

### Q1. NoteToSelf trust model for device admission

The biggest open question. Options:

- **(a) Plain shared-state mutation.** The authorizing device adds a row to
  `user_device` in shared NoteToSelf and syncs it. Simple, and NoteToSelf is
  single-user so there's no multi-party trust problem. But no cryptographic
  proof of who admitted whom.
- **(b) Signed identity-level cert.** Like team-level `device_link` certs
  but for NoteToSelf itself. Gives an auditable admission history. More
  machinery.

Default: **(a)** for this branch. NoteToSelf is "my own stuff" — the threat
model is different from multi-member teams. A signed cert can be added later
if the audit trail proves valuable.

### Q2. What is in the welcome bundle?

The minimum the joining device needs to locate and pull NoteToSelf:

- participant UUID (for filesystem layout)
- exact NoteToSelf remote descriptor for CodSync bootstrap
  - for this branch, it is acceptable to assume one cloud account / one
    NoteToSelf remote
  - the descriptor must include whatever berth locator detail CodSync needs
    in practice (for example bucket / prefix / path), not just protocol + URL
- identity label / display name (so the joining device can show the user
  what they're joining)

Explicitly NOT included:

- private keys
- cloud credentials (new device does its own provider auth, or for v1 we
  test with `LocalFolderRemote` which needs no auth)
- team-specific material

### Q3. Does the joining device prove key possession?

Default: **not in v1.** The out-of-band exchange is manual (QR code, email,
etc.) and the authorizing user is physically deciding to admit a key they
received. A signed challenge adds replay protection but the complexity may
not be worth it when the channel is already manual. Defer to a follow-up if
the threat model demands it.

### Q4. What local state exists before NoteToSelf sync?

The minimum viable local installation:

- a local keystore entry for the new NoteToSelf device key
- enough filesystem layout to host the fetched NoteToSelf repo
- a device-local DB created by a bootstrap-safe initializer

Important implementation constraint:

- do NOT call the normal ATTACH helper before the first NoteToSelf pull
- today that helper creates shared `NoteToSelf/Sync/core.db` if it is missing,
  which would accidentally create a fake fresh identity on the joining device
- this branch should introduce a bootstrap initializer that creates only the
  local DB plus filesystem layout, then switches to the normal helper after
  shared NoteToSelf has been fetched

Important: do NOT build a full independent identity locally and then "merge"
it. The joining device is joining, not starting fresh.

### Q5. Cloud auth for the initial NoteToSelf pull

Two separate questions were getting mixed together:

1. how the joining device fetches NoteToSelf the first time
2. how that device later obtains its own long-lived provider auth

Default branch shape:

- **Required proof:** `LocalFolderRemote`, which needs no cloud auth
- **Preferred stretch proof:** MinIO/S3 through the Hub, if it lands cleanly
- **Explicitly deferred:** live internet OAuth / real provider auth UX

Important architectural constraint:

- the Hub remains the only Small Sea component that talks to the internet
- the Manager should not grow duplicate cloud-adapter logic just for bootstrap

Proposed implementation direction:

- add a bootstrap-only Hub path that can fetch NoteToSelf from an explicit
  remote descriptor in the welcome bundle, without requiring a preexisting
  NoteToSelf session
- the local Hub is real infrastructure, not fake throwaway setup; it already
  owns adapter complexity and can keep owning it here
- after bootstrap, real deployments still need a separate per-device cloud
  auth step; credentials are never copied from the welcome bundle

### Q6. Per-team join flow

After identity join, joining a specific team is:

1. device generates a team-specific keypair
2. device records a join request through NoteToSelf (or Manager-owned state)
3. any device already in that team issues a `device_link` cert using the
   existing trust model

This branch should **name this seam** clearly in docs/tests but does NOT
need to implement steps 1–3 unless a tiny stub keeps the architecture honest.

## In Scope

- welcome bundle shape (typed structure, encode/decode helpers)
- authorizing-device helper: receive public key, admit to NoteToSelf, produce
  welcome bundle
- joining-device entry point: generate keypair, receive welcome bundle,
  initialize local state, pull NoteToSelf
- bootstrap-safe local initializer that does NOT create fresh shared
  NoteToSelf state on the joining device
- bootstrap transport plumbing for local-only proof, and possibly Hub-routed
  MinIO/S3 proof if it stays reviewable
- post-bootstrap stable state: new device knows devices/teams/apps from
  NoteToSelf, is NOT auto-joined to any team
- two-installation happy-path test using `LocalFolderRemote`
- doc updates to spec.md and architecture.md for the two-install-path model
- comment on #48 with the seams this branch produces

## Out Of Scope

- automatic team cloning after identity join
- automatic team key generation
- sender-key per-device runtime (#59)
- NoteToSelf routine sync policy (#48)
- provider-specific cloud auth UX
- revocation / removal / lost-device flows
- signed identity-level admission certs (follow-up if needed)
- rich UI / UX polish
- generalized multi-cloud NoteToSelf remote selection

## Concrete Change Areas

### `small-sea-note-to-self` package

- welcome bundle type definition and serialization
- possibly a small `bootstrap.py` module
- bootstrap-safe local initializer distinct from the normal ATTACH helper

### `small-sea-manager` — provisioning.py

- authorizing-side helper: admit new device to shared NoteToSelf
  (`user_device` row), produce welcome bundle
- joining-side entry point: generate keypair, consume welcome bundle, create
  minimal local layout, prepare for NoteToSelf pull

### `small-sea-manager` — manager.py

- session-layer orchestration: joining device pulls NoteToSelf after local
  init (`LocalFolderRemote` required, Hub bootstrap path if added)

### `small-sea-hub`

- if the branch takes the stretch path, add a bootstrap-only NoteToSelf fetch
  endpoint/path that uses the welcome bundle's explicit remote descriptor
  without requiring an already-established NoteToSelf session

### Tests

- new two-installation test under `packages/small-sea-manager/tests/`
- extends the `LocalFolderRemote` pattern from `test_merge_conflict.py`
- installation A: existing identity device
- installation B: brand-new Small Sea install, separate keystore, separate
  NoteToSelf
- asserts B knows teams/devices/apps from NoteToSelf after bootstrap
- asserts B has NOT auto-joined any team

### Docs

- `packages/small-sea-manager/spec.md` — two install paths, identity-first
  bootstrap
- `architecture.md` — identity join vs team join distinction
- explicitly rewrite the stale "Link new device" and "Device Linking Protocol"
  sections in `packages/small-sea-manager/spec.md`, which still describe the
  old token-with-creds / clone-all-teams model
- issue #48 comment naming the seams

## Implementation Order

### Phase 0: Lock Q1–Q6

Confirm or revise the defaults above before writing much code. Quick skim of
provisioning.py and manager.py to check nothing contradicts the plan.

### Phase 1: Welcome bundle shape

Define a small typed structure in `small-sea-note-to-self`. Encode/decode
helpers. No network I/O. Include the exact NoteToSelf remote descriptor, not
just protocol + URL.

### Phase 2: Bootstrap-safe local initialization

Implement the joining-side local initializer that:

- generates a new NoteToSelf device keypair, stores it locally
- creates only the minimum filesystem + local DB state
- does NOT create fresh shared NoteToSelf state before the first pull

### Phase 3: Authorizing-side admission

Implement the helper that:

- takes the new device's public key
- inserts a `user_device` row in shared NoteToSelf
- produces the welcome bundle from existing NoteToSelf state

### Phase 4: Joining-side bootstrap transport

Implement the entry point that:

- consumes the welcome bundle
- uses the bootstrap-safe local initializer
- pulls NoteToSelf via `LocalFolderRemote`
- optionally adds a Hub-routed MinIO/S3 bootstrap path if it stays clean

Important: do not add direct internet/cloud adapter logic to Manager just to
get around bootstrap awkwardness.

### Phase 5: Post-bootstrap stable state

Make the stable state explicit and testable. The new device should:

- know identity-wide devices from `user_device`
- know teams/apps from NoteToSelf shared tables
- NOT have local team clones or team device keys
- be considered "identity-bootstrapped"; real provider auth may still be a
  later step outside this branch

### Phase 6: Two-installation test

Drive the full arc end-to-end with `LocalFolderRemote`.

Stretch validation:

- if feasible, add a MinIO/S3-through-Hub version of the bootstrap flow to
  prove the architecture can support real cloud-shaped bootstrap without
  duplicating adapter logic in Manager

### Phase 7: Docs + issue audit

- update spec.md, architecture.md
- comment on #48 with produced seams
- update #58 to reflect what landed

## Validation

### Micro-level

- joining device's keystore has exactly one new NoteToSelf device key
- welcome bundle round-trips without private material
- welcome bundle includes the exact NoteToSelf remote descriptor needed for
  bootstrap
- `user_device` table on both installations includes both devices after sync
- no team device keys are auto-created on the joining device
- bootstrap initializer does not create a fake fresh shared NoteToSelf DB

### Flow-level

- two-installation test passes with `LocalFolderRemote`
- joining device can read NoteToSelf shared state after bootstrap
- joining device sees teams from NoteToSelf but has no local team clones
- existing device's state is not disrupted by the admission
- if MinIO/S3 bootstrap lands, the joining device can pull NoteToSelf through
  the Hub without Manager owning cloud-adapter logic

## Risks

- **Q1 (NoteToSelf trust model) proves insufficient mid-branch.** Mitigation:
  the plain shared-state mutation is the simplest thing that works; if it
  breaks, the cert machinery from `device-linking` is available to adapt.
- **scope creep into team-join flow.** Mitigation: the "name the seam, don't
  implement it" rule.
- **welcome bundle shape becoming a parallel to invitation flow.** Mitigation:
  reuse invitation shapes where they fit; only add fields with a concrete job.
- **cloud auth becoming a blocker.** Mitigation: `LocalFolderRemote` for
  tests; cloud auth is explicitly out of scope.
- **bootstrap transport distorts the Hub/Manager boundary.** Mitigation:
  prefer a small bootstrap-only Hub path over duplicating cloud adapter logic
  in Manager.
