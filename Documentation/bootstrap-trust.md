# The bootstrap trust transcript

Status: draft for human review ([#262](https://github.com/benjaminy/small-sea-collective/issues/262)).
It describes the intended design.
Several steps contradict what the code does today; those say so and name the change required.
The branch also contains three runtime fixes: blocking incomplete identity bootstrap, authenticating stored prekey bundles, and committing prekey consumption with received-key persistence.
Those fixes do not implement this transcript's exchange or authority reconstruction.

A device that has never synced must end up trusting a particular team history and a particular set of signing keys.
It cannot get there by checking a downloaded history against itself.
This document follows the evidence from the one thing a newcomer authenticates outside the system to the moment it recognizes a signing key for a specific berth and purpose, and it names, at every step, who decides and what remains unproved.

## What the newcomer starts with

A newcomer holds fresh keys it generated itself and nothing else.
It does not hold the team's history, the team's keys, or any way to tell the real team from a fabricated one.
Everything it will later believe has to come from one of three sources:

- evidence authenticated outside the system, by a human, in step B1;
- Constitution events whose authority chains back to that evidence;
- an explicit local decision, made by its own operator, that this document marks as a decision rather than a proof.

No fourth source exists.
In particular, a downloaded database, a decryptable payload, a transport descriptor, a friendly team name, and a self-consistent list of signers are all things an attacker can manufacture, so none of them can start the chain.

## Five decisions, not one

"Trusting the team" is five separate decisions with different owners and different failure consequences.
Collapsing them is the most common way a bootstrap design goes wrong, so the transcript keeps them apart throughout:

1. **Identity join.** Is this device one of the devices of this person?
2. **Team admission and device enrollment.** Is this person a teammate, and is this key one of their devices?
3. **Historical authorship.** Was some past commit made by a key that held authority when it was made?
4. **Local history acceptance.** Will this device adopt that history as its own active view?
5. **Distribution of future encryption keys.** Will this device send new key material to that key?

Decision 3 is about the past and decision 5 is about the future, and neither implies the other.
Accepting a removed teammate's old work does not authorize sending them tomorrow's keys.
Constitution core verification contributes to none of the five on its own; it only establishes that an event is authentic, which every one of the five then builds on.

## How to read a step

Each step carries an identifier, then the same seven labels.
**Inputs** and **Provenance** say what the step consumes and where it came from.
**Check** is the mechanical operation.
**Policy owner** names who decides — the framework, the admission extension, or the device's own operator.
**Evidence recorded** is what must survive the step for a later reviewer.
**Default conclusion** is exactly what the newcomer may believe afterwards, and no more.
**Override** says what a human may change, remembering that an override changes local policy and never changes what was verified.

Each step is also marked with its implementation status:

- **Implemented** — code enforces this, with the reference given.
- **Partly implemented** — code enforces the named subset; the remaining checks are identified explicitly.
- **Specified but unenforced** — a repository document states the rule and no code checks it.
- **Proposed** — this transcript is where the rule is first stated.
- **Contradicted** — current code does something incompatible, and the required change is named.

## B1 — Authenticate the exchange outside the system

**Inputs.** The newcomer's joining request and the introducer's response.
**Provenance.** Both are constructed locally by their own side; neither is trusted yet.
**Check.** For invitation and initial identity join, the two humans compare one short string, derived from the whole exchange, over a channel they already trust.
After identity join, the selected per-team sibling route can instead authenticate the complete exchange under the already recognized identity-device keys, as specified below.
That route records its inherited identity-recognition basis rather than claiming a new human comparison.

The compared value — call it the bootstrap commitment — must cover five things, in both directions:

1. the operation scope: which identity or which team, and which entry path;
2. both parties' long-term public keys, including the newcomer's fresh device signing key;
3. the technical origin and the explicit authority anchor selected under the bootstrap extension that B4 will check against;
4. the frontier: the exact set of Constitution event identifiers being offered;
5. the digest of the snapshot bytes that will be delivered.

Here, an authority anchor (called the trust root below) is an explicit input to the selected authority extension, distinct from the technical origin.
Independent authentication means independent of the fetched data: the newcomer may learn this anchor through the authenticated introducer and explicitly choose to trust that introduction.
It need not have known the anchor before the exchange, and the exchange does not establish the honesty of the introducer or anchor.

The final commitment is created only after the introducer receives the newcomer's request and fresh keys.
An invitation sent before that request is an offer to start the exchange, not a completed B1 commitment.

Covering both directions matters.
A commitment over only the introducer's response lets a relay substitute the newcomer's half, so the introducer authorizes a device its human never saw.

**Policy owner.** The two humans, for the comparison itself; local policy, for what happens when no comparison is recorded.
**Evidence recorded.** The authentication method, its result and the complete commitment, stored alongside the resulting trust view.
For a human comparison, record that it occurred and matched; for the per-team signed route, retain the signatures and the prior identity-recognition decision used.
**Default conclusion.** The exchange is bound to the compared introducer or previously recognized identity key, and that introducer committed to exactly this root, this frontier and these snapshot bytes.
A key binding alone cannot establish that its current holder is still the intended person.
**Not concluded.** Nothing about whether the introducer is honest, competent, or in possession of a complete view of the team.
B1 authenticates who is speaking, not what they say.
**Override.** A human may proceed with no recorded comparison.
The device then holds an unauthenticated view: it may inspect the data, and ordinary integration, identity use, team joins, and key distribution stay paused until a human accepts the view explicitly.
That acceptance is a local policy decision and leaves the authentication result exactly as it was — unproved.

**Status: Contradicted.**
`welcome_bundle_confirmation_string` (`packages/small-sea-note-to-self/small_sea_note_to_self/bootstrap.py`) covers the join-request artifact, the welcome bundle, and its signature.
It does not cover a trust root, a frontier, or a snapshot digest, so it authenticates who sent the bundle but not what history that bundle points at.
Nothing stores the result: a repository-wide search finds no consumer of the string outside `provisioning.py` and `bootstrap.py`, where it is returned and displayed.
On the invitation path there is no comparison at all; `create_invitation` (`packages/small-sea-manager/small_sea_manager/provisioning.py`) hands out an unsigned base64 token.
Both need to change.

The incomplete-bootstrap marker is a narrower implemented check.
`prepare_identity_bootstrap` creates it before installing fetched state, and Manager and Hub refuse normal access while it exists.
`finalize_identity_bootstrap` checks the welcome signature and matches the fetched joining-device keys to the local request, then clears the marker without a recorded human comparison.
Thus completion of the current runtime checks does not establish B1 or enforce its missing-comparison pause.

One open wire-format question belongs here.
The current string is 64 bits.
A generic collision search over attacker-controlled exchanges has a birthday bound near 2^32 trials, but that is not an established attack cost for the complete ceremony.
Which fields a relay can choose, when each side commits, and how attempts are limited determine whether that search applies.
The wire protocol and its comparison strength need review before the format is frozen; this transcript does not establish a security margin.

## B2 — Bind the delivered snapshot to the exchange

**Inputs.** The bytes fetched from wherever the exchange said to fetch them.
**Provenance.** An untrusted network location, named by the introducer.
**Check.** Recompute the snapshot digest and compare it to the commitment from B1.
**Policy owner.** The framework.
**Evidence recorded.** The digest, and the fact that it matched.
**Default conclusion.** These are the exact bytes the authenticated introducer offered.
**Not concluded.** That the bytes are correct, complete, or honest.
A matching digest proves delivery, not content.
An introducer who authenticates correctly and then offers a misleading snapshot passes B2 cleanly, which is why B5 exists.
**Override.** None.
A digest mismatch is a delivery failure; stop and show it.

**Status: Contradicted.**
`accept_invitation` (`provisioning.py`) clones from the URL in the token and checks out whatever head the store publishes.
It verifies no digest, because the token commits to none.
`bootstrap_existing_identity` (`provisioning.py`) fetches from the descriptor inside the welcome bundle with the same gap.

## B3 — Core-verify the Constitution events

**Inputs.** The Constitution events in the delivered snapshot.
**Provenance.** The snapshot bound in B2.
**Check.** The version-defined structural and cryptographic checks in [the Team Constitution](team-constitution.md#core-verification): canonical decoding, recomputed event id, signature under the named signer key, technical-origin binding, canonical parent set, and graph sanity.
Each event lands in exactly one handling state — invalid, ancestry-incomplete, or core-verified.
**Policy owner.** The framework.
**Evidence recorded.** Each event's handling state, and for ancestry-incomplete events, which parents are missing.
**Default conclusion.** These events are authentic events of this technical origin, made by the keys they name, declaring this ancestry.
**Not concluded.** Anything at all about membership, authority, or which head wins.
The core does not consult a roster, and an authentic signature by an unknown key is an ordinary outcome, not an error.
**Override.** None on the checks.
A human may park a core-verified event, which is an intake decision, not a claim that the event is malformed.

**Status: Specified but unenforced.**
`wrasse_trust/constitution.py` provides canonical bytes, record-id derivation, signing and verification for the current record shape, and the admission records verify individually.
The event DAG the Constitution document targets does not exist yet: there is no technical-origin binding, no parent set, and therefore no ancestry or handling states.
Today's records are SQL rows whose ordering comes from Git merges rather than from declared parents.

## B4 — Bind the frontier and its authority chains to the root

**Inputs.** The core-verified events from B3, the frontier from the B1 commitment, and the trust root from the B1 commitment.
**Provenance.** The events come from the untrusted snapshot; the frontier and root come from the authenticated exchange.
**Check.** Two parts.
First, the selected bootstrap frontier is exactly the tip set the commitment named, and its required closure is present and core-verified.
The byte-exact snapshot checked at B2 may itself contain parked events outside that closure.
B4 selects the committed frontier from that same snapshot; stored events outside its closure do not become part of the selected bootstrap view merely because they arrived in the same repository.
B2 still requires the exact committed snapshot bytes; a newer head is not a substitute for that snapshot.
Second, every authority chain the newcomer intends to rely on terminates at the root the commitment carried, through ancestry that is present and core-verified.
**Policy owner.** The Manager applies the selected bootstrap and authority extension.
The framework checks origin, event integrity and ancestry; the extension decides what counts as an authority chain to the explicitly pinned anchor.
A genesis authority key is one possible anchor for this extension, not a mandatory Constitution-core authority or an answer to its open origin-format questions.
**Evidence recorded.** The root, the frontier, and the chain from each relied-upon key back to the root.
**Default conclusion.** The authority this snapshot describes descends from the root the newcomer's human authenticated.
**Not concluded.** That the described authority is correct or current.
B4 establishes that the graph is the right graph, not that its contents are honest.
**Override.** No decision can make a failed chain reach its claimed root.
The identity policy below permits a separately recorded local recognition decision instead of a historical-chain conclusion; it preserves the failed or missing evidence.
Changing an adopted anchor likewise creates a new local trust decision, not a successful verification of the old chain.

This is the step that makes bootstrapping possible, and the step current code lacks entirely.

**Status: Contradicted.**
`trusted_device_keys_by_teammate` (`packages/wrasse-trust/wrasse_trust/identity.py`) seeds trust from any self-issued membership certificate:

```
if cert.issuer_participant_id == admitted_teammate_id:
    issuer_keys = [cert.subject_public_key]
```

It accepts as many roots as the snapshot happens to contain, and never asks which anchor the newcomer authenticated independently.
An attacker who fabricates a team database fabricates its genesis membership along with it, and the graph resolves without a complaint.
The required change: the resolver takes the root as an explicit parameter supplied by the caller from the exchange, and refuses to treat any certificate as a root unless it matches.
Until that change lands, no amount of care in B5 or B6 means anything, because reconstruction and snapshot will agree by being the same forgery read twice.

## B5 — Reconstruct authority-bearing state under named policy

**Inputs.** The core-verified events in the frontier's closure, plus an explicitly named set of policy parameters.
**Provenance.** Events from the snapshot, rooted by B4; parameters from local configuration or from the exchange.
**Check.** Replay the admission extension's rules over the closure to produce the authority-bearing state: which teammates exist, which device keys each of them holds, which berths exist, and what role each teammate has on each berth.
**Policy owner.** The admission extension, for the rules; the device's operator, for the parameters.
**Evidence recorded.** The reconstructed state, and the parameter values that produced it.
**Default conclusion.** Under these named parameters, this is the authority-bearing state the evidence supports.
**Not concluded.** That another correct implementation would compute the same state.
It would not, if its parameters differ.
**Override.** A human may change a parameter and re-run, which produces a different state from the same evidence.
That is a policy choice and must be recorded as one.

The parameters are not a formality, and the projection is not policy-independent.
At least three make a material difference:

- **Admission quorum.** How many endorsements a proposal needs before its finalization counts.
   Two devices holding identical evidence and different quorums reconstruct different membership.
- **Expiry treatment.** Whether a proposal finalized after its stated expiry counts.
- **Activation selection.** Which of several concurrent events this device has selected into its active view.
   The Constitution protocol deliberately preserves concurrency rather than picking a winner, so this is a local choice every time.

**Status: Contradicted.**
The rules exist: `trusted_device_keys_by_teammate` resolves device keys, `_admission_status` (`provisioning.py`) computes proposal status from which records exist, and `_constitution_snapshot` (`provisioning.py`) assembles teammates, devices, and berth roles.
Two inputs are not evidence-backed.
Quorum comes from `team_setting`, an unsigned key-value table, so the fetched snapshot supplies a value that must not silently become the newcomer's local policy.
`_valid_finalization_exists` checks a finalization's signature and bindings but deliberately does not recount quorum against the current mutable threshold.
It is not a historical replay under independently selected parameters.
Berths have no signed origin at all: `_ensure_team_app_activation` (`provisioning.py`) inserts fresh `app` and `team_app_berth` rows with no record behind them, and signed records then refer to `berth_id` values that no signature establishes.
Berth origins need signed evidence.
Policy parameters need explicit provenance: the operator may choose them locally, or the selected extension may interpret signed policy events.
If reconstruction claims that a threshold was in force at a particular historical point, it needs evidence for that claim; merely signing a policy value does not make it binding on every device.

## B6 — Compare the reconstruction against the delivered snapshot

**Inputs.** The reconstructed state from B5 and the snapshot's own projection tables.
**Provenance.** One computed locally, one delivered.
**Check.** Compare semantic state, not file bytes.
The snapshot's SQLite pages, row order, and indexes are irrelevant; its claimed membership, device keys, berths, and roles are the subject.
**Policy owner.** The framework produces the comparison; the operator decides what to do with a difference.
**Evidence recorded.** The first divergence, in full, with both sides preserved.
**Default conclusion.** One of four, and only one of them is a failure:

- **Agrees.** The snapshot asserts exactly what the evidence supports under the named parameters.
   Proceed.
- **Differs under policy.** The states differ only in ways a named parameter explains.
   Record both and the parameter responsible; this is a disagreement about policy, not about facts.
- **Differs on evidence.** The snapshot asserts authority the records do not support.
   Stop and show the first divergent field.
- **Incomplete.** Required ancestry is missing, so part of the state cannot be reconstructed either way.
   Pause, preserve what arrived, and name what is absent.

**Override.** A human may accept a snapshot that differs under policy, or accept an incomplete view for inspection.
A human may not turn "differs on evidence" into agreement; they may only decide to proceed anyway, which records an unproved view rather than a verified one.
**Not concluded.** That agreement makes the snapshot true.
Agreement means the introducer's claims and the introducer's evidence are consistent.
An introducer who controls both can make them consistent.

**Status: Proposed.**
No code compares a reconstruction against a delivered snapshot, because no code reconstructs.

## B7 — Decide whether to adopt this as the active view

**Inputs.** The verified frontier and the comparison result.
**Provenance.** B3 through B6.
**Check.** None.
This is a decision, not a check.
**Policy owner.** The device's operator.
**Evidence recorded.** Which view was adopted, on what evidence, and under which parameters.
**Default conclusion.** This device will treat this ancestry-closed view as its starting point.
**Not concluded.** That any other device agrees, or that this view is canonical.
A local ref is a local choice, like a Git branch.
Selecting this Constitution view does not yet authorize integration of its transported Git history.
That action still waits for B8's historical-authority and acceptance decision; B7 cannot bypass a later missing-evidence or removed-author pause.
Recognition for new work remains a separate B9 decision, so a removed author's lack of current authority does not prevent explicit acceptance of their old work.
**Override.** This step is the override; it is where a human decision legitimately enters.

**Status: Contradicted.**
`_adopt_source` refuses a source sharing no ancestry with local history, and `_require_note_to_self_tree` requires exactly one regular `core.db` blob.
Both are structural checks on shape, and neither consults a signature.
On a blank install `_adopt_into_unborn_repo` runs with no local history to compare against, so even the structural check has nothing to work with.

## B8 — Decide about historical authorship

**Inputs.** The commits and records the newcomer will rely on, and the reconstructed authority state at the time each was made.
**Provenance.** The adopted view.
**Check.** For each author whose work is relied upon, determine whether their key held the relevant authority when the work was made.
**Policy owner.** The admission extension establishes what authority existed; the operator decides what to do about gaps.
**Evidence recorded.** Which key signed what, and the authority state that key held at that point in the history.
**Default conclusion.** Authentic old signatures stay authentic.
A teammate's later removal does not retroactively unsign their commits, and it does not by itself invalidate work the team already built on.
**Override.** For a newcomer encountering an author removed before it joined, the default is to pause its own integration for local review with the evidence preserved, rather than to accept or discard silently.
A human may then accept the history.
That acceptance covers the past only, is not permanent, and grants no authority over future work.
Record the exact accepted commit set or head with its verified closure, the Constitution view, and the local decision.
Acceptance of that finite history must not become an unrestricted authorization for later commits by the same signing key.

Three distinctions carry the weight here.
The inviter's earlier acceptance of an author is a fact about the inviter, not an instruction to the newcomer.
The newcomer's acceptance of past work is separate from its willingness to accept new work by the same author, which is B9's question.
And a timestamp inside a record is signed metadata, not proof of when anyone acted; a malicious signer sets their own clock.
A reference to an old authority view does not prove creation before removal either: a signer can cite that view after it has lost authority.
The [finite-history probe](../Experiments/bootstrap_history/README.md) preserves this limit explicitly.
It authenticates small work statements and their named grants, but reports creation-before-removal as unproved and accepts removed-author work only through an exact local decision.
The actual binding between Git work and the Constitution basis used to judge it remains #266's responsibility.
Without that evidence, the newcomer must retain an unknown historical-authority result rather than interpreting a present allowed-signers set as proof about the past.

**Status: Proposed; evidence and runtime integration remain incomplete.**
No signed removal or exclusion record exists anywhere in the schema.
`admission_revocation` is a mutable projection of a proposal the inviter abandoned, not evidence that a teammate was removed.
So the removed-author case currently has no permanent evidence on either side, and [#263](https://github.com/benjaminy/small-sea-collective/issues/263) must supply it before this step can be enforced.

The retention boundary distinguishes commit objects from file contents.
`SshCommitVerifier.verify_history` (`packages/cod-sync/cod_sync/verify.py`) checks the head and every reachable commit object.
Cod Sync's [live-data window](../packages/cod-sync/README.md#history-compaction) retains those commit identities and their parent links while allowing old file contents to become unavailable.
A signed commit commits to a tree identifier; verifying its signature does not require reading every file in that tree.
The [retention probe](../Experiments/bootstrap_retention/README.md) runs the actual verifier: removing an old blob or tree leaves both commit signatures verifiable, while reading the old file fails.
Removing the parent commit makes verification unavailable; restoring it restores the valid control.

Thus content removal alone does not conflict with the current signature-verifier contract.
With all original commit objects and authorized signer evidence present, the newcomer can establish which keys signed those commits, even when some referenced contents cannot be inspected or restored.
It cannot infer what unavailable bytes contained, whether copied content originated with that signer, or whether those contents agreed with a historical projection.
If required Constitution evidence is missing, B4 through B6 pause the conclusions needing it even when every Git signature verifies.
If a required commit object is missing or the repository is shallow, the current verifier cannot establish complete commit ancestry; B8 does not call that history verified.
A local decision to inspect or accept an unproved view must preserve this missing-evidence result.

[#190](https://github.com/benjaminy/small-sea-collective/issues/190) retains responsibility for verifier and retention behavior; [#266](https://github.com/benjaminy/small-sea-collective/issues/266) must supply historical, berth-scoped authority to the caller.
The probe does not implement content pruning, bundle transport for partial histories, or Manager wiring.
Neither a fetched signer list nor acceptance of finite old history supplies the missing authorization for future commits.

## B9 — Recognize a signing key for a berth and a purpose

**Inputs.** A signing key, a team, a berth, and a purpose.
**Provenance.** The reconstructed authority state.
**Check.** The key's authority chain must cover this exact team, this exact berth, and this exact purpose.
A key authorized to sign for one berth is not thereby authorized for another, even when it belongs to the same physical device and its signature verifies.
**Policy owner.** The admission extension.
**Evidence recorded.** The chain, and the scope each link in it carries.
**Default conclusion.** This device recognizes the key for this berth and purpose under its selected Constitution view and named policy.
**Not concluded.** That the view is globally current, that no removal or conflicting grant exists elsewhere, or that another device recognizes the key.
An absent removal in an introducer-selected frontier does not prove that nobody removed the author.
B9 records the view and its known limits; it does not turn an incomplete view into a claim about the whole team.
Known missing or disputed evidence pauses the decisions that require it, under the local policy.
**Override.** None.
Scope is evidence, not preference.

**Status: Contradicted.**
`issue_membership_cert` and `issue_device_link_cert` (`packages/wrasse-trust/wrasse_trust/identity.py` and `:240`) bind `team_id` and `teammate_id` and nothing else.
There is no berth in a certificate and no purpose, so today a key trusted anywhere in a team is trusted everywhere in it.
`packages/wrasse-trust/README.md` still describes the device-only, per-team direction.
Supplying the missing scope is [#266](https://github.com/benjaminy/small-sea-collective/issues/266)'s work; this transcript's contribution is to state exactly which check has nothing to check against.

## B10 — Release future encryption keys

**Inputs.** A recognized key from B9, and its current prekey material.
**Provenance.** The reconstructed authority state, for the key; the team database, for the prekey material.
**Check.** The releasing device's Manager must recognize the recipient for this berth and key-distribution purpose under its own currently selected evidence and explicit local release policy.
The newcomer's reconstructed bootstrap view cannot instruct the sibling to release keys.
Known missing or disputed authority evidence pauses the release; B8's finite history acceptance and an identity-recognition decision are insufficient.
The prekey material must also be bound to that recognized key rather than merely filed under it.
**Policy owner.** The admission extension, under the strictest policy in this document.
**Evidence recorded.** Which key material went to which recognized key, the releasing device's selected Constitution view and policy, and its local release decision.
**Default conclusion.** This recipient can read work encrypted with the released key material.
The releasing device may still be unaware of a removal or compromise; the protocol does not prove global freshness or eliminate that local trust risk.
**Override.** A human may refuse.
A human should not be able to grant this without a recognized recipient, because the consequence is unrecoverable: key material, once sent, cannot be unsent.

The Team Constitution states the principle directly: an extension that releases fresh key material needs a stronger and more explicit authorization policy than one that merely displays an unfamiliar signed event.
This is also where the past-versus-future line from B8 becomes operational.
Accepting a removed teammate's old commits is a statement about history; sending them a new sender key is a statement about the future, and B10 must never inherit its answer from B8.

**Status: Partly implemented; berth-scoped authority remains unenforced.**
`redistribute_sender_key` selects a device key from the current certificate graph.
`_load_device_prekey_bundle` verifies a versioned, domain-separated signature under that selected key over the team id, target device id, and complete X3DH bundle before encryption.
The team-device and X3DH identity signing keys remain distinct; the outer signature binds them without requiring equality.
Unsigned or substituted rows are rejected.
The implementation and decrypting valid control are exercised in [the prekey-binding micro tests](../Experiments/autonomous_bootstrap/prekey_binding/test_prekey_observations.py).
`create_linked_device_bootstrap` separately takes its bundle from a signature-checked join request.

This binding does not establish B9's missing berth scope or repair the certificate graph's bootstrap root.
It also does not prove freshness: an older authentic bundle for the same team and device can still pass.
Shared one-time-prekey selection and local receipt persistence are separate concerns described in [the consumption experiment](../Experiments/autonomous_bootstrap/prekey_consumption/README.md).

## Entry path: invitation

A person who is not yet a teammate joins a team.

This path uses an offer followed by a request and a final response.
The inviter cannot bind a commitment to a newcomer key that does not yet exist.

1. **Offer.** The inviter conveys the intended team and a way to continue the exchange through a channel the invitee already trusts.
   The offer may name an invitation or proposal, but it does not complete B1, admit a device, authorize active history adoption, or release future encryption keys.
2. **Request.** The invitee generates its fresh keys and sends a request identifying that offer and the operation scope.
   The request includes an attempt identifier so the final response can be checked against this attempt rather than another pending join.
3. **Final response.** After receiving the request, the inviter selects the snapshot, frontier and explicit authority anchor it is offering.
   The final B1 commitment covers the offer reference, exact request, both parties' keys, operation scope, anchor, frontier and snapshot digest.
   Both sides authenticate that same completed exchange through their trusted channel and record the comparison before treating the request as belonging to the intended person.
4. **Delivery and decisions.** The invitee retrieves the committed snapshot and runs B2 through B9.
   The inviter separately evaluates admission and device enrollment under its current local policy before B10 can release keys.
   A matched exchange is evidence of what the parties agreed to discuss, not proof that admission or key distribution is authorized.
   The inviter records the attempt and final commitment and applies its admission policy to expired offers, repeated requests and already completed attempts.
   Replaying an offer or response does not by itself renew authority or trigger another key release; any new release needs a current local decision.

These messages can be exchanged asynchronously; the two humans need not be present at the same time.
This adds a return exchange before bootstrap completes.
That cost is deliberate: an unsolicited offer alone cannot authenticate a fresh key chosen later.
An alternative would authenticate a frozen snapshot in the offer and bind the device in a second ceremony, but it would need two separately tracked trust decisions and retention of the earlier snapshot.
This transcript chooses the request-first final commitment to keep one complete bootstrap commitment.

The invitee starts from the snapshot selected in the final response, not whatever head the store publishes when fetching begins.
Later changes arrive through ordinary sync and receive their own checks and local acceptance decisions.
If the pinned snapshot is unavailable, this attempt pauses; the parties can authenticate a new response instead of silently substituting newer bytes.

The authority anchor is explicit in the exchange and interpreted by the selected extension.
If that extension pins genesis authority, the inviter's key must chain to that anchor.
Choosing a different anchor is an explicit local trust decision, not a core-verification result or a root inferred from the fetched roster.

**Trusted to assert.** That this team is the team the invitee's human meant to join, and that this frontier is the team's history as the inviter sees it.
**Not trusted to assert.** That the view is complete, that no teammate was concealed, and that no concurrent branch exists.
The Constitution protocol cannot prove that any peer disclosed everything it knew, and no anchor changes that.
**Residual risk.** A malicious but correctly authenticated inviter can show the invitee a real, well-rooted, internally consistent history that omits events, and the invitee cannot detect the omission from the evidence alone.
The bootstrap design does not solve this; it confines it to a party the invitee's human chose and can hold responsible.

**Status: Contradicted.**
`accept_invitation` (`provisioning.py`) checks only that the token's `invitee_teammate_id` matches the teammate id it was given — a consistency check on the token against itself.
It does not check that the cloned history contains the proposal, that the proposal's signer is the named inviter, or that the head relates to anything in the token.
Whoever controls the URL in the token controls what the invitee accepts as team history.
The inviter's side is protected: `complete_invitation_acceptance` (`provisioning.py`) verifies the acceptance signature, nonce, team id, and invitee id against its own proposal row.
The protection runs in exactly one direction, and it is the wrong one for #262.

## Entry path: sibling device

A person who is already a teammate adds another of their own devices.
This path runs in two stages, and conflating them is why the current implementation has a hole in the middle.

**Stage 1: identity join.** The operator selects the person whose device set the newcomer is to join and independently authenticates the complete exchange with the introducing sibling under B1.
The request binds the fresh newcomer keys and identity-operation scope; the response binds the exact NoteToSelf snapshot, selected authority evidence and introducing key.
The compared sibling key identifies the introducer; it does not automatically become a permanent identifier for the person or the anchor for every authority claim.

The selected identity policy combines retained delegation with explicit local recognition.
It follows the [identity comparison](../Experiments/bootstrap_identity/README.md), which preserves both candidates and their failure cases.

- **When delegation survives:** the operator explicitly adopts the authority anchor carried by the authenticated exchange.
  Manager checks the retained signed path from that anchor to the sibling, including the authority to introduce this fresh key under the named identity extension.
  Losing the first device's private key does not invalidate a path whose public anchor and signed evidence survive.
  Each successor acts only within the powers the selected extension recognizes; a valid signature alone cannot enlarge that scope.
  Record the chain and its verification result separately from the operator's adoption of the anchor.
- **When evidence is missing or disputed:** pause the identity conclusions that depend on it, retain available records and any competing claims, and show what is missing.
  The operator may make a new, scoped local decision recognizing this claimant as a continuation of the named identity and authorizing this compared sibling to introduce the fresh device.
  Record the actor, prior identity/key references, exact exchange commitment, fresh key, scope, recognition basis or independent endorsements, missing evidence and competing claims.
  This is a new recognition decision; it does not repair the chain, prove that an earlier key belonged to the same person, or turn an unproved comparison into a match.
  New evidence may lead the operator to revise the recognition later.

Under either route, retain a signed device-introduction statement binding the fresh keys to this exchange and the authority or local recognition decision used.
The local decision must remain distinguishable from signed historical delegation when synchronized or shown to another device.
The introducer's signature authenticates its statement; local storage of the operator's decision records what this device chose.
Neither supplies cryptographic proof that a human was present or honest.
No globally authoritative person identifier is introduced by this policy.

An authenticated compromised sibling can satisfy the delegation route.
The probe gives the same signed input to a legitimate claimant and a thief holding the same key; their cryptographic verdicts are identical.
Human recognition or an independent endorsement can provide additional grounds for a present decision, but signatures cannot recover that missing distinction.
If the operator cannot or will not make that decision, this device's affected identity use remains paused; other devices need not stop.
Concurrent incompatible successor claims remain visible rather than being resolved by arrival order.
A new local recognition can select a provisional path without deleting the rival evidence or requiring other participants to agree.

These conclusions stay separate:

1. A particular key signed a particular delegation or introduction.
2. This device recognizes a successor as belonging to the same enduring identity.
3. A team recognizes that successor for a specified teammate, berth and purpose.

Stage 1 reaches at most the first two.
Another device may refuse the recognition or require more evidence, and stage 2 must establish team authority separately.
If every enrolled team key is lost and no prepared recovery capability survives, this identity decision does not bypass the Manager's [tier-two team recovery](../packages/small-sea-manager/spec.md#prepared-recovery--target-flow): fresh team admission uses a new team-local teammate UUID.
That team-local change does not force the person to deny continuity of their broader identity.

**Status: Proposed identity policy; signed evidence and local-decision storage are not implemented.**
NoteToSelf's `shared_schema.sql` stores `user_device`, `team`, `team_device_key`, `cloud_storage` and `berth_cloud_allocation` as unsigned rows.
Binding those bytes through B2 authenticates delivery, but the rows do not implement either the signed delegation route or the explicit recognition record above.
The current welcome bundle signs the introduction payload without this authority policy or its durable evidence.
The experiment uses a small linear delegation model and a full-digest comparison, not the intended event DAG, a general recovery system or the final wire ceremony.

**Stage 2: team join.** The operator selects one team and an already recognized sibling that participates in it.
The new device starts with an empty team store and generates a fresh team-device key.
An unsigned NoteToSelf team row may help display candidates; it supplies neither team authority nor a trusted baseline.

The selected delivery rule is an explicit per-team request and signed response through the identity-device relationship established in stage 1.
The [delivery comparison](../Experiments/bootstrap_team_delivery/README.md) also retains a fresh per-team human comparison as an alternative when the operator wants an independent ceremony or no currently accepted identity binding is available.
Both routes bind the same evidence and make the same team-authority checks.
The signed route avoids another human comparison but inherits the locally recognized identity key's compromise risk; it does not provide an additional independent check of the person.
If stage 1 used explicit local recognition because historical delegation was absent, retain that recognition decision and its missing-proof fields in the team bootstrap evidence.
The signed team response authenticates delivery under that locally recognized key; it cannot upgrade the historical continuity claim.
Stage 1 still requires its B1 comparison unless the operator explicitly accepts an unproved view under the recorded override policy.
A fresh per-team comparison is available, but repeating a comparison with the same compromised claimant does not itself establish historical continuity.

1. **Request.** Manager records the selected team, teammate, attempt, fresh team signing and encryption keys, and requested enrollment scope locally.
   The request names the recognized sibling identity key and the identity-recognition decision under which it is contacted.
   The newcomer signs the complete request with its identity-device key and proves possession of its fresh team-device signing key.
   The sibling checks the requester against its own locally recognized identity-device evidence, not a key embedded in the request or a newly fetched device table.
2. **Response.** The sibling selects the exact team snapshot and Constitution frontier, the authority anchor and named policy, and the evidence under which its team-device key may enroll the new device.
   It signs the entire request and those commitments with its recognized identity-device key.
   An explicit signed binding relates that identity key to its team-device key, team and teammate for this attempt.
   Its team-device key separately signs the scoped enrollment statement for the newcomer's fresh key.
   The response may carry that evidence or name its committed snapshot location; neither location nor successful decryption is an authority source.
3. **Retain before fetch.** The newcomer checks the response against its exact locally recorded request and the sibling identity key it already recognizes.
   It retains the request, signed response, identity-recognition basis including missing proof, anchor-selection decision and authentication result before asking the Hub to retrieve the selected snapshot.
   That retained response supplies B1's delivery binding on this route.
   The alternative route authenticates the same complete response through a fresh human comparison and records that comparison instead.
4. **Verify from empty state.** Manager explicitly adopts the offered anchor and policy as the local starting authority inputs, then runs B2 through B6 over the delivered evidence.
   It verifies the identity-to-team binding and the sibling team's authority to issue this enrollment under that anchor, team, teammate and scope.
   The identity signature proves who delivered the binding; it cannot grant team enrollment authority.
   The enrollment statement must cover the exact fresh key and request, and its signer must have the required enrollment power under the selected team policy.
   B7 through B9 govern view adoption, old history and current berth authority separately.
5. **Release and later updates.** The sibling independently evaluates current team policy before B10 releases any future key material.
   The new device enrolls only in the selected team; repeat this flow for another team.
   A missing pinned snapshot pauses the recorded attempt; retry those bytes or authenticate a new complete response.
   Do not replace the pinned view with the current store head, transfer all team secrets during identity join, or infer future authority from a recorded past enrollment.

The initial authority source is the operator's explicit adoption of the anchor delivered by a recognized sibling, followed by the independently checked team evidence rooted there.
The fetched graph cannot select its own anchor.
An authenticated malicious sibling can still propose a dishonest anchor or omit events; the operator's introduction decision bears that risk.
A locally accepted identity successor receives no team authority from that identity decision alone.
If no sibling has the required team evidence, pause this team's enrollment and use the separately specified team recovery or admission process.

**Status: Proposed delivery design; current runtime still requires a supplied baseline.**
`finalize_linked_device_bootstrap` (`provisioning.py`) checks the authorizer's team-device key against `get_trusted_device_keys_for_teammate`, read from the joiner's local team database.
The micro tests prepare that database with `_copy_team_baseline`; no runtime path implements the evidence delivery above.
The model begins with an empty store and exercises both delivery routes, but its single-anchor enrollment grants simplify Constitution ancestry and team policy.
It does not implement encrypted transport, short-string protocol security, or runtime provisioning.

**Trusted to assert.** That this device belongs to this person, and — in stage 2 — that this person holds the teammate identity the certificate names.
**Not trusted to assert.** Which teams the new device should join.
That is a separate decision per team, and a person may deliberately keep a device out of some of their teams.

## Whose work pauses

Every branch in this transcript that cannot proceed pauses something specific.
Naming what stays permitted matters as much as naming what stops, because a pause that blocks everything is indistinguishable from a failure.

| Situation | What pauses | What remains permitted | Who can resolve it |
| --- | --- | --- | --- |
| No accepted B1 authentication method | Identity use, team joins, integration of fetched work, key distribution | Inspecting the fetched data locally | The newcomer's human, by comparing, or by accepting an unproved view explicitly |
| B2 digest mismatch | Everything on this attempt | Retrying delivery | Nobody; the bytes are wrong, so fetch again |
| B4 chain does not reach the root | Everything derived from that chain | Inspection; other chains that do reach the root | Nobody by decision — this is a failed binding, not a policy question |
| B6 differs on evidence | Adoption of the view | Inspection, preserving both states | Nobody by decision; a human may proceed with an unproved view, recorded as such |
| B6 differs under policy | Nothing automatically | Everything, under the recorded parameters | The operator, by choosing parameters |
| B6 incomplete ancestry | Conclusions needing the missing closure | Everything not depending on it | Whoever can supply the missing events |
| Known missing or disputed current authority evidence | This device's dependent B9 recognition and B10 key release | Inspection, unrelated scopes, preserving alternative views | The local operator or an authorized policy actor, using additional evidence or a separately recorded decision; no decision proves global completeness |
| B8 removed author in relied-upon history | This newcomer's integration of that history | Inspection; unrelated berths; the rest of the team continues unaffected | The newcomer's human, for the past only |

A person's own device may stay paused indefinitely if its human chooses not to resolve the pause.
That harms only that person, and it is a better outcome than a device guessing.

## What this transcript requires the code to change

These are consequences of the design above, not a review of unrelated defects.

1. **Root the certificate graph.** `trusted_device_keys_by_teammate` must take the root as a caller-supplied parameter and reject self-issued certificates that do not match it (B4).
2. **Widen and record the bootstrap commitment.** Construct it after the newcomer request exists; cover scope, the offer and attempt, both parties' keys, the root, the frontier, and the snapshot digest, in both directions, and store the comparison result with the trust view (B1).
3. **Carry and verify the final response commitment.** The early offer is not this commitment; `accept_invitation` must check the snapshot against the authenticated final response before adopting anything (B1, B2).
4. **Record policy provenance.** Select quorum explicitly under local policy; historical claims about a threshold require evidence interpreted by the selected extension (B5).
   Do not adopt the unsigned fetched `team_setting` value as the newcomer's policy implicitly.
5. **Give berths a signed origin.** Signed records currently name `berth_id` values that no signature establishes (B5).
6. **Add a signed removal record.** Until one exists, B8 has no evidence on either side ([#263](https://github.com/benjaminy/small-sea-collective/issues/263)).
7. **Scope certificates to berth and purpose** ([#266](https://github.com/benjaminy/small-sea-collective/issues/266)) (B9).
8. **Bind prekey bundles to the device key they are filed under** (B10).
   Implemented locally with a signed wrapper; freshness and B9 authority remain separate gaps.
9. **Give NoteToSelf signed device-authority records and separate local continuity decisions** (sibling path, stage 1).
   Preserve the authentication result and missing historical proof; carry no implicit team authority.
   Implement the stage-2 per-team evidence exchange before treating a fetched baseline as trusted.
10. **Implement retention while preserving the required commit and Constitution evidence** ([#190](https://github.com/benjaminy/small-sea-collective/issues/190), [#266](https://github.com/benjaminy/small-sea-collective/issues/266)) (B8).
11. **Decide whether Manager repositories sign commits.** `Repo.configure_signing` (`packages/cod-sync/cod_sync/repo.py`) has no call site outside cod-sync's own tests, so every Manager commit today is unsigned and Git contributes no authorship evidence at all.

## Where this leaves the six questions

| Question | Answer |
| --- | --- |
| What is authenticated independently first? | The complete invitation or identity exchange, in B1, through the human channel. A later per-team sibling exchange uses the resulting identity binding or a fresh comparison. |
| Adopted trust, validated anchor, or both? | Both, and they are different steps. B1 and B4 anchor the history to a root the human authenticated; B7 is the local decision to adopt a particular view. The anchor limits what a dishonest introducer can substitute; it does not make the introducer honest. |
| What binds the ceremony to the exact history? | The frontier and snapshot digest inside the B1 commitment, checked at B2 and B4. The sibling delivery sequence specifies that binding; the current runtime still lacks it. |
| How is a key recognized for a berth and purpose? | B9, which today has no scope to check against because certificates carry only team and teammate. |
| What about an author removed before joining? | B8. Old signatures stay authentic; the newcomer pauses its own integration for human review; acceptance covers the past, is revisitable, and grants nothing about the future. |
| What happens when evidence is missing or contradictory? | B6's four outcomes and the pause table above. An authenticated delivery can still differ on evidence; preserve that content discrepancy separately from authentication and local acceptance. |

## Selected rules and remaining implementation gaps

Both entry paths now name their first authority input and evidence-delivery sequence.
Invitation uses an independently compared complete exchange and an explicitly adopted team anchor.
Sibling identity join uses retained signed delegation where available and permits a separately recorded local continuity decision when it is not.
Each selected team then uses an authenticated per-team request and response, with team authority checked separately from identity recognition.
The comparisons preserve the alternatives and show why none establishes an honest or globally complete view.

The remaining gaps are implementation and protocol work, not permission to infer authority from a fetched roster:

- Define and implement signed identity delegation/introduction records and local-recognition storage, including review of conflicting claims.
- Implement per-team delivery from an empty store, persistence before fetch, complete request binding and the separate team-enrollment authority check.
- Supply permanent authority and removal evidence, berth/purpose scope, and historical policy evaluation for B4 through B10 (#263 and #266).
- Review the complete wire ceremony and short-string security; the experiments use full digests.
- Implement and validate retention transport without dropping required commit ancestry or Constitution evidence (#190); absent historical file contents remain unavailable for inspection even when commit signatures verify.

These models justify a research design choice, not a production security claim.
The acceptable evidence for a disputed human continuity claim remains an explicit local decision; this transcript imposes no universal takeover-risk threshold.

## Invitation ordering checks

These are design walkthroughs, not claims that the current invitation code implements the sequence.

| Case | Expected conclusion |
| --- | --- |
| Valid offer, request and authenticated final response | Both sides bind the same fresh device keys and snapshot; admission and local adoption still need their separate decisions. |
| Offer arrives before installation | No device key is assumed; the offer can wait without completing B1 or granting authority. |
| Relay replaces the request key | The newcomer rejects a final response naming a different request; a relay presenting different exchanges to the two sides is detected by the human comparison. The inviter alone cannot infer the newcomer's original request. |
| Response belongs to another attempt | The offer/attempt and exact-request binding fails even if the signature and snapshot digest are individually valid. |
| Store advances after the final response | Fetch the pinned snapshot; treat later events separately. A current head alone cannot satisfy B2. |
| Inviter changes its local admission policy after responding | The response still authenticates the offered evidence; it does not force the inviter to admit the device or release keys. |
| Pinned snapshot is missing | Pause this attempt or authenticate a new complete response; do not substitute a history by arrival order. |

The remaining B1 wire-format and comparison-strength questions still need protocol review.
This ordering correction does not implement the exchange or prove its short-authentication-string security.
