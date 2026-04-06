# Device Provisioning — Design and Implementation Plan

> Referenced from [README.md](README.md). This will become a GitHub issue once
> the design solidifies.

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

### Key Insight: NoteToSelf as Device Manager

NoteToSelf is every participant's single-user "team." It is the natural home
for device management because:

- It exists before any team membership
- It is private to the participant
- It has its own git repo (and thus its own append-only history)
- Device operations should not be visible to teams

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

### Adding a Second Device

1. New device generates its own **device key** pair locally
2. New device displays its device key fingerprint (QR code or short code)
3. Existing device verifies the fingerprint (scan QR or compare codes)
4. Existing device (using NoteToSelf signing key or identity root) signs a
   `device_binding` cert for the new device's key
5. Cert is committed to NoteToSelf's git repo
6. New device syncs NoteToSelf repo and can now act on behalf of the
   participant

**Key transfer question:** Does the new device need the NoteToSelf signing
key? Options:

- **Option A: Per-device signing keys.** Each device has its own signing key,
  signed by the identity root. No key transfer needed. Signatures are
  attributable to specific devices. More key material to manage.
- **Option B: Shared signing key.** The signing key is transferred (encrypted
  to the new device's device key) during provisioning. Simpler mental model.
  Key transfer is a security-sensitive operation.

**Recommendation for v1:** Option A (per-device signing keys). It avoids the
security complexity of key transfer and naturally supports device-level
attribution. The identity root signs each device's signing key via
`self_binding` cert.

### Provisioning a Device into a Team Context

When Alice's new device needs to act as Alice/Sharks:

1. Alice already has a Sharks identity root (from when she joined the team)
2. The Sharks identity root is stored in NoteToSelf (encrypted), accessible
   from any provisioned device
3. New device either:
   - **Option A:** Receives a team-specific signing key (transferred from
     existing device, encrypted to new device key)
   - **Option B:** Gets a device-scoped team signing key, signed by the Sharks
     identity root
4. The team-level device binding cert is committed to the team's git repo so
   other members can verify

**Open question:** Does the team need to know about Alice's NoteToSelf at all?
Ideally not — the team only sees "Alice/Sharks added device X" via a cert
signed by Alice/Sharks' identity root. The NoteToSelf machinery is invisible
to the team.

## What Needs to Be Built (MVP)

### Data Structures

- [ ] `DeviceKey` dataclass: device_id, public_key, device_name/label,
  created_at, hardware_backed (bool)
- [ ] `DeviceBindingCert`: extends the cert model with device-specific fields
  (or uses the generic cert with `device_binding` type in claims)
- [ ] Device registry in NoteToSelf's SmallSeaCollectiveCore DB

### Operations

- [ ] `bootstrap_first_device()`: generate NoteToSelf identity root + device
  key + signing key, issue self_binding and device_binding certs, commit to
  NoteToSelf repo
- [ ] `provision_new_device()`: generate device key on new device, verify
  fingerprint on existing device, issue device_binding cert, commit to
  NoteToSelf repo
- [ ] `provision_device_for_team(team)`: issue a team-context device binding
  cert so the device can act as Alice/{team}, commit to team repo
- [ ] `list_devices()`: enumerate all provisioned devices from NoteToSelf certs
- [ ] `revoke_device(device_id)`: issue revocation cert for a device key,
  commit to NoteToSelf repo (and to team repos for team-context bindings)

### Verification

- [ ] `verify_device_binding(device_key, identity_root)`: check that a valid
  device_binding cert chain exists
- [ ] During sync: other team members can verify that a device acting as
  Alice/Sharks has a valid cert chain from Alice/Sharks' identity root

### UX Flows

- [ ] First-launch setup: generate keys, display recovery information
- [ ] "Add device" flow: QR code or short-code verification between devices
- [ ] Device list management: see devices, revoke devices

## Security Considerations

- Device keys should be hardware-backed (Secure Enclave, TPM, Android
  Keystore) where the platform supports it
- The NoteToSelf identity root is the most sensitive key — its compromise
  allows provisioning rogue devices. It should have the strongest available
  protection.
- Device revocation must propagate to all team contexts. If Alice revokes her
  phone, Alice/Sharks and Alice/Jets both need revocation certs for that
  device.
- The fingerprint verification step during provisioning is critical for
  preventing MITM. This is the same security property as Signal's safety number
  verification or Matrix's device verification.

## Future Extensions (Not MVP)

- Paper key / recovery key for bootstrapping when no existing device is
  available
- Hardware security key (YubiKey, etc.) as an identity root
- Remote device revocation (revoke a lost device from another device)
- Device attestation certs (proving hardware-backed key storage)
- Automatic device provisioning within trusted network contexts
