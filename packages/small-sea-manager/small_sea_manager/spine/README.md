# The Constitution spine

The spine is a research replacement for today's constitution code (`constitution_store.py`, `constitution_projection.py`, `wrasse_trust/events.py`).
It lives in the Manager package beside the old modules, and no team flow calls it yet; a later step will cut the team flows over to it and delete the old modules.
It is meant to organize development of the framework, not to be thrown away.

Slice 1, finished 2026-11-07, tells one story:
Alice removes Bob while Bob adds a device.
Every device notices the conflict and pauses Constitution publishing.
Alice signs a reconciliation that keeps her removal and rejects Bob's device.
Carol's device, which had followed Bob's addition, asks Carol to choose; she accepts, and Bob refuses.
A fresh process, given only a bundle that Carol's device exported and the team's origin, checks the history and explains it.

```
uv run python -m small_sea_manager.spine.demo --dir EMPTY_DIR
uv run python -m small_sea_manager.spine.verify --origin ID BUNDLE [--json]
```

`scenario.py` holds the story's steps, and `tests/test_spine_scenario.py` asserts every device's view after each one.

## 1. Direction

The owner set these directions in October 2026; the design follows them.

- **Materialize whole Constitution states**, one per head or candidate resolution, so people can see what each choice leads to.
- **No multi-parent events.** Every event, join, and reconciliation has one parent.
- **Constitution changes are rare.** Device changes are more common than membership changes, but still infrequent; several changes per minute in one team should be extremely rare.
  So the design may stop and ask people when unusual combinations happen, and need not handle long chains of collisions gracefully, as long as it stays safe and visible.
- **The common collision must just work.** Two members each adding a device of their own at the same time must resolve without anyone doing anything (the join).
- **One member decides.** A reconciliation is signed by one member; there is no quorum, approval, or proposal state in the core.
  An objection is a competing reconciliation on the same base.
  Silence is not consent; whether a team moves on without someone's acknowledgement is a policy choice.
  Team fission (continuations that share a prefix) is the backstop, and it must be loud: every teammate chooses which version of the team to follow.
- **Never follow on your own a reconciliation that rejects what you already followed.** The owner chooses (perhaps a configurable policy later).
- **Pause means "resolve the issue before adding to the Constitution."**
  It limits what a device's Manager publishes to the Constitution; it does not stop apps, which in at least some cases should keep working normally.
- **Receipt means acceptance**, provisionally: a device that receives a valid, non-conflicting event accepts it.
  ("This is local-first, so we fundamentally can't control what all devices do. But we have to start somewhere.")
- **One fixed policy for now.** Merge compatibility should eventually be configurable policy.
- **Devices should rotate keys periodically**, perhaps twice a year; a team of about 100 devices would then see at most a few rotations per day.
  Not built.

## 2. Storage

Each device has one SQLite file, used through SQLAlchemy `text()` as `constitution_store.py` does, so lessons carry over to the Core berth database.
`device.py`'s docstring lists the tables.
Only `spine_object` would be synced after cutover; the rest are local.
No table stores tips or a pause: `view()` recomputes them from the held objects, the followed head, and the refused reconciliations, which are the only remembered choices.
The device records the followed head whenever the view says "followed" or it publishes, and never moves it to a conflict's base; otherwise a reconciliation rejecting what the device followed could be followed without the owner choosing.
Closing and reopening a device gives the same view.

## 3. Encoding and object formats

**Canonical bytes**: `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` encoded as ASCII.
Values are objects, arrays, strings, and non-negative integers; no floats, booleans, or nulls.
Byte values are lowercase hex: keys and IDs 64 characters, signatures 128.
The decoder rejects duplicate keys, unknown or missing keys for the kind, wrong types, and any input whose re-encoding differs from the received bytes.

**IDs and signatures** use domain prefixes ending in a zero byte:
- object ID = SHA-256(`"spine-v1 id\0"` + canonical body);
- signature = Ed25519 (RFC 8032) over `"spine-v1 sig\0"` + canonical body.
  Our signer is deterministic, but verifiers accept any nonce, so a key holder can make several valid signatures over one body.

The ID excludes the signature, so one rule covers signed and unsigned objects.
A store keeps the first envelope for an ID and refuses a later, different one with reason "envelope conflict".
Copies inside a reconciliation are checked on their own bytes; a mismatch with a stored standalone is logged as an envelope conflict and does not change the reconciliation's verdict.

- **Genesis** (unsigned): `{format:"spine-v1", kind:"genesis", nonce, time, policy:{name:"spine-demo-1", settings:{}}, members:[{member_id, name}], devices:[{device_key, member_id, label}]}`.
  Valid iff member IDs and device keys are unique, every device names a listed member, and the policy is exactly this one.
  Its ID is the team's origin, which each device and the verifier learn out of band.
- **Signed event**: `{body:{format:"spine-v1", origin, kind, author, time, parents:[one ID], payload}, sig}`.
  Its ancestry must reach the pinned genesis; carrying the right `origin` is not enough.
- **Time**: whole seconds since 1970 (UTC) by the author's clock, in a genesis and every signed event.
  It is only the author's claim, so no validity rule reads it; it shows what time the author's device thought it was.
  A join has none, because devices must build identical joins.
  Because of it, IDs differ from run to run.
- **Ordinary**: `{op:"remove_member", member_id}` or `{op:"add_device", member_id, device_key, label}`.
- **Reconciliation R**: a signed event of kind `reconciliation` with `payload {tips, adopted, rejected, copies}`.
  `adopted` is in replay order; `tips`, `rejected`, and `copies` are sorted.
  Its author is the one member who decided.
  Its parent is its base (section 4), and it carries copies of the events it decides.
- **Join** (unsigned): `{body:{format:"spine-v1", origin, kind:"join", parents:[base], payload:{tips}}}`, with `tips` strictly sorted.
  A join carries no copies, so devices holding the same heads build the same bytes.

**Bundle**: `{format:"spine-bundle-v1", origin, tip, objects}`, where `objects` holds the encoded genesis and the evidence needed to verify `tip`, sorted by ID.
Export follows parent links, join tips, and copies inside reconciliations.
Copies travel inside R rather than as separate entries, and the bundle holds no private keys.
`verify.py`'s docstring describes the verifier's result.

## 4. Validation rules

**Reconciliation checks**, in this order; the first failure names its check, and a missing base or base ancestor makes the result incomplete:
1. *Core*: R and every copy decode canonically, and their IDs, signatures, and origins verify.
2. *Shape*: R names at least two distinct tips.
3. *Base*: the receiver derives the nearest common ancestor of the tips, following parent links through the copies and then its own spine.
   R's parent is either that ancestor (a classic reconciliation) or one of the tips (an anchored one, below).
4. *Partition*: the covered events are every event after the nearest common ancestor on a tip's path; the copies are exactly the covered events, and `adopted` and `rejected` partition them with no duplicates, overlap, or extras.
5. *Original validity*: every copy is valid at its own parent's state.
6. *Closure*: rejecting an event rejects its covered descendants.
7. *Replay*: `adopted` respects ancestry, and each step is valid at its new preceding state, starting from the base state.
8. *Authority*: R's signer is a device at the base.

A classic reconciliation's tips may lie on each other's paths, so it can roll back an event without a real split; devices that followed the event must choose, and devices that never saw it do not notice.

**Anchored reconciliations** settle an event that arrives late beside an earlier reconciliation.
R's parent is one of its tips, the anchor, and these rules change:
- No tip may lie on another tip's path.
- The anchor's history after the nearest common ancestor must hold a reconciliation or a join.
  If it held only ordinary events, a sibling decision could reject the anchor itself, and two such reconciliations could reject each other's anchors.
- The covered events leave out what the anchor's history already includes: its own events, the events its reconciliations adopted, and the events its joins covered.
- R is invalid if a tip's path holds an event that the anchor's history rejected.
- Replay starts from the anchor's state, and authority is checked there, so a device added on the anchor's side may sign R.

**Join checks**: a join names at least two tips, none on another's path; its parent is their nearest common ancestor; every covered event is ordinary and valid at its own parent; and `policy.merge` says the covered events merge.
A join needs no authority because it rejects nothing.

**View**, computed in separate steps so that suppression and head selection do not depend on each other.
`compute_view` takes the held objects, the pinned genesis, the followed head, and the refused reconciliations, so arrival order does not matter.
1. *Validate* each object: pending if its ancestry is incomplete, else valid or invalid; reconciliations and joins use the checks above.
   A refused reconciliation, and everything built on it, is invalid on this device.
   An event that builds on an event a valid reconciliation rejected is invalid ("builds on a rejected event"); it is kept as evidence and does not block.
2. *Suppress* the covered events of every valid reconciliation and join, including competing ones; a standalone copy of a covered event never becomes a head.
   Also suppress a join if either a larger valid join covers its events or a valid reconciliation already decided them.
3. *Heads* are valid objects that are not suppressed and have no valid child.
4. *Classify*, in this order:
   - **Owner must choose** if a reconciliation on the heads' history rejects an event that the followed head's history includes.
     The acting state stays the followed head's, and the Manager may publish only a reconciliation.
     The owner accepts by following that reconciliation, or refuses it, which puts the device back at the split.
   - **Followed** with one head; the acting state is its state.
   - With several heads, the base is their nearest common ancestor.
     If the heads' events merge, the view builds the join, the acting state is the join's, and the Manager may publish only that join (**split awaiting join**).
     A join head counts as its tips, so a late child of a joined event leads to a larger join.
     Over `policy.merge`'s six-event limit, the split is **too many to check automatically**; otherwise, including when a branch holds a reconciliation, it is a **conflict**.
     In both, the acting state is the base state, and the Manager may publish only a reconciliation.

Competing reconciliations and unrelated heads beside a reconciled split stay visible and blocking; an anchored reconciliation settles the second.
A pause begins with a conflict, too many events to check, or an owner choice, and ends when the view has one head again.
A split whose events merge does not pause in practice: the device acts on the join's state and publishes the join.
A publisher runs the same checks on its own objects that a receiver would.

## 5. The fixed policy, `spine-demo-1`

State tables are `members(member_id, name)`, `devices(device_key, member_id, label)`, and `policy(name, settings)`, keyed by content.
An event is checked against the state at its parent; its signer must be a device in that state, and that device's member is the actor.
- `add_device` is valid iff `member_id` is the actor and `device_key` is absent; it adds the row.
- `remove_member` is valid iff the target is a member other than the actor; it deletes the member and the member's devices.
- Branch events merge iff every ancestry-respecting order of them succeeds and all orders reach equal tables; more than 6 events are too many to check.
- A reconciliation is authorized iff its signer is a device at its base (at the anchor, for an anchored one).

## 6. Decisions and their caveats

1. **The ID is the hash of the body without the signature**, with origin and kind in the body.
   Caveat: the ID does not identify the signature bytes, so the envelope-conflict rule (section 3) is needed.
   The alternative is to hash body plus signature, as the earlier signed probe did.
2. **R's only parent is its base**, derived and checked by the receiver, with the decided events carried as copies.
   The alternative, making the tips R's parents, was rejected because rejected events would become ancestors that could never be deleted.
3. **Splits are classified**, pausing on a conflict or too many events; a split whose events merge is joined.
4. **The view is derived from held objects plus two remembered choices**, the followed head and the refusals.
   Caveat: app anchors will need more history of what a device acted on than the last followed head.
5. **Children of rejected events are invalid**, not blocking.
   The owner calls this a policy question: the protocol could work either way.
   A branch that *adopts* what another rejected still goes to a person.
   An event whose parent is known only as a copy inside an *invalid* reconciliation is a different case, because nobody validly rejected that parent: the copy counts as a received event with its own disposition, so a malformed reconciliation cannot sink honest events.
6. **A late child of an adopted event** (Alice adds a2 on X before she sees R, which adopted X) is a conflict, settled by an anchored reconciliation that covers only the new event.
   This replaced an earlier owner decision to treat it as a split awaiting join.

## 7. Open items and known gaps

- **The "builds on a rejected event" rule lives only in the view.**
  Moving it into the evaluator makes check 5 call itself in a loop, so `check_reconciliation` alone accepts a reconciliation whose base builds on a rejected event, while the view refuses it.
  `test_spine_view.py` pins the gap.
- **The replay guard** in `reconcile.py` refuses non-ordinary replay steps, which the partition check already makes impossible; kept as protection on the most sensitive path.
- **One evaluator.** The verifier runs the same `reconcile.Evaluator` the devices run, so a bug there would fool both.
  Literal expectations and mutation tests offset this; a second, independent verifier is not built.
- **The verifier describes only the latest reconciliation** on the tip's path.
- **The device stores its private key in the clear**; this is a demo store.

## 8. Not built yet

These are candidates for the next slice:
- Hub, MinIO, and Cod Sync transport; screens.
- Sessions in which outsiders read the demo and the verifier's explanation; the owner will recruit participants.
- App records and anchors, and "acting" while a split exists (an event that reads no row any branch wrote could be published at the split point, keeping it mergeable).
- A query over history, key release and rotation, and resets.
- Resolving competing reconciliations automatically; slice 1 keeps them visible and blocking.
- Rejections that apply only within their own continuation, letting equal-state reconciliations pass without a choice, telling authors their event was rejected, and fission screens.
- Configurable policy, including comparing two policies.
- Cutting team flows over to the spine and deleting the old constitution modules.
