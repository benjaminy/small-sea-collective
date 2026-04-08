# Branch Plan: Typed Cert Format

**Branch:** `typed-cert-format`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/wrasse-trust/device_provisioning_todo.md`,
`architecture.md`  
**Related issues:** #4 "Cuttlefish integration with Hub and Manager",
#6 "Settle identity model for NoteToSelf and multi-device",
#44 "Revisit sender-key storage once multi-device design is clearer",
#48 "Manager - multi-device NoteToSelf sync and team discovery"

## Context

The previous `wrangle-that-wrasse` branch landed the first concrete
team-scoped trust slice:

- `device_binding` certificates now exist in real Manager flows
- `cert_id` is content-addressed from canonical signed bytes
- public proof material lives in the team DB while private team-identity
  material lives in NoteToSelf

That was the right first slice, but the certificate vocabulary is still too
loose:

- `KeyCertificate.cert_type` is still a free-form `str`
- `issue_cert(...)` still defaults to `"generic"`
- ceremony payload extraction still backfills missing `cert_type` with
  `"generic"`
- Manager cert deserialization still backfills missing `cert_type` with
  `"generic"`
- several tests and helper paths still rely on the implicit default instead of
  naming the kind of cert they are creating

The Wrasse Trust docs are explicit that this is the wrong long-term shape:

- `README-brain-storming.md` argues that trust traversal must be typed and that
  a pile of untyped signatures is not meaningful
- `device_provisioning_todo.md` says the cert format needs a `cert_type` enum
  from the start because retrofitting typing after certs exist is expensive

Downstream work is already blocked on this ambiguity:

- #4 needs a settled vocabulary before Hub/Manager/Cuttlefish can decide what
  trust state lives where
- #44 is waiting on clearer device-local versus cross-device identity meaning
- #48 depends on a stable understanding of what a second device is certifying
- #6 is the broader identity-model umbrella that this branch intentionally does
  not try to solve all at once

There is one important implementation constraint that the plan needs to respect:
the current generic verifier API only receives `issuer_public_key: bytes`.
That is enough for signature checking, but it is **not** enough information to
enforce the full README issuer table generically. This branch therefore focuses
on the part that can and should land now:

1. freeze the on-wire cert vocabulary
2. remove permissive `"generic"` fallbacks
3. make current issuance paths explicit about which cert type they emit
4. add only the typed semantic checks that current call sites actually have
   enough context to enforce

Full typed trust traversal and full issuer-role enforcement remain for the
later key-taxonomy work, when the codebase actually has stable public metadata
for "what kind of key is this?"

## Goal

After this branch lands:

1. every cert emitted by current code paths carries an explicit type from one
   canonical `CertType` vocabulary
2. that vocabulary reserves every certificate family named in the Wrasse Trust
   brainstorming doc, so later branches do not invent new on-wire strings after
   data is already flowing
3. missing or unknown cert types fail closed at JSON/DB parsing and
   verification boundaries; `"generic"` disappears
4. the current codebase only emits the cert types it can honestly support now:
   `SELF_BINDING`, `DEVICE_BINDING`, and `CROSS_CERTIFICATION`
5. existing `device_binding` certs remain byte-for-byte stable on the wire
   because the frozen enum value stays `"device_binding"`
6. no DB schema changes, Hub API changes, network-behavior changes, or
   Cuttlefish behavior changes are required

## Outcome

_To be filled in when the branch lands._

## Concrete Scope

### In scope

- a canonical `CertType` definition for all certificate families named in
  `README-brain-storming.md`
- removal of all implicit `"generic"` defaults and permissive fallback parsing
- explicit `CertType` use in current cert issuance paths:
  `issue_device_binding_cert`, `build_hierarchy_certs`, `complete_ceremony`,
  and the direct `issue_cert(...)` call sites in tests
- strict cert parsing at the ceremony and Manager serialization boundaries
- typed verification only where the current call site has enough context to do
  it honestly, especially `verify_device_binding_cert(...)`
- micro tests for string stability, strict parsing, and unchanged
  `device_binding` wire bytes

### Out of scope

- the larger BURIED/GUARDED/DAILY to purpose-based key-taxonomy refactor
- introducing `DeviceKey`, `TeamMembershipIdentity`, `TeamDeviceKey`, or
  wrapped-key envelopes beyond what already exists
- full typed trust-path traversal in `wrasse_trust.trust`
- pretending that `verify_cert(cert, issuer_public_key)` can enforce the full
  README issuer table without more issuer metadata
- changing DB schemas in Manager or NoteToSelf
- changing Hub responsibilities, internet behavior, or Manager DB exclusivity
- converting `RevocationCertificate` into an ordinary `KeyCertificate`
- issuing new real-world cert families like `membership`, `succession`,
  `identity_link`, `attestation`, `ambient_proximity`, or `revocation`

## Changes

### 1. Introduce `CertType` as the single source of truth

Add a small `CertType` definition in `wrasse_trust.identity` using `StrEnum`
so the enum values are also plain strings at JSON/DB boundaries.

Members should cover every family named in the brainstorming README:

- `SELF_BINDING = "self_binding"`
- `DEVICE_BINDING = "device_binding"`
- `CROSS_CERTIFICATION = "cross_certification"`
- `MEMBERSHIP = "membership"`
- `SUCCESSION = "succession"`
- `IDENTITY_LINK = "identity_link"`
- `ATTESTATION = "attestation"`
- `AMBIENT_PROXIMITY = "ambient_proximity"`
- `REVOCATION = "revocation"`

The `.value` strings are the wire format. Editing one later is an on-wire
breaking change, so the tests should treat those strings as frozen.

Important clarification: `CertType.REVOCATION` is reserved vocabulary only in
this branch. The existing dedicated `RevocationCertificate` structure remains
separate for now.

### 2. Remove implicit `"generic"` certs

The current default of `"generic"` is exactly the ambiguity this branch is
meant to eliminate.

Update `issue_cert(...)` so:

- `cert_type` is required
- callers pass `CertType`, not bare ad hoc strings
- there is no default fallback

That forces every call site to declare intent.

Current call-site mapping:

- `issue_device_binding_cert(...)` -> `CertType.DEVICE_BINDING`
- `complete_ceremony(...)` -> `CertType.CROSS_CERTIFICATION`
- `build_hierarchy_certs(...)` -> `CertType.SELF_BINDING`

That last mapping needs one explicit caveat: the current BURIED/GUARDED/DAILY
hierarchy is still legacy placeholder structure. Using `SELF_BINDING` there is
acceptable for this branch as a **provisional legacy mapping**, but this branch
must not claim that it has settled the long-term issuer semantics of the old
hierarchy.

### 3. Make parsing fail closed at serialization boundaries

The current permissive behavior in ceremony payloads and Manager acceptance/team
DB deserialization is a real integrity hole. It lets untyped certs limp through
the system by silently becoming `"generic"`.

Tighten those boundaries:

- `wrasse_trust.ceremony.generate_ceremony_payload(...)` serializes
  `cert.cert_type.value`
- `wrasse_trust.ceremony.extract_hierarchy_certs(...)` requires a `cert_type`
  field and parses it strictly into `CertType`
- `small_sea_manager.provisioning._serialize_cert(...)` writes the enum's
  string value
- `small_sea_manager.provisioning._deserialize_cert(...)` requires a
  recognized `cert_type` and fails closed on missing or unknown values

This branch is pre-alpha and should not add compatibility shims for missing
types. Old or hand-rolled payloads without a valid `cert_type` should fail.

### 4. Separate low-level cryptographic validity from typed semantics

This is the most important correction to the original draft.

`verify_cert(cert, issuer_public_key)` can and should remain the low-level
check that:

- reconstructs canonical bytes
- confirms `cert_id`
- rejects unknown cert types
- verifies the signature

But it does **not** have enough information to decide whether the issuer was an
"identity root", "team-membership identity", "device key", or "admin chain".
That higher-level meaning depends on context the current API does not have.

So this branch should enforce typed semantics only where the caller has enough
context:

- `verify_device_binding_cert(...)` continues to be the meaningful verifier for
  `DEVICE_BINDING`, because it already receives `team_id`, `member_id`, and the
  expected subject key
- ceremony helpers should verify explicit `CROSS_CERTIFICATION` use rather than
  relying on an unlabeled cert
- unsupported reserved types should not gain loose "accept anything" behavior;
  they remain reserved names, not newly enabled trust paths

This keeps the branch honest: stricter and safer now, without pretending to
finish the whole trust-policy problem prematurely.

### 5. Keep storage and API shape stable

No schema changes are needed.

Manager's `key_certificate.cert_type` column is already `TEXT`, which is fine:
it should continue storing the frozen string values. The branch should only make
the code around that column stricter and more explicit.

Similarly, this branch should not alter:

- Hub API contracts
- who may read or write team/NoteToSelf databases
- Cuttlefish storage or protocol behavior
- the team-local/public versus NoteToSelf/private split established by the
  previous branch

### 6. Sweep tests and helper code to use explicit cert types

The blast radius is still small, but it is broader than `wrasse_trust.identity`
alone.

The mechanical sweep includes:

- `packages/wrasse-trust/tests/test_identity.py`
- `wrasse_trust.ceremony`
- Manager provisioning serialization helpers
- Manager tests that inspect serialized cert payloads

Direct `issue_cert(...)` calls in tests should stop depending on the missing
default and instead name the real cert family they mean:

- hierarchy-style tests -> `SELF_BINDING`
- ceremony / teammate-vouching tests -> `CROSS_CERTIFICATION`

The goal is not just type annotations. The goal is to make every test describe
what kind of trust statement it is modeling.

## Validation and Micro Tests

### New micro tests

Add focused micro tests that specifically guard the failure modes this branch is
meant to close:

- **enum string stability:** assert every `CertType` member has the exact
  expected wire string
- **no implicit generic default:** direct `issue_cert(...)` without a
  `cert_type` should no longer be possible
- **ceremony strict parsing:** a ceremony payload missing `cert_type`, or using
  an unknown string, fails extraction/verification instead of silently turning
  into `"generic"`
- **Manager strict parsing:** `_deserialize_cert(...)` rejects missing or
  unknown `cert_type`
- **device_binding wire stability:** an emitted `device_binding` cert still
  serializes `"device_binding"` and produces the same canonical bytes / `cert_id`
  shape as before
- **ceremony type explicitness:** `complete_ceremony(...)` emits
  `CROSS_CERTIFICATION`, not an unlabeled generic cert

### Regression suite to keep passing

- `packages/wrasse-trust/tests/test_identity.py`
- `packages/small-sea-manager/tests/test_create_team.py`
- `packages/small-sea-manager/tests/test_invitation.py`
- `packages/small-sea-manager/tests/test_signed_bundles.py`
- `packages/small-sea-manager/tests/test_hub_invitation_flow.py`

### Success criteria

A bright critic should be convinced that this branch:

1. freezes the cert vocabulary before more real data accumulates
2. removes the silent `"generic"` escape hatch from live code paths
3. keeps existing `device_binding` wire bytes stable
4. makes ceremony and Manager cert parsing fail closed
5. reserves future cert names now without accidentally enabling half-designed
   trust flows
6. does not smuggle in schema work, Hub work, or key-taxonomy work under the
   label of a format cleanup

## Risks To Watch

- over-promising issuer-constraint enforcement in places where the current API
  does not have enough issuer metadata
- freezing misleading semantics from the legacy BURIED/GUARDED/DAILY hierarchy;
  the `SELF_BINDING` use there must stay clearly marked as provisional
- accidentally leaving one `"generic"` fallback behind in ceremony, Manager, or
  tests
- accidentally changing an enum string value and breaking existing stored certs
- conflating reserved `CertType.REVOCATION` vocabulary with the still-separate
  `RevocationCertificate` implementation

## Migration / Compatibility

- Pre-alpha: backward compatibility for arbitrary free-form `cert_type` strings
  is **not** a goal
- existing stored `device_binding` rows should remain valid because their
  string value stays `"device_binding"`
- ceremony payloads or Manager payloads missing `cert_type` should fail after
  this branch by design
- DB schema remains unchanged; only parsing and construction become stricter

## Order of Operations

1. add `CertType` and remove the `"generic"` default from `issue_cert(...)`
2. update `identity.py` and `ceremony.py` to use explicit cert types
3. update Manager cert serialization/deserialization to parse strictly
4. sweep tests and helper call sites to name explicit cert families
5. add the focused micro tests for strict parsing and string stability
6. run the regression suite and fix any fallout
7. fill in the Outcome section
