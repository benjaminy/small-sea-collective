# Join Existing Identity Bootstrap

Branch plan for `joining-device-bootstrap`.
Primary tracker: #58.

Related trackers:

- #48 — multi-device NoteToSelf sync and team discovery
- #61 — NoteToSelf shared/device-local split (landed in PR #62)
- #59 — multi-device sender-key / peer-routing runtime
- #57 — persist trusted device sets with admission pipeline

## Context

When Small Sea is installed on a fresh device, there are two distinct flows:

1. **Start a new identity**
2. **Join an existing identity**

The repo is now much clearer about the difference:

- **Identity join** gets a device into NoteToSelf.
- **Team join** is separate and happens later, per team.
- Any subset of a person's devices may participate in any subset of their
  teams. Identity membership is not the same thing as team participation.

The #61 branch made shared NoteToSelf safe to sync later by splitting secrets
and runtime state into a device-local DB. That means this branch no longer has
to fight the old "copying one device's live secrets to another device" problem.

The main remaining challenge is bootstrap shape:

- the joining device should stay almost blank at first
- the welcome bundle should be rich enough that the device can initialize
  itself correctly once it has the bundle
- the bundle should carry bootstrap metadata, not copied secret material or a
  second source of truth for NoteToSelf state

## Branch Goal

Make "join an existing identity" real as a two-installation flow:

1. the joining device generates a NoteToSelf device keypair and device UUID
2. only the public key + device UUID leave the device
3. an existing device admits that device into shared NoteToSelf state
4. the existing device prepares a **rich welcome bundle**
5. the joining device uses that bundle to initialize only the minimal local
   shape it actually needs
6. the joining device pulls NoteToSelf
7. after sync, the joining device knows the identity's devices, teams, apps,
   and NoteToSelf shared metadata
8. joining any team remains a separate later flow

The branch succeeds when a two-installation test proves that sequence with:

- separate keystores
- separate local NoteToSelf directories
- no assumption of automatic team membership
- no copied cloud credentials or private keys in the welcome bundle

**Required proof:** `LocalFolderRemote`

**Nice stretch proof:** MinIO/S3-shaped bootstrap without violating the
Hub-as-gateway rule

## Core Design Stance

The joining device should build as little as possible before it has enough
authoritative information to initialize correctly.

That means:

- do not create a fake fresh shared NoteToSelf DB and later overwrite it
- do not clone every team as part of identity bootstrap
- do not copy device-local secrets from another installation
- do use a richer welcome bundle so the joiner can initialize its local shape
  honestly once it knows what identity it is joining

## What This Branch Is Not

- clone an existing installation wholesale
- auto-join every team on identity join
- solve sender-key or peer-routing multi-device runtime (#59)
- solve routine NoteToSelf sync policy (#48)
- solve generalized multi-cloud NoteToSelf remote selection
- solve provider-specific cloud auth UX for real internet deployments
- a trust-model redesign

## Preconditions

This branch should assume:

- the existing identity already has a working NoteToSelf remote
- that remote has a pushed NoteToSelf repo the joiner can bootstrap from
- for now, NoteToSelf uses exactly one configured remote

If those assumptions are false in real life, that is a separate product/UX
problem. This branch is about honest bootstrap once an identity already has a
place for NoteToSelf to live.

## Load-Bearing Decisions

### 1. NoteToSelf admission model

Default for this branch:

- the authorizing device adds a row to shared `user_device`
- no identity-level signed admission cert yet

Reason:

- NoteToSelf is single-user social territory, not multi-member governance
- this is the smallest honest thing that works

Future seam to preserve:

- periodic cross-device sanity checks / challenge-response should be easy to
  add later
- the bootstrap flow should not be shaped in a way that blocks later
  signed/auditable admission if it becomes valuable

### 2. The welcome bundle should be rich

The welcome bundle should carry enough information that the joining device does
not need to guess or build fake local identity state.

It should be rich in:

- bootstrap metadata
- remote locator details
- identity labeling / UX details
- lightweight verification metadata

It should be thin in:

- authority
- secrets
- copied shared-state snapshots

The pulled NoteToSelf repo remains the real source of truth.

### 3. The joiner stays minimal until it has the bundle

There are really two sub-states here.

Before the welcome bundle exists, the joining device should have only:

- its new NoteToSelf device keypair
- its new device UUID
- a public join request artifact (device UUID + public key, serialized as a
  tiny versioned JSON payload for v1) derived from those

After the welcome bundle arrives, but before the first NoteToSelf pull, the
joining device should have only:

- enough filesystem layout to receive NoteToSelf
- a device-local DB with local secret refs

It should not have:

- a fresh shared `NoteToSelf/Sync/core.db`
- pre-created teams
- pre-created team device keys
- copied cloud credentials

### 4. Required transport proof vs stretch transport proof

Required branch proof:

- `LocalFolderRemote`

Stretch proof, only if it stays clean:

- MinIO/S3-shaped bootstrap through Hub-owned transport

This keeps the branch grounded:

- we must prove the identity bootstrap model works
- we do not have to solve all real-cloud auth UX in the same branch

### 5. Hub remains the only internet-facing component

Manager must not grow its own cloud-adapter zoo just because bootstrap is
awkward.

If a real remote bootstrap path is added in this branch, the preferred shape
is:

- a narrow bootstrap-only Hub path
- driven by an explicit remote descriptor from the welcome bundle
- not requiring a preexisting NoteToSelf session

## Rich Welcome Bundle

The welcome bundle should at least include:

- bundle format/version
- participant UUID
- joining device UUID
- joining device public key echoed back (for cross-check on receipt)
- identity label / nickname for UI clarity
- exact NoteToSelf remote descriptor
- `issued_at` timestamp (for staleness detection)
- `authorizing_device_label` (UX — tells the user which device admitted them)

For this branch, the remote descriptor can start from the existing invitation /
`ExplicitProxyRemote` convention, but the plan should not overfit to
bucket-shaped backends forever.

The welcome bundle should be **encrypted to the joining device's public key**
using an authenticated-encryption construction.

Best-practice requirements for this branch:

- confidentiality: only the intended joining device can read the payload
- integrity: tampering must be detected before the payload is used
- recipient binding: decryption with the wrong device key must fail
- protocol binding: include a bundle-purpose / bundle-version string as
  authenticated context so this payload cannot be confused with some other
  encrypted object
- identity binding: include `participant_uuid` and `joining_device_uuid` in
  the authenticated payload or associated data
- staleness detection: keep `issued_at`, and add `expires_at` if that stays
  simple enough for the branch

Nice-to-have future seam, but not required to ship this branch:

- explicit authorizing-device authenticity beyond AEAD integrity
  - for example a detached signature or a later cross-check after the first
    NoteToSelf pull

The authorizing device already has that key from OOB leg 1, so this is natural.
It means the bundle is safe to pass over an insecure OOB channel (email, photo,
etc.) — only the intended device can read the remote locator details inside.

The welcome bundle must not include:

- private keys
- cloud credentials / refresh tokens / bearer tokens
- team-specific secrets
- copies of NoteToSelf tables as a shadow source of truth

## Joining Device Local State

The joining device needs a bootstrap-safe initializer distinct from the normal
ATTACH helper.

Why:

- the normal ATTACH helper currently creates shared `NoteToSelf/Sync/core.db`
  if it is missing
- that would accidentally create a fake fresh identity on the joining device

So this branch should add a bootstrap initializer that does only:

- create `Participants/{participant_hex}/NoteToSelf/Local/`
- create the device-local DB
- create enough directory layout for `NoteToSelf/Sync/`
- store the local NoteToSelf device private key ref

and explicitly does **not**:

- initialize shared `core.db`
- populate shared tables

### Local secret storage for the NoteToSelf device key

Recommended decision for this branch:

- add a dedicated local table for NoteToSelf device key refs

Why:

- reusing `team_device_key_secret` with a sentinel team ID would work
- but it smuggles identity-level state into a team-scoped table and makes the
  storage model harder to read later

## Post-Bootstrap Stable State

After a successful identity bootstrap, the joining device should:

- know identity-wide devices from shared `user_device`
- know teams/apps from shared NoteToSelf tables
- have a valid local NoteToSelf device key
- have no local team clones yet
- have no local team device keys yet

This state should be treated as:

- **identity-bootstrapped**
- not necessarily **fully cloud-authenticated for routine real-world sync**

That distinction matters because real provider auth UX is out of scope.

## Per-Team Join Seam

After identity join, joining a specific team is still a separate flow:

1. the device generates a team-specific keypair
2. the device records or requests team participation through NoteToSelf or
   Manager-owned state
3. an already-participating device in that team issues a `device_link` cert

This branch should name that seam clearly in docs/tests, but not implement the
full team-join flow unless a tiny stub is needed to keep the architecture
honest.

## In Scope

- rich welcome bundle type and serialization
- authorizing-side helper:
  - receive joining device UUID + public key
  - admit device into shared `user_device`
  - commit/push NoteToSelf
  - produce welcome bundle
- joining-side entry point:
  - generate device UUID + keypair
  - export the public join request artifact
  - later consume welcome bundle
  - run bootstrap-safe local initialization using the previously generated key
  - pull NoteToSelf
- dedicated local storage for the joining device's NoteToSelf key ref
- two-installation happy-path proof with `LocalFolderRemote`
- explicit docs for the distinction between identity join and team join
- issue audit / comments for the issues materially changed by the branch

## Out Of Scope

- automatic team cloning after identity join
- automatic team key generation
- sender-key per-device runtime redesign (#59)
- NoteToSelf routine sync policy (#48)
- generalized multi-cloud remote selection
- real provider auth UX
- revocation / lost-device / device-removal flow
- identity-level signed admission certs
- broad UI polish

## Concrete Change Areas

### `cuttlefish`

- welcome bundle seal/open API: public-key authenticated encryption using a
  well-known construction (e.g. X25519 + ChaCha20-Poly1305)
- simple interface: `seal_welcome_bundle(recipient_public_key, plaintext)` →
  ciphertext; `open_welcome_bundle(private_key, ciphertext)` → plaintext
- protocol binding and identity binding baked into the construction's
  associated data

### `small-sea-note-to-self`

- welcome bundle type definition
- serialization / parsing helpers (plaintext → JSON → encrypt → base64
  text-safe encoding for OOB transport)
- bootstrap-safe local initializer
- local schema addition for NoteToSelf device secret refs

### `small-sea-manager` — `provisioning.py`

- authorizing-side admission helper
- welcome bundle generation
- joining-side bootstrap entry point
- local initialization orchestration

### `small-sea-manager` — `manager.py`

- high-level bootstrap orchestration
- local-only transport path for required proof
- optional hook point for a future Hub bootstrap path

### `small-sea-hub`

Only if the stretch path stays small and clean:

- bootstrap-only NoteToSelf fetch path
- driven by explicit remote descriptor
- no preexisting NoteToSelf session required

### Tests

- a new two-installation test under `packages/small-sea-manager/tests/`
- installation A = existing identity device
- installation B = truly blank joining device
- separate keystores, separate local DBs, separate NoteToSelf dirs
- assert B learns devices/teams/apps from pulled NoteToSelf
- assert B does not auto-create team participation

### Docs

- `packages/small-sea-manager/spec.md`
  - rewrite the stale "Link new device" section
  - rewrite the stale "Device Linking Protocol" section
- `architecture.md`
  - make identity-join vs team-join distinction explicit
- issue updates
  - #48: note the seams produced by this branch
  - #58: summarize what this branch actually lands

## Implementation Order

### Phase 0: Lock branch assumptions

Before coding much:

- confirm the single-remote NoteToSelf assumption; add a loud assertion if
  multiple `cloud_storage` rows exist
- confirm plain shared-state admission for v1
- confirm dedicated NoteToSelf local secret table instead of sentinel reuse
- confirm Cuttlefish can take a new seal/open primitive without major surgery

### Phase 1: Joining-device request material

Implement the joining-device side first:

- generate the device UUID + keypair
- store the private key in the enclave (FakeEnclave for now) immediately —
  the underlying secret never leaves the enclave
- write only a lightweight reference to that enclave-held key into the
  platform-appropriate app/user-data directory (faked in tests)
- later, during bootstrap-safe initialization, copy or move that reference
  into the proper device-local DB row without regenerating the key
- expose the public join request artifact (device UUID + public key as a
  tiny versioned JSON payload that is easy to copy as text in v1)

### Phase 2: Rich welcome bundle + Cuttlefish seal/open

Implement the bundle type and encryption together.

Bundle type:

- versioned structure
- exact remote locator details
- no secrets

Cuttlefish:

- add `seal_welcome_bundle` / `open_welcome_bundle` using a well-known
  construction
- protocol and identity binding via associated data

Serialization:

- plaintext JSON → encrypt via Cuttlefish → base64 text encoding for
  OOB transport

### Phase 3: Authorizing-side admission

Implement the helper that:

- takes joining device UUID + public key
- inserts the `user_device` row
- commits/pushes NoteToSelf
- returns the welcome bundle

Note:

- a dangling admitted-but-never-finished device is acceptable in v1
- that is messy socially, but not a protocol failure

### Phase 4: Bootstrap-safe local initialization

Implement the joining-side initializer that:

- accepts the previously generated key material plus bundle metadata
- stores the private key locally
- creates only local DB + directory structure
- does not create shared `core.db`

### Phase 5: Joining-side bootstrap flow

Implement the joining-side flow that:

- receives the welcome bundle
- runs bootstrap-safe local initialization
- pulls NoteToSelf via `LocalFolderRemote`

Do not add direct internet/cloud adapter logic to Manager just to get around
bootstrap awkwardness.

### Phase 6: Stable-state proof

Make the stable post-bootstrap state explicit in tests:

- both devices visible in `user_device`
- teams/apps visible on the joining device
- no team clones or team keys created automatically

### Phase 7: Optional real-cloud-shaped proof

Only if it stays reviewable:

- add a MinIO/S3-shaped bootstrap path using test credentials / local harnesses
- prefer Hub-owned transport over Manager adapter duplication
- do not treat this as solving real provider-auth UX

### Phase 8: Docs + issue audit

- update `spec.md`
- update `architecture.md`
- comment on #48
- update #58

## Validation

### Micro-level

- joining device's keystore has exactly one new NoteToSelf device key
- join request artifact round-trips cleanly
- join request artifact is versioned and contains only public data
- welcome bundle round-trips cleanly
- welcome bundle contains no secret material
- welcome bundle contains exact bootstrap locator metadata
- intended joining key decrypts the bundle
- wrong key cannot decrypt the bundle
- tampered bundle is rejected before use
- bootstrap initializer creates no fake shared `core.db`
- no team device keys are auto-created on the joining device

### Flow-level

- two-installation `LocalFolderRemote` bootstrap passes
- after bootstrap, both installations see both `user_device` rows
- the joining device can read NoteToSelf shared state
- the joining device sees teams from NoteToSelf but has no local team clones
- the existing device's state is not disrupted

### Stretch validation

If the optional Hub/S3 path lands:

- the joining device can bootstrap through Hub-owned transport
- Manager still does not own duplicate cloud adapter logic

## Risks

- **Plain shared-state admission proves too weak.**
  Mitigation: keep the branch shape compatible with later signed admission /
  sanity-check additions.

- **The welcome bundle turns into a shadow copy of NoteToSelf.**
  Mitigation: carry only bootstrap metadata and remote locator details, not
  copied shared-state tables.

- **Bootstrap transport distorts the Hub/Manager boundary.**
  Mitigation: keep `LocalFolderRemote` as the required proof and make any Hub
  transport extension narrow and optional.

- **The branch drifts into team-join implementation.**
  Mitigation: keep identity join and team join sharply separated in code,
  tests, and docs.

- **Real provider auth becomes a blocker.**
  Mitigation: explicit non-goal for this branch; local-only proof is enough to
  validate the architecture.

- **Welcome bundle encryption requires real crypto against placeholder infra.**
  Mitigation: add the welcome bundle seal/open primitive to Cuttlefish with a
  simple API (e.g. `seal_welcome_bundle` / `open_welcome_bundle`). Use a
  well-known construction from an established library (e.g. X25519 +
  ChaCha20-Poly1305 via `cryptography` or `nacl`). Do not design novel crypto.
  Accept that the specific construction may change when Cuttlefish matures.

- **Multiple `cloud_storage` rows make NoteToSelf remote ambiguous.**
  Mitigation: assume exactly one `cloud_storage` row for now (matches the
  single-remote precondition). Fail loudly if multiple rows exist so the
  assumption is never silently violated. Solve multi-remote selection later.
