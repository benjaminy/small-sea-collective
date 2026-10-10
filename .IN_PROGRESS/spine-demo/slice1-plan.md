# Slice 1 plan: one conflict, one reconciliation, one fresh verifier

Draft, 2026-10-10, revised after the astra critique: proposal C as trimmed by the critiques and the owner's answers.
`OQ`, `RD`, and `QA` mean `open-questions.md`, `reconciliation-design.md`, and `quorum-approval.md` on `constitution-party`.

## 1. Goal

Alice removes Bob while Bob adds a device, and every device notices the conflict and pauses on its own.
Two members approve a reconciliation; every device checks and follows it, and the pause ends.
A fresh process given only an exported bundle checks it and explains what each branch did and what the team now follows.

Slice 1 should teach us:
- whether the reconciliation record carries enough evidence for a device that did not write it, and for a fresh process, to reach the same verdict;
- whether the reconciliation checks can be pure functions of the base state and the reconciliation, each returning valid, invalid with a named check, or incomplete with the missing IDs;
- whether a device's view can be computed from the objects it holds, so that "accepted" is provisional and arrival order does not matter;
- whether the envelope and ID scheme suit the later cutover into Core.

If the checks or replay turn out contradictory, stop and redesign the record before building on it (crit-Z).

## 2. Module layout and storage

New subpackage `packages/small-sea-manager/small_sea_manager/spine/`, called by no team flow.
It ports probe semantics and imports nothing from the probe or the old constitution modules.

| Module | Responsibility | Size |
|---|---|---|
| `encoding.py` | Canonical bytes, decoding with strict field rules, IDs, signing, proposal digest, bundle encoding | ~150 |
| `policy.py` | The fixed policy: genesis validation, state tables, operations, compatibility check, reconciliation authority. Pure | ~170 |
| `history.py` | Pinned genesis, state at an object, original-context validation | ~120 |
| `reconcile.py` | The reconciliation checks in section 4 | ~170 |
| `view.py` | Validation pass, suppression, heads, classification, pause, acting state | ~130 |
| `proposal.py` | Draft C, sign approvals, assemble R | ~70 |
| `device.py` | SQLite store, `receive(bytes)`, publish guard, lookup through retained copies, `view()`, `export_bundle()` | ~220 |
| `explain.py`, `verify.py` | Plain-terms descriptions; `python -m small_sea_manager.spine.verify --origin ID BUNDLE [--json]` | ~200 |
| `scenario.py`, `demo.py` | Fake delivery controller, story steps, CLI printing each device's view | ~180 |

**Storage: one SQLite file per device, through SQLAlchemy `text()` as `constitution_store.py` does.**
Cutover lands in the Core berth database, so schema and transaction lessons carry over.
Each table is marked by where it would live after cutover:
- `spine_object(object_id PK, kind, encoded)` (synced): every object that passes core checks, valid under policy or not.
- `spine_retained_copy(object_id, container_id)` (local index): which reconciliation carries which event.
- `spine_proposal(digest PK, encoded)` (local): proposal packages drafted or received; not Constitution objects.
- `spine_receipt(seq, object_id, disposition, reason)` (local): what the device concluded at each arrival, for display only.
- `spine_identity` (local): this device's private key and the pinned genesis bytes.

No table stores acceptance, tips, or pause; `view()` recomputes them, and decision logic never reads `spine_receipt`.
Restart micro tests (section 7) make the storage earn its place: closing and reopening a device must give the same view.

## 3. Encoding and object formats

**Canonical bytes**: `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` encoded as ASCII.
Values are objects, arrays, strings, and non-negative integers; no floats, booleans, or nulls.
Byte values are lowercase hex: keys and IDs 64 characters, signatures 128.
The decoder rejects duplicate keys, unknown or missing keys for the kind, wrong types, and any input whose re-encoding differs from the received bytes.
**IDs and signatures** use domain prefixes ending in a zero byte:
- object ID = SHA-256(`"spine-v1 id\0"` + canonical body);
- signature = Ed25519 (RFC 8032, deterministic) over `"spine-v1 sig\0"` + canonical body;
- D = SHA-256(`"spine-v1 proposal\0"` + canonical C).

The ID excludes the signature, as v0 (`wrasse_trust/events.py`) does and the probe does not, so one rule covers signed and unsigned objects.
A store keeps the first envelope for an ID; a later, different envelope with the same ID is refused with reason "envelope conflict".
Copies inside R are checked on their own bytes; a mismatch with a stored standalone is logged as an envelope conflict and does not change R's verdict.
T1 commits literal test vectors: canonical bytes, ID, and signature for a fixed-seed genesis and one event.

- **Genesis** (unsigned): `{format:"spine-v1", kind:"genesis", nonce, policy:{name:"spine-demo-1", settings:{reconcile_quorum:2}}, members:[{member_id, name}], devices:[{device_key, member_id, label}]}`.
  Valid iff member IDs and device keys are unique, every device names a listed member, and policy and settings equal exactly these values.
  Its ID is the team's origin, which each device and the verifier receive out of band.
- **Signed event**: `{body:{format:"spine-v1", origin, kind, author, parents:[one ID], payload}, sig}`.
  Its ancestry must reach the pinned genesis; carrying the right `origin` is not enough.
- **Ordinary**: `{op:"remove_member", member_id}` or `{op:"add_device", member_id, device_key, label}`.
- **Proposal C** (not an event): `{format:"spine-proposal-v1", origin, base, signer, resolved_tips, adopted, rejected}`; `adopted` is in replay order, the other lists sorted.
  The package `{proposal:C, copies}` travels as bytes.
- **Approval**: parent = C.base, `payload {approves:D}`.
- **Reconciliation R**: parent = C.base, author = C.signer, `payload {proposal:C, copies, approvals}`, each list of encoded objects sorted by ID.
- **No object may name an approval as its parent.**
  Otherwise a signed event could advance governance through a support event.

**Bundle**: `{format:"spine-bundle-v1", origin, tip, objects}`, where `objects` holds the encoded genesis and every object on the parent chain from `tip` back to genesis, sorted by ID.
Copies and approvals travel inside R, never standalone; the bundle holds no identity keys.
The verifier returns `{status: valid|invalid|incomplete, check, missing, tip, base, branches:[{event, description, valid_at_parent, state_after}], conflict, reconciliation:{signer, approving_members, adopted, rejected}, final_state, scope}`.
`scope` says the verifier checked only the supplied history; it cannot say what any device received or whether other branches exist.

## 4. Validation rules

**Reconciliation checks**, in this order; the first failure names its check, and a missing base or base ancestor makes the result incomplete:
1. *Core*: R, every copy, and every approval decode canonically, and their IDs, signatures, and origins verify.
2. *Shape*: R's author equals C.signer, R's parent equals C.base, and C has at least two resolved tips.
3. *Base*: the receiver derives the nearest common ancestor of the resolved tips, following parent links through the copies and then its own spine, and it must equal C.base.
4. *Partition*: the covered events are every event between the base and a resolved tip; the copies are exactly the covered events, and `adopted` and `rejected` partition them with no duplicates, overlap, or extras.
5. *Original validity*: every copy is valid at its own parent's state.
6. *Closure*: rejecting an event rejects its covered descendants.
7. *Replay*: `adopted` respects ancestry, and each step is valid at its new preceding state, starting from the base state.
8. *Authority*: the policy at the base authorizes R and its approvals (section 5).

**View**, computed in separate steps so suppression and head selection do not depend on each other circularly:
1. *Validate* each stored object: pending if its ancestry is incomplete, else valid or invalid; reconciliations use the checks above.
2. *Suppress* the covered events of every valid reconciliation, including competing ones; a standalone copy of a covered event never becomes a head.
3. *Heads* are valid ordinary events and reconciliations that are not suppressed and have no valid child other than approvals.
4. *Classify*: one head is followed, and the acting state is its state.
   With several heads, the base is their nearest common ancestor, and the acting state is the base state.
   The split is a **conflict** if any branch holds a reconciliation after the base or the compatibility check says incompatible or unsupported; otherwise it is a **split awaiting join**.
   Both block ordinary publishing and report every head; only a conflict allows reconciliations and approvals.

Competing reconciliations, unrelated heads beside a reconciled split, and new children of rejected events therefore stay visible and blocking.
Resolving them is deferred, not decided by the evaluator.

**Provisional answers to RD's questions:**
1. *Fields* as in section 3; R names its base twice, and the receiver derives it independently.
   Alternative: parents = resolved tips; rejected because rejected events would become core ancestors and could never be deleted (OQ Q3's reasoning for joins).
   E1's `rejected_tips` equals `resolved_tips ∩ rejected` and is omitted.
2. *A pause begins* when the view classifies a conflict.
   **What "pause" means (owner, 2026-10-10):** roughly "resolve the issue before adding to the Constitution."
   It limits what a device's Manager publishes to the Constitution; it does not stop apps, which in at least some cases should keep operating normally.
   Slice 1 has no apps, so it implements only the publishing guard and the acting state; names and docstrings should say "Constitution publishing paused," not suggest that the device or team stops.
   "Split awaiting join" is a temporary limitation of slice 1: OQ Q15 says devices join compatible splits right away.
   Alternative: every split pauses; simpler, but it leaves Q2 untested.
3. *A pause ends* when the view has one head again.
   The publisher runs the same checks on its own R, an unfinished draft changes nothing, and a published R with too few approvals is invalid, not pending.
4. *Approvals* name D, so a changed proposal needs new approvals.
   Alternatives: naming R's ID creates a cycle; omitting the signer lets any device publish another's approved proposal.

**QA's E1-E4 are provisional choices adopted for this slice, not settled design:**
E1, approvers sign D over C, which binds the designated signer; E2, an approval's sole parent is C.base;
E3, approvals change no state, are never heads, and sit outside coverage (this changes Q19's coverage scope);
E4, all authority is checked against the base tables, with approvals passed in as part of the operation, never inserted into the tables.

## 5. The fixed policy, `spine-demo-1`

State tables are `members(member_id, name)`, `devices(device_key, member_id, label)`, and `policy(name, settings)`, keyed by content.
An event is checked against the state at its parent; its signer must be a device in that state, and that device's member is the actor.
- `add_device` is valid iff `member_id` is the actor and `device_key` is absent; it adds the row.
- `remove_member` is valid iff the target is a member other than the actor; it deletes the member and the member's devices.
- An approval is valid iff its signer is a device at its parent; it changes nothing.
- A split is compatible iff every ancestry-respecting order of the branch events succeeds and all orders reach equal tables; more than 6 events is unsupported.
- A reconciliation is authorized iff its signer is a device at the base, and approvals that parent the base, name the recomputed D, and come from devices at the base cover at least 2 distinct members.
  Two devices of one member count once; the publisher's signature is not an approval.

## 6. Scenario and demo

Story steps are methods in `spine/scenario.py` that only publish and deliver; they return no outcomes.
The controller moves encoded bytes and may duplicate or withhold them; its whole API is `outbox(device)` and `deliver(device, bytes)`.

1. Genesis G lists Alice (device a), Bob (b), and Carol (c); Carol adds phone c2 as event P, delivered to all, so the base is P.
2. a publishes X (remove Bob) and b publishes Y (add b2), with neither delivered yet.
3. c receives Y and follows it, then receives X and pauses; b gets X and a gets Y, and both pause; c gets X a second time.
4. Carol tries to add tablet c3, and c refuses.
5. a drafts C (adopt X, reject Y) and signs approval U; c receives both, describes the proposal, and Carol signs V, which goes to a.
6. a assembles R with U and V, checks it, and follows it; b and c receive R, check it, and follow it.
7. Carol adds c3 on top of R, and a and b follow it.
8. c exports a bundle with tip c3; a subprocess runs `verify` with G's ID passed separately.

**No join is needed:** the only split is resolved by a reconciliation, c3 waits for R, and approvals never become heads.
`packages/small-sea-manager/tests/test_spine_scenario.py` runs the steps and asserts literal views between them.
`uv run python -m small_sea_manager.spine.demo --dir DIR` runs the same steps and prints a, b, and c's views after each, then the verifier's explanation.

## 7. Micro tests

Expected values are literals (member names, device labels, check names), never read from fixture objects.
Each mutation that changes C recomputes D, collects fresh approvals from a and c, and re-signs R, so that quorum cannot mask the intended check.

| Test | Computed by |
|---|---|
| Vectors: fixed-seed genesis and event give the committed bytes, ID, and signature | `encoding` |
| Duplicate key, float, extra key, uppercase hex, unescaped non-ASCII, or reordered keys: refused | `encoding` decoder |
| Genesis with a duplicate device, a device of an unlisted member, or other settings: refused | `policy` |
| Object whose ancestry reaches another genesis: never valid; different envelope for a known ID: "envelope conflict" | c's view and receipt |
| Bob's laptop signs `add_device` for Carol, or b2 signs at P: invalid, no head | c's view |
| Event whose parent is an approval: invalid | c's view |
| Y alone: Y followed, tables {Alice: a; Bob: b, b2; Carol: c, c2}, no pause | c's view |
| X and Y before P: both pending, view at G; P arrives: conflict, heads {X, Y}, base P, "Y fails after X", acting state P's tables | a fresh device's view |
| Same objects in two orders to two fresh devices: both views equal the literal above | both views |
| Alice adds a2 while Carol adds c3: split awaiting join, publishing blocked, no reconciliation offered | c's view |
| Seven concurrent additions: unsupported, so conflict | c's view |
| Unrelated head Z beside X and Y, then valid R resolving X and Y: still conflict, heads {R, Z} | c's view |
| Competing R1 (adopt X) and R2 (adopt Y, approved by Bob and Carol), both valid: conflict, heads {R1, R2} | c's view |
| R reaches a fresh device first, then Y for the first time: Y suppressed, no head | that device's view |
| R reaches a device lacking P: pending, missing [P], pause unchanged | that device's view |
| Paused c refuses `add_device` but publishes an approval | c's publish guard |
| Stale U and V on C′ (adopt Y, reject X): invalid, authority | c's check |
| C.base = G, rejecting P too: invalid, base | c's check |
| `adopted=[X], rejected=[X, Y]`; Y in neither; an extra event: invalid, partition | c's check |
| Y's copy altered and R re-signed: invalid, core; a copy invalid at its own parent: invalid, original validity | c's check |
| Y1 adds b2 and Y2 (b adds b3) builds on it; reject Y1, adopt Y2: invalid, closure | c's check |
| Adopt [X, Y]: invalid, replay fails at Y | c's check |
| R author differs from C.signer: invalid, shape; only U, or approvals from c and c2, or R signed by b2: invalid, authority | c's check |
| Alice and Bob approve, b signs R removing Bob: valid, followed | c's view |
| Restart during the pause, and after deleting standalone X and Y: same views; lookup of Y resolves from R | reopened c |
| Scenario: literal views of a, b, c after each step; c's description of the package says Bob removed, b2 not added | the named devices |
| Verifier: valid; Y's state has Bob and b2, final state has neither; approvers Alice and Carol; scope stated | `verify` subprocess |
| Bundle with another origin: rejected; without P: incomplete, names P; altered copy: invalid; no key bytes present | `verify` subprocess |

## 8. Work breakdown

| # | Task | Depends on | Judgment |
|---|---|---|---|
| T1 | `encoding.py`, including D and the bundle format, with vectors and decoder tests | — | medium |
| T2 | `policy.py` with micro tests on hand-built states | T1 | medium |
| T3a | `history.py`: pinned genesis, state at an object, original validation | T2 | medium |
| T3b | `reconcile.py`: checks 1-8 | T3a | high |
| T3c | `view.py`: validate, suppress, heads, classify | T3b | high |
| **R1** | **Strong review of T3a-c against section 4; a different agent writes the view and mutation micro tests on in-memory objects; fix before T4** | T3c | high |
| T4 | `proposal.py` | R1 | low |
| T5 | `device.py` with restart tests | T4 | medium |
| T6 | `scenario.py` and the scenario test | T5 | medium |
| **R2** | **Check that every assertion tests computed behavior (a view, a check, a refusal), never data the fixture built** | T6 | — |
| T7 | `explain.py`, `verify.py`, verifier tests | T6 | high |
| T8 | `demo.py` | T7 | low |
| **R3** | **Owner runs the demo and reads the verifier's explanation** | T8 | — |

After each delegated task, grep the diff for the listed tests and real assertions; agent reports have overstated before.

## 9. Decisions for the owner

1. **ID scheme:** hash of the body without the signature, with origin and kind in the body.
   Caveat: the ID does not identify the signature bytes, so section 3's envelope-conflict rule is needed; the alternative is the probe's hash of body plus signature.
2. **R's sole parent is its base**, derived and checked by the receiver, with resolved events carried as copies.
3. **Pause trigger** (approved 2026-10-10; see the owner's meaning of pause in section 4): classify splits, pausing only on a conflict; "split awaiting join" is a temporary limitation until joins exist.
4. **Policy details** in section 5, plus QA's E1-E4 as provisional choices.
5. **Derived view, for slice 1 only.**
   Caveats: resets need persistent local acceptance (OQ, "Events built on an unaccepted reset are held"), and a derived view cannot say which branch a device acted on earlier, which app anchors will need.

## 10. Out of scope

- Hub, MinIO, Cod Sync, screens, and outsider comprehension sessions.
- Mechanical joins, app records and anchors, the history query, key release and rotation, and resets.
- Resolving competing reconciliations, unrelated heads beside a reconciled split, or new children of rejected events; slice 1 only keeps them visible and blocking.
- Policy flexibility, including comparing two policies.
- A second, independent verifier; the shared evaluator is a known risk, offset by literal expectations and mutation tests.
- Cutting team flows over and deleting the old constitution modules.
