# Device Provisioning — Design and Implementation Plan

> [!WARNING]
> This document reflects the older **layered** identity model
> (`team-membership identity key` + wrapped private key material in
> `NoteToSelf` + `device_binding` certs beneath it).
>
> The current design direction in
> [README-brain-storming.md](README-brain-storming.md) is a simpler
> **device-only** model:
>
> - no per-team private key above device keys
> - no wrapped shared private key material synced through `NoteToSelf`
> - `membership` admits a per-team participant UUID and names its founding
>   device key
> - `device_link` expands that UUID's device set within one team
>
> Keep this file as historical design context for now, but do not treat its
> wrapped-key flow as the active plan for new implementation work.

> Referenced from [README.md](README.md). This will become a GitHub issue once
> the design solidifies.
>
> Working status: this document is now past pure brainstorming, but it is still
> not a settled spec. Within the older layered model it was converging on
> "advance device registration first, keep offline-root sophistication for
> later."

## The Problem

Small Sea is fundamentally multi-device. Alice uses her phone at lunch and her
laptop at home. Both need to act as "Alice/Sharks" — signing content, decrypting
messages, participating in sync.

Device provisioning is the process of making a new device a trusted
representative of a participant's team-membership identity. It must answer:

1. How does a new device prove it belongs to Alice?
2. How do other team members verify that Alice's new device is legitimate?
3. How does this work across the per-team identity model (Alice/Sharks,
   Alice/Jets, Alice/NoteToSelf are separate identities)?
4. Where does the private key for `Alice/Sharks` actually live?

## How Other Systems Do This

**Signal:** New device scans QR code from existing device. Existing device
transfers identity key material to new device via encrypted channel. Simple and
effective, but tightly coupled to phone-number identity.

**Matrix:** New device generates its own keys. Existing device cross-signs the
new device's key using the self-signing key (SSK). The SSK is derived from the
master signing key (MSK). Device verification can happen via QR code scan or
emoji comparison.

**Keybase:** New device is "provisioned" by an existing device (or paper key).
The provisioning device adds a signed link to the user's sigchain declaring the
new device. The new device generates its own per-device key (and receives the
per-user key, encrypted to its device key).

## Proposed Flow for Small Sea

### Key Insight: NoteToSelf as Control Plane, Team Repo as Proof Surface

NoteToSelf is every participant's single-user "team." It is the natural home
for device management because:

- It exists before any team membership
- It is private to the participant
- It has its own git repo (and thus its own append-only history)
- Device inventory and wrapped private key material should not be visible to
  teams

But the proof a teammate cares about should be **team-local**:

- the Sharks repo should contain the public certs proving that
  `Alice/Sharks/phone` is a valid Sharks device
- the private key wrappers and local inventory for `Alice/Sharks` should live
  in NoteToSelf

### Near-Term Recommendation

Advance a narrow v1 around these choices:

- Each team-membership identity like `Alice/Sharks` has a **team-membership
  identity key**
- The private key for that identity is stored in `NoteToSelf` as encrypted key
  material, with one device-specific wrapper per authorized device
- Each physical device generates its own **per-team device key** locally, ideally
  in a secure enclave
- Routine signing uses the per-team device key, not the team-membership
  identity key
- The team-membership identity key is used rarely: device binding, revocation,
  and later succession
- Teams are verified from team-local history; no team needs to see NoteToSelf
  material directly

### First Device (Bootstrap)

1. User installs Small Sea on their first device
2. Device generates:
   - A **NoteToSelf identity root** key pair (high protection: passphrase or
     hardware-backed)
   - A **device key** pair (hardware-backed where possible, otherwise local
     storage)
   - A **NoteToSelf signing key** pair (biometric/PIN protected)
3. NoteToSelf identity root signs:
   - `self_binding` cert for the signing key
   - `device_binding` cert for the device key
4. These certs are stored in NoteToSelf's git repo (SmallSeaCollectiveCore DB)
5. The NoteToSelf identity root IS the participant's meta-identity for device
   management purposes

### Creating or Joining a Team

When Alice creates or joins Sharks:

1. The Manager creates or imports the **team-membership identity key** for
   `Alice/Sharks`
2. The private key is stored in `NoteToSelf` as:
   - encrypted key material
   - plus one wrapper for the currently authorized device
3. The current device generates its own per-team **team-device key** for
   `Alice/Sharks/{device}`
4. `Alice/Sharks` issues a `device_binding` cert for that team-device key
5. The public cert goes in the Sharks repo; the wrapped private team identity
   key stays in NoteToSelf

### Adding a Second Device

Provisioning a second device is a two-step process:

1. add the device to NoteToSelf
2. activate the device for each team the participant belongs to

1. New device generates its own **device key** pair locally
2. New device displays its device key fingerprint (QR code or short code)
3. Existing device verifies the fingerprint (scan QR or compare codes)
4. Existing device (using NoteToSelf signing key or identity root) signs a
   `device_binding` cert for the new device's key
5. Cert is committed to NoteToSelf's git repo
6. New device syncs NoteToSelf repo and can now act on behalf of the
   participant

For each existing team such as Sharks:

1. New device generates its own per-team **team-device key** for
   `Alice/Sharks/{new_device}`
2. Existing trusted device verifies the new device's fingerprint or QR code
3. Existing trusted device unwraps the `Alice/Sharks` team-membership identity
   key from NoteToSelf
4. Existing trusted device:
   - adds a new NoteToSelf wrapper for the new device
   - issues a `device_binding` cert for the new per-team device key
5. The wrapper update is committed to NoteToSelf
6. The public `device_binding` cert is committed to the Sharks repo

This keeps team verification local to Sharks while still letting NoteToSelf act
as the private control plane.

### Provisioning a Device into a Team Context

When Alice's new device needs to act as Alice/Sharks, the team should only need
to see:

- Alice's membership in Sharks
- the `Alice/Sharks` identity
- the `device_binding` cert from `Alice/Sharks` to
  `Alice/Sharks/{new_device}`

The team should **not** need to see:

- NoteToSelf identity material
- private key wrappers
- any cross-team information

## Removals and Rotation

Any removal is a big event.

### Removing a Device

If Alice removes her phone:

1. Revoke the affected per-team device keys
2. Delete or disable the corresponding wrappers in NoteToSelf
3. Advance the epoch for each affected team
4. Rotate any content/session keys the removed device could decrypt
5. If the removed device could unwrap a team-membership identity key, decide
   whether re-wrapping is enough or whether full key rotation is needed

### Removing a Teammate

If Sharks removes Bob:

1. Revoke Bob's membership in Sharks
2. Advance the Sharks epoch before further ordinary writes
3. Rotate the content/session material Bob could read
4. Reject ordinary writes from stale epochs once a newer epoch is known

The decentralized model cannot guarantee "no splits under partition." The
protocol should aim to make stale epochs explicit rather than silently merging
everything forever.

## Build Order

The minimum path to a working single-device flow that can grow into
multi-device, multi-team provisioning. Each step builds on the previous one
and is meant to be landable on its own.

**1. New data structures and a typed cert format.** Replace the BURIED /
GUARDED / DAILY model with the purpose-based structures listed below
(`DeviceKey`, `TeamMembershipIdentity`, `WrappedTeamIdentityKey`,
`TeamDeviceKey`). Add a `cert_type` enum to the certificate format from the
start — typing cannot be retrofitted cheaply once certs exist in the wild.
Per-team scoping (a `team_id` or equivalent) belongs on identity keys and
binding certs from day one for the same reason.

**2. `bootstrap_first_device()`.** Generate the NoteToSelf identity root and
the first device key, issue `self_binding` and `device_binding` certs, commit
to the NoteToSelf repo. This is the entry point for every participant —
nothing else works without it, and it's small enough to land in isolation.

**3. `create_team_identity(team)`.** Create the `Alice/{team}` team-membership
identity, store its wrapped private key in NoteToSelf with a device-specific
wrapper, generate the first per-team team-device key, and issue the team-local
`device_binding` cert. This is what connects device registration to team
joining and exercises the NoteToSelf-as-control-plane / team-repo-as-proof
split end to end.

**4. Typed cert verification with issuer constraints.** The trust graph
traversal already exists; extend it to respect cert types and the
issuer-constraint table from the README so nonsensical chains are rejected.
Steps 1–3 produce a working flow; this step makes it safe.

Multi-device provisioning (`provision_new_device()` and
`provision_device_for_team()`) builds naturally on top of these four steps and
does not need to land in the first iteration.

**Note on the wrapped-key envelope.** All current crypto is placeholder, so
the encrypted-blob-plus-per-device-wrapper format in NoteToSelf should also be
a clearly-labeled placeholder envelope for now. The goal of the first
iteration is to exercise the full flow shape — generation, wrapping,
unwrapping, cert issuance, commit — so that the real wrapping primitive can
slot in later without disturbing the surrounding code.

## What Needs to Be Built (MVP)

### Data Structures

- [ ] `DeviceKey` dataclass: device_id, public_key, device_name/label,
  created_at, hardware_backed (bool)
- [ ] `TeamIdentity`: team_id, identity_public_key, created_at, local label
- [ ] `WrappedTeamIdentityKey`: team_id, device_id, wrapped_private_key,
  wrapping_method, created_at, revoked_at
- [ ] `TeamDeviceKey`: team_id, device_id, public_key, created_at,
  hardware_backed (bool), revoked_at
- [ ] `DeviceBindingCert`: either a specialized structure or the generic cert
  with `device_binding` claims
- [ ] `MembershipEpoch`: team_id, epoch_number, authority_hash, activated_at
- [ ] Device and team-identity registry in NoteToSelf's
  SmallSeaCollectiveCore DB

### Operations

- [ ] `bootstrap_first_device()`: generate NoteToSelf identity root + device
  key + signing key, issue self_binding and device_binding certs, commit to
  NoteToSelf repo
- [ ] `create_or_import_team_identity(team)`: create/import `Alice/{team}`,
  store wrapped private key in NoteToSelf, issue first team-device binding cert
- [ ] `provision_new_device()`: generate device key on new device, verify
  fingerprint on existing device, issue device_binding cert, commit to
  NoteToSelf repo
- [ ] `provision_device_for_team(team)`: generate a new per-team team-device
  key, add a wrapper for the team-membership identity key, issue a team-local
  `device_binding` cert, commit to both NoteToSelf and the team repo
- [ ] `list_devices()`: enumerate all provisioned devices from NoteToSelf certs
- [ ] `revoke_device(device_id)`: issue revocation cert for a device key,
  remove or disable wrappers, commit to NoteToSelf repo (and to team repos for
  team-context bindings)
- [ ] `advance_team_epoch(team)`: create a new epoch when removals or other
  major authority changes happen

### Verification

- [ ] `verify_device_binding(device_key, team_membership_identity)`: check that
  a valid device_binding cert chain exists
- [ ] During sync: other team members can verify that a device acting as
  Alice/Sharks has a valid cert chain from Alice/Sharks' team-membership
  identity
- [ ] During sync: reject ordinary writes from stale epochs once a newer epoch
  is known

### UX Flows

- [ ] First-launch setup: generate keys, display recovery information
- [ ] "Add device" flow: QR code or short-code verification between devices
- [ ] Device list management: see devices, revoke devices, see which teams a
  device is authorized for

## Security Considerations

- Device keys should be hardware-backed (Secure Enclave, TPM, Android
  Keystore) where the platform supports it
- The NoteToSelf identity root is the most sensitive key — its compromise
  allows provisioning rogue devices. It should have the strongest available
  protection.
- Team-membership identity keys should not live in plaintext in synced storage.
  They should be stored in NoteToSelf only as encrypted blobs with
  device-specific wrappers.
- Routine signatures should come from per-team device keys so the
  team-membership identity key is touched as rarely as possible.
- Device revocation must propagate to all team contexts. If Alice revokes her
  phone, Alice/Sharks and Alice/Jets both need revocation certs for that
  device, and the affected team epochs likely need to advance.
- The fingerprint verification step during provisioning is critical for
  preventing MITM. This is the same security property as Signal's safety number
  verification or Matrix's device verification.
- A fully compromised authorized device may be able to unwrap a
  team-membership identity key. The protocol needs clear rules for when
  re-wrapping is sufficient and when full identity rotation is required.

## Future Extensions (Not MVP)

- Paper key / recovery key for bootstrapping when no existing device is
  available
- Hardware security key (YubiKey, etc.) as an identity root
- Remote device revocation (revoke a lost device from another device)
- Device attestation certs (proving hardware-backed key storage)
- Automatic device provisioning within trusted network contexts
- Offline roots and stricter recovery/certifying-key custody
