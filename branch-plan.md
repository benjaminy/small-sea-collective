# Branch Plan: Device Linking First Slice

**Branch:** `device-linking`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/small-sea-manager/spec.md`,
`architecture.md`  
**Related archive plans:** `Archive/branch-plan-identity-model-rethink.md`,
`Archive/branch-plan-admin-control-clarification.md`,
`Archive/branch-plan-device-oriented-identity-first-steps.md`,
`Archive/branch-plan-better-fetch-merge-separation.md`

## Scope Warning

This branch has grown. The original framing was "add `device_link` cert
support + device-aware git signing." Through planning, three additional
structural changes surfaced as unavoidable if the branch is to be honest:

1. the data model currently encodes "member has one device key" as a hard
   assumption; the branch has to remove that lie
2. there is no admission pipeline — sync-arrived certs currently reach
   SQL state by raw file replacement with zero Python validation — and
   the branch has to introduce one before device-aware trust decisions
   mean anything
3. the joining device's intermediate state during a device-link flow is
   currently undesigned

The plan below holds all of this together as one coherent slice. If any
one piece turns out to be larger than expected during implementation,
the branch should be re-split rather than quietly compromised. **We are
at least one more planning iteration from implementation.** See the
Open Questions section for what still needs to be resolved.

## Context

The repo now has the first half of the device-only model in code:

- team creation emits a self-issued `membership` cert
- invitations admit a new member by issuing `membership`
- the old per-team identity-key layer is gone from the live path
- fetch/merge separation is in place (`CodSync.fetch_from_remote` +
  `merge_from_ref`, parked refs under `refs/peers/<member_id_hex>/<branch>`)

What is still missing or structurally wrong:

- `device_link` is documented but is not a supported live cert type
- there is no code path for "same member, second team-device key"
- the team DB encodes a singular `member.device_public_key`
- `team_device_key` (NoteToSelf) is PK'd `(team_id, device_id)`, which
  forbids rotation history for the same device
- **there is no admission pipeline.** `_store_team_certificate` in
  `provisioning.py` is a plain INSERT with no validation, called only
  from local-issue sites (`create_team`, `complete_invitation_acceptance`).
  The team DB is a SQLite file (`core.db`) living inside the team's git
  repo, so sync-arrived certs reach `key_certificate` by raw byte
  replacement during merge — no Python in the loop. Adding `device_link`
  and transitive trust on top of this would just be adding another path
  for unvalidated rows to land in the trust tables.

## Trust Model for This Branch

**Monotonic and validate-on-insert.** When a cert (local or sync-arrived)
is admitted, its signer is checked against the member's current trusted
device set. If the check passes, the cert's subject key joins the set.
After admission, "is D trusted for M?" is a flat set-membership check.

**Trust is transitive across `device_link` edges.** The trusted set for
member M starts with the founding device from M's `membership` cert,
and grows as `device_link` certs are admitted. A device admitted via a
cert signed by a previously-linked (non-founding) device is valid.

This skips the "was D trusted *at the time* it signed?" question. That
question only has meaning once revocation exists. The revocation branch
will need to revisit admission and consult cert history (or a
revocation-aware view of the device set). Deferred, not avoided.

## Proposed Goal

Land the first **honest, reviewable** `device_link` slice:

1. `device_link` becomes a supported live cert type with issue/verify
   helpers
2. the team DB's data model stops pretending members have one device key
3. a working admission pipeline runs on both local-issue and sync-arrival
   paths, and is the sole writer of trusted device state
4. Manager provisioning can record and certify a new device key for an
   existing member through an honest "admit an externally-generated
   public key" flow
5. Git link signing and verification become device-aware, so any trusted
   linked device key can sign for its member
6. the branch's co-device promise is limited to Git link signing —
   sender keys, peer routing, and whole-install bootstrap remain
   member-scoped and unchanged

## Why This Slice

Three concerns that are easy to conflate stay separated:

- **Trust/data model:** one member, multiple linked device keys, with
  an admission pipeline that enforces validity
- **Git/runtime signing:** multiple trusted devices can sign and verify
  pushed git links for the same member
- **Full co-device runtime:** multiple devices acting as peers
  independently, sender-key state per device, etc. — **not this branch**

The first two belong together because device-aware signing has nothing
to verify against without the data model and admission pipeline. The
third spills into sender-key identity and Hub peer/session assumptions
and deserves its own branch.

## Proposed Scope

### 1. Add live `device_link` cert support

Update `wrasse_trust.identity`:

- add `CertType.DEVICE_LINK` to `SUPPORTED_CERT_TYPES`
- add `issue_device_link_cert(...)` and `verify_device_link_cert(...)`
- the cert's subject key is the newly linked device key
- the only required claim is `member_id`
- the authorizing device (D_old) is identified by the envelope signer,
  not by a claim field; verifiers read D_old off the envelope
- no changes to `DEVICE_BINDING` beyond what the previous branch left

### 2. Data model changes

The current model encodes two lies. Both go away in this branch,
together.

#### 2a. `member_device` as a devices table

Add to the team DB:

```sql
CREATE TABLE IF NOT EXISTS member_device (
    member_id         BLOB NOT NULL,
    device_public_key BLOB NOT NULL,
    admitted_by_cert  BLOB NOT NULL,
    added_at          TEXT NOT NULL,
    -- future: nickname, label, form_factor, last_seen_at, ...
    PRIMARY KEY (member_id, device_public_key),
    FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE,
    FOREIGN KEY (admitted_by_cert) REFERENCES key_certificate(cert_id)
);
```

**Framing:** `member_device` is a **table of known devices for a member**,
not a projection of cert history. `key_certificate` is the raw signed
input; `member_device` rows are what the admission pipeline has validated
and accepted. Eventually the table will grow device metadata columns
(nicknames, labels, last-seen) that certs don't carry. For this branch
the schema is minimal, but the table's *job* is "devices with a trust
link," not "certificate mirror."

Invariant: every `member_device` row has an `admitted_by_cert` FK that
resolves to a cert in `key_certificate`, and at the time that row was
written, the admitting cert's signer was in the member's trusted device
set (validate-on-insert).

#### 2b. Drop `member.device_public_key`

- remove the column from the `member` table
- redirect the two existing writers (`create_team` and
  `complete_invitation_acceptance` in `provisioning.py`) to route cert
  inserts through the admission pipeline, which populates `member_device`
- redirect the readers (currently only tests, per grep) to query
  `member_device` via the new `get_trusted_device_keys_for_member` helper

#### 2c. Loosen `team_device_key` PK

Change the NoteToSelf `team_device_key` PK from `(team_id, device_id)`
to `(team_id, device_id, public_key)` so that rotation history for the
same device on the same team is admissible without making timestamp
metadata part of row identity.

`created_at` and `revoked_at` columns stay as-is. `get_current_team_device_key`
already filters `revoked_at IS NULL` and picks the most recent by
`created_at`; no change needed. No rotation code is written in this
branch; the looser PK is deliberate unused capacity so the schema stops
forbidding something the model allows.

#### 2d. Source-of-truth summary

- canonical raw signed history: `key_certificate` (team DB, synced)
- trust-validated device sets: `member_device` (team DB, synced,
  populated **only** by the admission pipeline)
- local private device keys: NoteToSelf's `team_device_key`
- "my current local signing key for team T": existing
  `get_current_team_device_key(...)` — unchanged
- "trusted device public keys for member M": new
  `get_trusted_device_keys_for_member(member_id)` over `member_device`
- historical-lookup helper for NoteToSelf (returning superseded rows):
  **deferred**; no caller needs it in this branch

### 3. Introduce an admission pipeline

This is the most important structural change in the branch.

**Design:**

- define `admit_pending_certs(conn)`: an idempotent pass that reads
  `key_certificate` and writes `member_device`
- for each row in `key_certificate` not yet reflected in `member_device`:
  - if the cert's signer is in the current trusted device set for its
    member, add the subject key to `member_device` with
    `admitted_by_cert = cert.cert_id`
  - if not, leave it alone — it stays in `key_certificate` as inert
    signed bytes with no effect on verification
- **iterate to fixpoint within a single pass**: admitting cert A can
  enable cert B (A links a device that then signed B). Bounded by the
  cert count, cheap in practice
- return a report: admitted, still-pending, rejected-with-reason

**Run points:**

- at the end of every local-issue call (`create_team`,
  `complete_invitation_acceptance`, new `issue_device_link_cert`) —
  the pass trivially admits the one new cert
- after every team-repo merge — see §3b for the hook-point question

**Invariant:** `member_device` is the **sole** source of truth for
verification. `key_certificate` is the raw signed-bytes store.

- no code outside admission writes to `member_device`
- no verification code reads `key_certificate` to make a trust decision
- this is the property that makes rejected/bogus rows in
  `key_certificate` harmless, regardless of how they got there

#### 3a. Admission hook at local-issue sites

Either replace `_store_team_certificate` with a higher-level
`admit_and_store_local_cert(conn, cert, ...)` helper, or wrap its two
existing call sites so that each local insert is followed immediately
by `admit_pending_certs`.

#### 3b. Admission hook at merge sites

Every team-repo merge in the manager must be followed by
`admit_pending_certs` on the (now-updated) team DB connection.

**Open question** (see Open Questions §1): the exact call sites and
whether a single chokepoint exists. Resolving this determines whether
§3b is a one-line hook or a small refactor.

#### 3c. Failure handling

When admission leaves certs rejected after a merge:

- rejections are logged and included in the admission report
- the merge is **not** rolled back — `key_certificate` keeps the
  rejected rows as inert bytes
- verification reads from `member_device`, which never saw the rejected
  rows, so trust decisions are unaffected
- surfacing rejections to users is **out of scope**; for this branch,
  a log line plus a test-observable report is enough

The tradeoff: this is weaker than "abort the whole merge on failure"
(rejected bytes still land in `key_certificate`) but stronger in one
respect: the safety property is independent of git state. Corrupted,
malicious, or otherwise weird rows in `key_certificate` cannot affect
verification regardless of how they got there, because only
`member_device` feeds verification and only admission writes to it.

### 4. Make Git link signatures device-aware

- allow a pushed link to be signed by any currently trusted device key
  for its member
- verification succeeds if the signature matches any key returned by
  `get_trusted_device_keys_for_member(member_id)`
- CodSync's signature payload shape changes from `member_id -> signature`
  to a shape that identifies the signing device by its raw public key
  alongside the member
- no "key id" concept is introduced; raw pubkeys are used directly

Rationale for raw pubkeys in this branch:

- signing public keys are already public in the team trust model
- keeps CodSync verification self-contained
- avoids inventing an indirection layer in the same branch that is
  already changing the signature shape

Narrow branch promise:

- multiple trusted devices may validly sign git links for the same
  member
- only one device still needs to be locally active at a time on any
  given installation
- sender-key and peer-routing behavior remain member-scoped

### 5. Device-link provisioning path

The first implementation slice does **not** solve full new-machine
bootstrap. It proves the admission-pipeline flow end-to-end.

**Authorizing (linking) installation:**

- accepts an externally supplied new device public key for an existing
  member
- issues a `device_link` cert signed by its current active device key
  (via `get_current_team_device_key`)
- routes the cert through the admission pipeline, which validates and
  writes the new `member_device` row

**Joining (new-device) installation:**

- generates a new team-device key locally into its own NoteToSelf's
  `team_device_key` (new row, with the existing `team_id`/`device_id`
  shape)
- the public key leaves the joining device via an out-of-band channel
  (for this branch: a direct hand-off in the test harness)
- once the authorizing installation has issued and synced the
  `device_link` cert, the joining installation's next merge of the team
  repo brings the cert into `key_certificate`, admission runs, and the
  new device appears in `member_device`
- until that merge completes, the joining installation holds a private
  key for a team to which it is not yet fully admitted

**Open question** (see Open Questions §2): the joining side's exact
state transitions still need design.

**Production semantics — do not weaken even for tests:**

- the target device generates its own keypair locally in a device-local
  keystore
- only the public key leaves that device for admission
- `FakeEnclave/` is the current stand-in for a real keystore and must
  be treated with the same discipline: **do not mint foreign keypairs
  on behalf of another installation**, even in test code, except where
  a test explicitly simulates both sides in one workspace
- the production code path is always phrased as "admit this externally
  generated public key," not "mint a key for that device"

### 6. Targeted doc cleanup

- `packages/small-sea-manager/spec.md` currently implies a broader
  NoteToSelf-clone token flow for second-device setup; clarify that
  this branch is per-team `device_link` work, not full bootstrap
- document `member_device` alongside the team DB schema docs
- document the admission pipeline's invariant (sole writer to
  `member_device`; runs after local issue and after merge)

## Explicitly Out of Scope

- cloning `NoteToSelf/Sync` onto a new machine
- sharing cloud credentials via device-link token
- creating a new installation that is immediately live across all teams
- changing Cuttlefish sender-key identity from member-based to
  device-based
- simultaneous encrypted-send behavior from multiple linked devices
  for one member
- peer download/routing that distinguishes sibling devices of one member
- revocation traversal over `device_link`
- device removal / re-key UX
- cross-team batching of device enrollment
- all rotation code (schema admits rotation; no code exercises it)
- a historical-lookup helper for NoteToSelf device keys
- UX for surfacing admission rejections to users
- rollback of merges that contained rejected certs
- stronger consistency guarantees between `key_certificate` and
  `member_device` beyond the validate-on-insert invariant

## Open Questions

These are load-bearing for the plan. None of them are bikeshed items;
each one can push the branch's shape.

### 1. Merge hook points for admission

How many manager call sites perform a team-repo merge today, and is
there a natural chokepoint to hook `admit_pending_certs` into? If
there is one central place (e.g., a manager-level "pull this team
from its peers" helper), §3b is a one-line hook. If the merge logic
is scattered, this branch may need to first introduce a merge-wrapper
helper, which grows scope.

**Research task:** grep for `merge_from_ref` and any immediate-merge
wrappers under `packages/small-sea-manager/`; identify whether a
single hook point exists.

### 2. Joining-device intermediate state

When the new device generates its team-device key locally but its
`device_link` cert has not yet been issued and synced back from the
authorizing installation:

- Does it write the `team_device_key` row to its own NoteToSelf
  immediately? (Probably yes — it is installation-local state, no
  team-DB pollution.)
- Does it clone or touch the team DB at all before admission
  completes? (Probably no, mirroring the invitation flow's "no
  premature member row" rule.)
- What happens on the joining side when the cert finally syncs in —
  is there anything the joining installation has to do, or does
  admission-on-merge handle everything?
- Can the joining installation even run admission if it has no prior
  trust state for the team yet?

This mirrors the invitation-flow intermediate-state question from the
previous branch but is not identical. Needs design before
implementation.

### 3. Test infrastructure for multi-installation sync

The validation plan requires at least one test where installation A
issues a `device_link`, installation B merges the team repo, and
admission runs on B's side. Does that harness exist?

**Research task:** check `packages/small-sea-manager/tests/` and
`tests/test_sync_roundtrip.py` for existing multi-installation
patterns. If none exist, adding one is a named scope item; if one
exists, point at it.

### 4. CodSync signature shape migration cost

Changing the signature payload shape is a breaking change to the
bundle format. Are there checked-in sample bundles, fixture files, or
persistent state anywhere that would need regenerating?

**Research task:** grep for bundle fixtures under `tests/` and
`packages/cod-sync/tests/`.

### 5. `member_device` repair story

The plan accepts that `key_certificate` may contain inert rejected
rows and that `member_device` is populated only incrementally. If a
bug ever causes `member_device` to lose a row that *should* be there
(admitted cert, valid signer, but row is missing), verification
silently fails. Is the answer:

- (a) accept the risk, rely on tests to catch it
- (b) add a test-only "audit" helper that walks `key_certificate` and
  confirms every admissible cert is reflected in `member_device`
- (c) add a production repair helper that re-runs admission
  defensively on schema load or at startup

Leaning (b) as cheap insurance, but this is a real decision.

## Concrete Change Areas

### 1. `wrasse_trust.identity`

- add live `DEVICE_LINK` support
- add issue and verify helpers
- add micro tests: happy-path verification,
  wrong-member/wrong-team rejection, transitive signer case

### 2. Schema changes

Team DB:

- add `member_device`
- drop `member.device_public_key`

NoteToSelf:

- loosen `team_device_key` PK to `(team_id, device_id, public_key)`

### 3. Admission pipeline

- add `admit_pending_certs(conn)` in the manager
- replace or wrap `_store_team_certificate` so local-issue sites
  route through admission
- add admission hooks at merge sites (exact plumbing depends on
  Open Question 1)
- add `get_trusted_device_keys_for_member(member_id)` over
  `member_device`

### 4. `small_sea_manager.provisioning`

- add `issue_device_link_cert(...)` that accepts an externally
  supplied public key
- redirect `create_team` and `complete_invitation_acceptance` to
  route cert inserts through admission
- add the joining-device side of the provisioning flow (shape TBD;
  see Open Question 2)

### 5. `cod_sync` signature shape

- update the link supplement signature shape to identify the signing
  device by raw public key alongside the member
- update verification to accept any trusted device for the member
- update existing tests and any fixture data (scope depends on
  Open Question 4)

### 6. Micro tests

- `packages/wrasse-trust/tests/test_identity.py`:
  - `device_link` happy-path verification
  - wrong-member and wrong-team rejection
- new manager micro test covering:
  - create team
  - link a second device key via the provisioning helper
  - admission populates `member_device` for founding and linked
    devices
  - transitive case: a third device linked via a cert signed by the
    second (non-founding) device is accepted
  - rejection case: a `device_link` signed by an unknown key is left
    in `key_certificate` but does not appear in `member_device`, and
    verification does not honor it
- new or expanded multi-installation test (see Open Question 3):
  - installation A issues and pushes a `device_link`
  - installation B fetches and merges
  - admission runs on B, `member_device` is updated
  - a signed bundle signed by the newly linked device verifies on B
- update existing tests that read `member.device_public_key`
  (`test_signed_bundles.py`, `test_invitation.py`) to use the new
  helpers

## Risks

### 1. Branch scope is already large

The admission pipeline is new plumbing, not a tweak. Introducing it
while also changing the data model, introducing a new cert type, and
changing the git signature shape is a lot for one branch. If Open
Question 1 (merge hook points) reveals that hooking admission into
merges requires widespread refactoring, the branch should be
**re-split rather than narrowed in place**: admission pipeline +
data model in one slice, `device_link` + device-aware signing in a
follow-up.

### 2. Joining-device flow may not fit in this branch

Open Question 2 is structural. If the answer lands somewhere awkward,
the joining-device flow may need to move to a follow-up branch,
shrinking this one to "authorizing-side device link only." That is
acceptable; the authorizing-side flow alone still exercises the
admission pipeline and the data model.

### 3. Git signature shape churn

Changing link signatures from member-only to device-aware is a
cross-cut through CodSync and signed-bundle tests. Manageable, but
should stay deliberately narrow.

### 4. Over-promising multi-device support

If the branch lands `device_link` plus device-aware Git signatures,
readers may infer that all linked devices are fully live. Docs and
tests must be explicit that this is not yet true for sender-key
behavior or peer routing.

### 5. Silent `key_certificate` / `member_device` divergence

The design accepts that rejected certs remain in `key_certificate` as
inert bytes. If a bug causes an *admitted* cert to be lost from
`member_device` while still present in `key_certificate`, verification
silently fails. Mitigation is Open Question 5; at minimum, tests
should assert that after admission, every cert in `key_certificate`
whose signer was trusted is reflected in `member_device`.

## Validation

This branch is successful if:

- the code can issue and verify a real `device_link` cert
- `member.device_public_key` no longer exists; the team DB represents
  a member's trusted device set through `member_device`
- `team_device_key` admits rotation history (loosened PK), even though
  no code in this branch exercises rotation
- the admission pipeline runs at every local-issue site and every
  merge site; no other code writes to `member_device`, and no
  verification code reads `key_certificate` directly
- a transitively-linked device (signed by a non-founding device)
  verifies correctly
- a cert whose signer is unknown is left in `key_certificate`, does
  not appear in `member_device`, and is not honored by verification
- in a multi-installation scenario, a `device_link` issued by
  installation A and merged by installation B results in the new
  device being trusted on B — admission alone carries the weight; no
  Python code in the sync path does validation
- signed-bundle verification succeeds for a member whose valid signer
  is any of several linked device keys
- the updated micro tests pass

Suggested validation command (to be adjusted once new test files
exist):

`uv run pytest packages/wrasse-trust/tests/test_identity.py packages/small-sea-manager/tests/test_create_team.py packages/small-sea-manager/tests/test_invitation.py packages/small-sea-manager/tests/test_signed_bundles.py`

## Locked Scoping Decision

This first `device_link` branch **should** include device-aware Git
signing, device multiplicity in the schema, and an admission pipeline.
It should **not** try to make multiple devices simultaneously live for
sender-key state, peer routing, or whole-install bootstrap.

If during implementation the admission pipeline or the joining-device
flow proves materially harder than expected, the branch should be
re-split rather than narrowed in place.
