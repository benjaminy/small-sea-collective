# Decisions for #226: what integrating Core state means

Draft for the owner.
Each entry asks one question, lists options, cites evidence, and gives a recommendation.
Recommendations are the drafter's and are not decisions.
Evidence comes from `inventory.md`, `parked-to-merge.md`, and `packages/small-sea-manager/tests/test_core_merge_characterization.py` (called "the tests" below).

Two facts run through every entry.
Today's merge driver keeps ours on every conflict, and it accepts a peer's rewrite or deletion of a signed row (the tests, `test_update_on_one_side_applies_cleanly` and `test_delete_on_one_side_applies_cleanly`).
The only production path from a peer to `core.db` is `integrate_core_events`, which copies signed `constitution_event` rows and moves no other table.

## 1. Snapshots or events

**Question:** Does integration accept a peer's whole `core.db` file, or only the signed events inside it?

- **A. Events only.** Read the peer's `constitution_event` rows, verify, store, and recompute projections.
  Other tables never come from a peer file.
- **B. Snapshot merge.** Merge the peer's `core.db` table by table with a per-class rule (entry 2).
- **C. Events for signed classes, snapshot rules for the rest**, once the unsigned tables are classified.

Evidence: A is what `integrate_core_events` already does, and it never moves `teammate.display_name`, `app`, `team_app_berth`, `team_setting`, the admission tables, `admission_revocation`, or `device_prekey_bundle`.
B on the current driver fails on signed rows: two devices of one steward each endorse a proposal, and the merge exits nonzero on the UNIQUE constraint (`test_two_endorsements_by_one_teammate_make_the_merge_fail`).
The constitution says the event DAG is the protocol artifact and the SQL layout is an implementation around it.

**Recommendation:** A for now, and C only as tables gain signed forms.
The cost of A is that unsigned tables have no peer route until someone signs them or decides they stay local.

## 2. Rule per content class

**Question:** What does integration do with each class?

Proposed rules (signed history and projections are close to forced; shared data is the real choice):
- **Signed history:** union by id; identical bytes are one row; a different row under an existing id is refused (entry 5).
  Never update or delete.
- **Local mutable projection:** never merged from a peer.
  Recompute from signed history after integration.
- **Imported shared data:** either (a) last-writer-wins by a signed timestamp or sequence, (b) keep both and ask a person when they differ, or (c) become signed events and fall under the first rule.

Evidence: signed rows merged by the driver lose their protection, since rewrite and delete both apply cleanly.
Projections duplicate when two devices each derive them: two `berth_role` rows for one teammate and berth survive with different roles (`test_two_devices_projecting_one_berth_role_produce_duplicate_rows`), and a deleted teammate leaves an orphan `team_device` because the driver applies rows with foreign keys off.
Shared data loses a peer's change to a different column of the same row (`test_changes_to_different_columns_of_one_row_still_conflict`) and duplicates on random ids (`test_two_devices_activating_one_app_produce_duplicate_rows`).

**Recommendation:** the signed-history and projection rules as written.
For shared data, prefer (c) where the data affects authority and (b) elsewhere, because Human-Scale Coordination names pausing for a person as a first-class option.
Option (a) is the simplest, but it picks a winner silently.

## 3. Convergence target

**Question:** What must two devices that hold the same publications agree on?

- **A. Same event set.** Same `constitution_event` rows, nothing more.
- **B. Same event set and same derived view.** Also the same `key_certificate`, `team_device`, and `berth_role` contents, which are functions of the events.
- **C. Byte-identical `core.db`.**

Evidence: the constitution says teammates who hold the same events hold the same Constitution, and that each device computes its view locally.
C is unreachable while random uuid7 ids and local writes exist.
`parked-to-merge.md` shows integration adds a one-parent commit, so two devices never share git history either.

**Recommendation:** B, with the derived view defined as a pure function of events plus the adopted anchor.
Tables not derived from events are outside the target until they are classified.

## 4. Validation of a candidate against #173 and #174

**Question:** What must a candidate pass before adoption, given that the envelope (#173) and DAG (#174) are unbuilt?

- **A. Today's checks:** `check_event` signatures, id-versus-bytes, and the whole-batch refusal on one bad event.
- **B. Add #174's structural checks now:** bounds, origin, canonical parent set.
- **C. Define the candidate check as "whatever the core verifier accepts"** and let #173 and #174 fill it in.

Evidence: size limits, origin rules, and missing-parent requests are listed as not yet built.
`parked-to-merge.md` finds no verification of `self_publication` refs, and none in `integrate_core_events` that the source chain descends from the local anchor.
Format `v0` is explicitly not frozen.

**Recommendation:** C.
Name one verifier entry point that integration calls, keep A behind it, and do not duplicate envelope rules here.
Also require that commit-history verification has run on the source (#190), or that the entry point says why fetch-time verification suffices.

## 5. Content-identity collisions

**Question:** What happens when two rows or events claim one identity with different content?

- **A. Refuse the whole source batch** (today's behavior for events: `EventConflictError`).
- **B. Refuse that event, keep the rest.**
- **C. Keep both, mark the identity ambiguous, and pause for a person.**

Evidence: a same-key, different-content insert keeps ours and only warns on stderr (`test_insert_with_same_key_and_different_content_keeps_ours`).
That is wrong for signed history, where a collision means tampering or a bug.
For a content-derived event id, a collision with different bytes cannot happen honestly.

**Recommendation:** A for signed history, since the case indicates corruption and the batch is untrustworthy.
For shared data, C if the class survives entry 2.
B risks a source that is partly accepted with silent gaps.

## 6. Semantic refusal

**Question:** Is there an outcome "valid but refused by policy", separate from "invalid", and what does the caller do with it?

- **A. No.** Valid events are always stored; judgment is a local view (today's stance).
- **B. Yes, for a small set of cases:** for example a source that would remove the anchor or undermine another removal, reported as `refused` with a reason, and the parked source is kept.
- **C. Yes, generally:** any extension can veto adoption.

Evidence: the constitution says authority is not checked on arrival, that conflicting authentic events are both kept, and that self-undermining removals pause for a local decision.
So the "pause" outcome exists already in the view, not in the merge.

**Recommendation:** A for storage, with B's pauses expressed as view states.
Integration would then have two outcomes, adopted or refused as malformed, and a person resolves semantic problems from the view.

## 7. Candidate versus adoption

**Question:** Where is the line between evaluating a candidate and changing the live checkout?

- **A. In place** (today): edit live `core.db` under `BEGIN IMMEDIATE`, then commit.
- **B. Candidate in a temp copy, then adopt by swapping the accepted bytes into the live file.**
- **C. Candidate copy, then adopt by replaying its row changes into the live database and committing a two-parent merge**, as NoteToSelf does.

Evidence: with A, a valid source's effects land in the live file before the git commit, and a failed commit returns `integrated` with `git_record_failed`.
The NoteToSelf code never overwrites the live file because the Hub may hold it open, and its two-parent commit makes the source an ancestor of `main`.
With today's one-parent commit, `contained_in_main` stays false for that head forever.
A temp candidate removes cleanup problems but not the live-update risk.

**Recommendation:** C, with adoption as its own step that fails as a unit and cannot leave the live database ahead of `main`.
B is simpler but races the Hub.

## 8. The tables the inventory could not classify

`inventory.md` marks 7 tables "unclear", not 8: `teammate`, `app`, `team_app_berth`, `invitation`, `team_setting`, `admission_revocation`, `device_prekey_bundle`.
I add `berth_role` as the eighth, because the inventory calls it a projection but notes its initial rows have no signed backing (`provisioning.py:5720`).
`team_device` has the same seed problem, so the owner may want it here too.
Please confirm which table you meant.

Each row asks the owner one question.
The options are always: signed history (make it an event), imported shared data (state a merge rule), local projection (never merged), or delete.

| Table | Question | Recommendation |
|---|---|---|
| `teammate` | Is `display_name` a claim the teammate signs, or a label each device keeps for itself? Row existence already follows certificates. | Existence is a projection; `display_name` is a signed claim if peers should see it, otherwise local. |
| `app` | Is an app's identity its name (one row per name, deterministic id) or a random id per device? | Deterministic id from the name, so two devices cannot duplicate it. |
| `team_app_berth` | Is a berth an event that a teammate signs, given that signed records already name berths by this id? | Signed event, because signed rows depend on it. |
| `invitation` | Does anything still need it? Nothing writes it, but `admission_events.py:244` and `backend.py:1541` read it. | Delete after those readers are checked. |
| `team_setting` | Are quorum and expiry team-wide rules that need a signature, or per-device preferences? | Quorum is signed governance; expiry may be local. |
| `admission_revocation` | Is a revocation a team fact (signed and replicated) or a local disposition, as its schema comment says? | Decide first; if team fact, sign it. Today it is neither. |
| `device_prekey_bundle` | Is it latest-wins shared data, or a cache of a signed bundle that should be re-fetched? | Latest-wins keyed on a signed sequence number. |
| `berth_role` | Is the role always derived from `integration_mode_change`, so the seed insert at app activation should become a signed event? | Yes: purely projected, and drop the unsigned seed. |

These recommendations are guesses about intent.
The owner knows what the tables were meant to be.
