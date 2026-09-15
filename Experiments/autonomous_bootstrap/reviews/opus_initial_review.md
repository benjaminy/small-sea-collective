# Initial Opus review (unadopted recommendations)

This is saved model output, not a project decision.
Some recommendations conflict with local-policy boundaries and need independent assessment.

# Review: bootstrap trust transcript (`issue-262-bootstrap-transcript`)

## Verdict

The transcript's decomposition into five decisions is the best thing in it, and the draft violates that decomposition in B1, B6, and B8. The code findings (self-issued cert as root; unsigned quorum; unsigned `berth_id`; prekey bundle bound to its own embedded key) look correct and are the strongest part. The *design* around them is weaker than the findings: three of the ten required changes are unimplementable as stated, and one of the six B6 outcomes is attacker-selectable.

**Settled (from the supplied architecture/constitution docs):** no team server; core-verifies-bytes / extensions-assign-meaning; the five core checks; handling states and parking; the basis object; Hub-as-sole-gateway; endpoint-trust-scoped reads; forward-only Git; pausing as a first-class outcome.

**Proposed by the draft (not settled):** the five-decision split; B1's five-part bidirectional commitment; B4's genesis authority root and frontier equality; B5 parameter naming; B6 comparison; B8's removed-author pause; B9 berth+purpose scope; B10 strictness; signed NoteToSelf device authority.

**Proposed by me (clearly mine, not in either document):** root *set* named in the commitment; self-parent rule; removal cut; monotone-more-authority rule; B6 as lint; taint label; unanimity-across-tips for B10; proof-of-possession at release; identity-as-constitutional-group; commit-then-reveal SAS.

---

## 1. B1's commitment cannot be built on the invitation path

B1 requires the compared value to cover "both parties' long-term public keys, including the newcomer's fresh device signing key." The invitation path says "B1's commitment is created when the inviter creates the invitation." At that moment the invitee's device key does not exist — often the invitee has not installed anything. These two sentences cannot both hold. This is not a wording slip; the asynchrony is the whole point of the invitation path.

The draft's own framing supplies the fix it then ignores. The relay-substitution attack it cites ("the introducer authorizes a device its human never saw") is an attack on **decision 2, admission** — the inviter's decision. It is not an attack on **decision 4, history adoption** — the invitee's. Only the invitee→history direction needs to be fixed at invitation time.

**Minimal change.** Split into two commitments with different times, owners, and decisions:

- `C_offer` (inviter, at creation): scope, inviter key, root set, frontier, snapshot digest. Supports the invitee's decisions 3/4. Conveyed over the trusted channel.
- `C_bind` (at acceptance): `H(C_offer)` ‖ invitee's fresh device signing key ‖ nonce. Supports the inviter's decision 2, and gates decision 5.

State explicitly: an invitation that has been offered but not bound admits *nothing*, and B10 is unreachable until `C_bind` is compared. Today `complete_invitation_acceptance` verifies the acceptance signature against its own proposal row — that is `C_bind` minus the `H(C_offer)` link. Adding the link is small.

## 2. B4 is assigned to the wrong owner, and its root is the wrong shape

B4 says "**Policy owner.** The framework. This is a protocol rule, not an application choice." The constitution document says the opposite three times: "The core does not consult a roster"; whether a key is "delegated, revoked, compromised, or unknown" belongs to an extension; "extensions must not describe their own authorization decision as a guarantee made by the Constitution protocol." "Authority chain terminating at a root" is admission-extension vocabulary. Labelling it framework is exactly the boundary erosion the constitution's narrow-waist section warns against, and it matters practically: a second Manager-class app with a different admission extension would be *required* to accept this chain rule.

Worse, "the genesis authority key" presumes an answer to two of the document's own Open Core Questions (does an origin have exactly one parentless genesis event; is origin a genesis digest or a signed random value). The draft builds its load-bearing step on an unresolved core question.

The shape is also fragile. A single genesis authority key is a permanent single point of compromise for every future bootstrap, in a system whose first pillar is a web of trust with "no central membership oracle." If the founder's key leaks in year three, every subsequent newcomer re-roots on whoever holds it.

**Alternative I'd argue for.** Drop "genesis authority key." The commitment names a **root set**: one or more event IDs the introducer's human vouches for. B4 becomes: every relied-upon authority chain terminates at a member of that set, present and core-verified in the delivered closure; no self-issued certificate is a root unless its event ID is in that set. This (a) fixes the actual `trusted_device_keys_by_teammate` bug identically, (b) survives founder rotation, co-founders, and multi-parent origins, (c) needs no answer to the genesis question, and (d) is honestly an extension rule. Owner: admission extension. The *framework* contribution is narrower and real — origin binding and the snapshot digest.

## 3. B4's frontier equality contradicts the invitation path two sections later

B4: "the frontier present in the snapshot is exactly the event set the commitment named — no more, no fewer," with **Override: None**. The invitation section: "Team changes made after the invitation was issued arrive afterwards, through ordinary sync."

Counterexample: inviter commits frontier `{E1}` Monday; appends `E2` Tuesday; invitee clones Wednesday and receives a repo whose head is `E2`. `E1` is an ancestor, everything is authentic, nothing is wrong — and B4 fails with no override. The only remedy is a fresh invitation, and it races again. Cod Sync delivers bundles and Git clones fetch heads; "no more" makes the common case fail.

**Minimal change.** B4 checks *reachability*, not equality: every committed frontier ID is present and core-verified, and the committed frontier is ancestry-closed within the snapshot. B5 reconstructs authority **at the committed frontier**, not at the delivered head. Events outside that closure are ordinary post-bootstrap sync and face B8/B9. Keep a hard failure for the one case that matters: a committed frontier ID **absent** from the snapshot.

## 4. Backdating is parent omission, not clock lies

B8 says to determine whether a key "held the relevant authority when the work was made," then concedes the timestamp proves nothing. That leaves the check undefined. In a partial order, the only sound reading is causal — the authorizing event is an ancestor. But the author chooses their own parents, so causality is author-controlled.

Counterexample. Mallory is admitted at `G` and removed at `R`. After removal, Mallory authors `E₁…E₁₀₀₀`, each naming only pre-`R` events as parents. Every one is core-verified, every one is causally *concurrent* with `R` rather than after it, and every one satisfies "authority held at authoring time" under any ancestry-based rule. B8's default — "authentic old signatures stay authentic," accept the past — accepts them. Removal therefore bounds nothing: it does not stop a removed key from manufacturing unlimited "past" work forever. The transcript's residual-risk paragraph knows peers cannot prove full disclosure but never connects that to B8.

**Minimal changes (both cheap, neither touches the core envelope):**

1. **Removal cut.** The signed removal record (#263) carries the removed party's last event IDs as the remover observed them. Default extension rule: from a removed author, accept only events reachable from that cut. Anything else is new work wearing old parents — pause, do not accept. This tells #263 *what to put in the record*, which item 6 currently does not.
2. **Self-parent rule.** The admission extension requires each event to name the author's own previous event among its parents. Equivocation by an authentic key becomes a visible fork in that author's own chain rather than an undetectable omission, and "the cut" reduces to a position in one chain. State it as an extension rule so the narrow waist stays narrow.

Also: B8's override is "a human may accept the history," scoped to the past. With no cut, "the past" is unbounded. With a cut, it is a finite, nameable set — which is what makes the human decision reviewable.

## 5. "Differs under policy" is a category the attacker picks

B5 establishes that reconstruction is parameter-relative and that quorum currently comes from the unsigned `team_setting` table — "whoever wrote the snapshot also chose the threshold its own finalizations had to clear." B6 then routes parameter-explained differences to **"Differs under policy: Nothing automatically pauses. Everything permitted, under the recorded parameters."**

Counterexample, end to end. A malicious inviter ships a snapshot asserting Mallory is a teammate, admitted under quorum 1. The invitee reconstructs under quorum 2 and gets no Mallory. B6 classifies this as *differs under policy* — a parameter fully explains it. Nothing pauses. B7 adopts. B9 recognizes Mallory's key. B10 releases sender keys. No step reports an evidence failure, because none occurred: the attacker chose the category by choosing a parameter. Every gate downstream inherits the classification.

**Minimal change (direction matters).** Classification is computed under the **invitee's** parameters only, and any difference where the snapshot asserts *more* authority than the local reconstruction — an extra teammate, an extra device key, a broader role — is **differs on evidence**, regardless of whether a parameter explains it. Snapshot-asserts-*less* stays a benign policy difference.

**Stronger alternative: delete B6.** If B5 reconstructs, the reconstruction *is* the state and the delivered projection tables are a cache the newcomer should discard and rebuild. Nothing about adoption should depend on what the inviter's SQLite rows claim. B6 earns its place as a **lint** — "the introducer's tables disagree with the introducer's evidence, here is the first divergence" — which is useful for diagnosing a broken or dishonest peer and should not gate anything. That removes a whole ambiguity class rather than patching its worst outcome. Note that the architecture already says apps must not open `core.db`; B6 as specified makes the newcomer's security depend on another participant's internal projection format.

## 6. B10 is not fail-closed under concurrency, and "recognized" is not "reachable"

B10 gates on B9 recognition. But the architecture says conflicting removal events "remain concurrent evidence until local policy or people resolve them," and Manager parks branches under resource pressure. So a device whose active view happens to exclude the branch carrying Mallory's removal recognizes Mallory and ships keys. The irreversible action is the one place where a *local* view choice produces an *unrecoverable* global consequence, and the draft gives it the same view semantics as everything else.

**Minimal changes:**

1. **Unanimity across the active view.** B10 requires recognition under the reconstruction at *every* active tip, not the merged view. Any **parked** branch whose closure contains a removal of the recipient blocks B10 pending a human decision. This is the one rule I would make asymmetric, and it fits the architecture's "pause is first-class" principle precisely.
2. **Proof of possession.** The transcript's own prekey bug — material "filed under" a key rather than bound to it — recurs at the protocol level unless release is conditioned on a fresh signature by the recognized key over `nonce ‖ berth_id ‖ key epoch`. "Bind prekey bundles to the device key they are filed under" (item 8) fixes the stored row; this fixes the class.
3. **Taint propagation.** The pause table says an unproved B1 view leaves key distribution paused, but nothing carries that state forward. Give the trust view a monotone label — `proved` / `unproved` / `incomplete` — set at B1/B2/B4/B6 and read by B10, with **no human override on `unproved`**. The draft says a human "should not be able to grant this"; make it a mechanism, not an adverb.

Finally, temper the claim: the architecture already states any admitted endpoint can proxy plaintext. B10's guarantee is "no new *ongoing* access," not "no access." Say so, or B10 reads as stronger than the pillar it sits under.

## 7. Identity and team bootstrap: two roots, no precedence rule

The draft proposes signed NoteToSelf device-authority records "rooted at the person's first device key." The architecture puts device→teammate authority in *team* state ("the signed history associating a device key with a teammate is durable team state") and NoteToSelf labels explicitly outside trust. The draft creates a second authority root for the same fact and never says which wins.

Counterexample: Alice's laptop is stolen. They remove it from team T's Constitution. The NoteToSelf chain still lists it — so the thief keeps syncing NoteToSelf, which per `shared_schema.sql` carries `cloud_storage` and `berth_cloud_allocation`: provider accounts and the map of every team Alice belongs to. A device removed from one team retains the index to all of them. Conversely, a device removed from NoteToSelf but holding a valid team cert is still recognized by B9, whose scope is team-only.

**Alternative I'd argue for.** Make identity a **constitutional group of one person** — its own technical origin, the same envelope, the same B3/B4/B5 machinery — rather than a second signed-record format in `shared_schema.sql`. The constitution document already flags the deferred "constitutional group" generalization; this is the use case that justifies it. One verifier, one revocation story, and stage 1 gets rooting for free.

Then make recognition **conjunctive**: a device key is recognized for berth B only if it is in the identity DAG's active device set **and** holds a team certificate scoped to B. Revocation from either side fails closed, which resolves the stolen-laptop case in the direction users expect. Note this is a real cost — two DAGs to sync — and it is the honest price of the draft's own true observation that "the person's device set is exactly the thing no team can establish for them."

This does not close the transcript's open blocker (how team evidence reaches a sibling device). It does make `_copy_team_baseline` replaceable by a normal bootstrap: stage 2 becomes an invitation the person issues to themselves, with `C_offer` carried inside the already-authenticated identity DAG.

## 8. The 64-bit SAS: fix the ordering, not the length

The draft says a relay shaping both halves searches for a collision at "about half the bits," so the margin is "nearer 2^32." That is right *only when the attacker can grind both halves freely*. Under commit-then-reveal — the newcomer sends `H(its half)` first, the introducer reveals, the newcomer reveals — the attacker must hit a specific target and the work is 2^64 per attempt. So the lever is message ordering, not string length, and 64 bits stays defensible with the short UX intact. This is standard practice (ZRTP, PGPfone).

This also reinforces §1: commit-then-reveal is natural for the synchronous sibling path and awkward across an asynchronous invitation, which is another reason the invitation's `C_offer` should be one-directional and `C_bind` a separate, later comparison.

---

## Delta against the draft's change list

Keep items 1, 3–11 (with 1 amended to a caller-supplied root *set*, and 6 amended to specify the cut). Amend item 2: two commitments, not one bidirectional one. Add:

- **12.** Self-parent rule in the admission extension.
- **13.** Monotone rule in B6: snapshot-asserts-more ⇒ evidence failure; and demote B6 from gate to lint.
- **14.** Taint label on the trust view; B10 refuses on `unproved` with no override.
- **15.** B10 unanimity across active tips; parked removals block.
- **16.** Proof-of-possession challenge at key release.
- **17.** Reassign B4's policy owner to the admission extension.

---

## Three executable local experiments

All are single-machine, temp-dir, no network, no migration, no consensus.

**E1 — Forged-root bootstrap.** Fabricate a team database from scratch: fresh genesis, self-issued membership cert, internally consistent roster. *Arm A:* run `trusted_device_keys_by_teammate` and `accept_invitation` against it; assert it resolves cleanly today (confirms the reported bug). *Arm B:* implement root-set-as-parameter; assert refusal. *Arm C:* take a **genuine** snapshot and pass a *wrong* root; assert refusal. *Arm D:* genuine snapshot, correct root, head advanced by one extra event past the committed frontier; assert **success** — this is the regression guard against B4's "no more" (§3). Pass: A resolves, B/C refuse, D succeeds.

**E2 — Post-removal parent omission.** Two local Managers in temp dirs. Admit Mallory, then remove them (stub the #263 record if needed). Mallory authors 20 events naming only pre-removal parents and publishes bundles. Measure: how many Alice integrates; whether `_constitution_snapshot` still lists Mallory's authority; whether `redistribute_sender_key` selects Mallory's device. Expected before: 20, yes, yes. Then add the removal cut and self-parent rule and re-measure. Pass: exactly the events reachable from the cut are accepted, 0 of the 20 are, and key redistribution refuses. This is the cheapest way to show the transcript that B8 as written grants unbounded future capability.

**E3 — Policy-difference smuggling.** Build a snapshot where `team_setting` quorum is 1 and Mallory is admitted only under that threshold; invitee's local parameter is 2. *Arm A:* run B5/B6 and follow the chain; assert the outcome is "differs under policy," that nothing pauses, that B9 recognizes Mallory, and that B10 would release. *Arm B:* apply the monotone rule plus the taint label; assert B6 returns "differs on evidence" and B10 refuses with no override. *Arm C (false-positive guard):* invert — snapshot quorum 2, local quorum 1, snapshot asserts *fewer* teammates; assert this stays a benign policy difference and does **not** block. Pass: A demonstrates the smuggle, B blocks it, C does not over-block.