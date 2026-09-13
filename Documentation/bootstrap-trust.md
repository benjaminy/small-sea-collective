# The bootstrap trust transcript

Status: draft under review on branch `issue-262-bootstrap-transcript` ([#262](https://github.com/benjaminy/small-sea-collective/issues/262)).
It describes the intended design.
Several steps contradict what the code does today; those say so and name the change required.

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
- **Specified but unenforced** — a repository document states the rule and no code checks it.
- **Proposed** — this transcript is where the rule is first stated.
- **Contradicted** — current code does something incompatible, and the required change is named.

## B1 — Authenticate the exchange outside the system

**Inputs.** The newcomer's joining request and the introducer's response.
**Provenance.** Both are constructed locally by their own side; neither is trusted yet.
**Check.** The two humans compare one short string, derived from the whole exchange, over a channel they already trust.

The compared value — call it the bootstrap commitment — must cover five things, in both directions:

1. the operation scope: which identity or which team, and which entry path;
2. both parties' long-term public keys, including the newcomer's fresh device signing key;
3. the trust root, meaning the team's technical origin and the genesis authority key that B4 will check against;
4. the frontier: the exact set of Constitution event identifiers being offered;
5. the digest of the snapshot bytes that will be delivered.

Covering both directions matters.
A commitment over only the introducer's response lets a relay substitute the newcomer's half, so the introducer authorizes a device its human never saw.

**Policy owner.** The two humans, for the comparison itself; local policy, for what happens when no comparison is recorded.
**Evidence recorded.** That a comparison was performed, that it matched, and the commitment value, stored alongside the resulting trust view.
**Default conclusion.** The introducer named in the exchange is the party the newcomer's human meant to reach, and it has committed itself to exactly this root, this frontier, and these snapshot bytes.
**Not concluded.** Nothing about whether the introducer is honest, competent, or in possession of a complete view of the team.
B1 authenticates who is speaking, not what they say.
**Override.** A human may proceed with no recorded comparison.
The device then holds an unauthenticated view: it may inspect the data, and ordinary integration, identity use, team joins, and key distribution stay paused until a human accepts the view explicitly.
That acceptance is a local policy decision and leaves the authentication result exactly as it was — unproved.

**Status: Contradicted.**
`welcome_bundle_confirmation_string` (`packages/small-sea-note-to-self/small_sea_note_to_self/bootstrap.py:150`) covers the join-request artifact, the welcome bundle, and its signature.
It does not cover a trust root, a frontier, or a snapshot digest, so it authenticates who sent the bundle but not what history that bundle points at.
Nothing stores the result: a repository-wide search finds no consumer of the string outside `provisioning.py` and `bootstrap.py`, where it is returned and displayed.
On the invitation path there is no comparison at all; `create_invitation` (`packages/small-sea-manager/small_sea_manager/provisioning.py:5272`) hands out an unsigned base64 token.
Both need to change.

One open wire-format question belongs here.
The current string is 64 bits.
A relay that shapes both halves of an exchange can search for two exchanges sharing a string, which costs about half the bits, so the practical margin is nearer 2^32 than 2^64.
Whether that margin is acceptable is a threat-model question this document does not settle; it should be reviewed against short-authentication-string practice in comparable protocols before the format is frozen.

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
`accept_invitation` (`provisioning.py:5421`) clones from the URL in the token and checks out whatever head the store publishes.
It verifies no digest, because the token commits to none.
`bootstrap_existing_identity` (`provisioning.py:2524`) fetches from the descriptor inside the welcome bundle with the same gap.

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
First, the frontier present in the snapshot is exactly the event set the commitment named — no more, no fewer.
Second, every authority chain the newcomer intends to rely on terminates at the root the commitment carried, through ancestry that is present and core-verified.
**Policy owner.** The framework.
This is a protocol rule, not an application choice.
**Evidence recorded.** The root, the frontier, and the chain from each relied-upon key back to the root.
**Default conclusion.** The authority this snapshot describes descends from the root the newcomer's human authenticated.
**Not concluded.** That the described authority is correct or current.
B4 establishes that the graph is the right graph, not that its contents are honest.
**Override.** None.
A chain that does not reach the root is not a weaker chain; it is a chain to somewhere else.

This is the step that makes bootstrapping possible, and the step current code lacks entirely.

**Status: Contradicted.**
`trusted_device_keys_by_teammate` (`packages/wrasse-trust/wrasse_trust/identity.py:323`) seeds trust from any self-issued membership certificate:

```
if cert.issuer_participant_id == admitted_teammate_id:
    issuer_keys = [cert.subject_public_key]
```

It accepts as many roots as the snapshot happens to contain, and never asks which one is this team's genesis.
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
The rules exist: `trusted_device_keys_by_teammate` resolves device keys, `_admission_status` (`provisioning.py:1353`) computes proposal status from which records exist, and `_constitution_snapshot` (`provisioning.py:4594`) assembles teammates, devices, and berth roles.
Two inputs are not evidence-backed.
Quorum comes from `team_setting`, an unsigned key-value table, so whoever wrote the snapshot also chose the threshold its own finalizations had to clear.
Berths have no signed origin at all: `_ensure_team_app_activation` (`provisioning.py:5187`) inserts fresh `app` and `team_app_berth` rows with no record behind them, and signed records then refer to `berth_id` values that no signature establishes.
Both need signed records before reconstruction can mean anything.

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

Three distinctions carry the weight here.
The inviter's earlier acceptance of an author is a fact about the inviter, not an instruction to the newcomer.
The newcomer's acceptance of past work is separate from its willingness to accept new work by the same author, which is B9's question.
And a timestamp inside a record is signed metadata, not proof of when anyone acted; a malicious signer sets their own clock.

**Status: Proposed, with a known conflict.**
No signed removal or exclusion record exists anywhere in the schema.
`admission_revocation` is a mutable projection of a proposal the inviter abandoned, not evidence that a teammate was removed.
So the removed-author case currently has no permanent evidence on either side, and [#263](https://github.com/benjaminy/small-sea-collective/issues/263) must supply it before this step can be enforced.

Separately, `SshCommitVerifier.verify_history` (`packages/cod-sync/cod_sync/verify.py:87`) checks the head and every commit it reaches, raising on the first commit it does not accept.
That full-ancestry contract conflicts with a retention model that discards old Git contents: a device that pruned history cannot satisfy a verifier that demands all of it.
Reconciling the two is a prerequisite for [#266](https://github.com/benjaminy/small-sea-collective/issues/266), not something this transcript resolves.
Whatever replaces it must not silently drop required ancestors, and must not solve the problem by adding every fetched key to the verifier's allowed set.

## B9 — Recognize a signing key for a berth and a purpose

**Inputs.** A signing key, a team, a berth, and a purpose.
**Provenance.** The reconstructed authority state.
**Check.** The key's authority chain must cover this exact team, this exact berth, and this exact purpose.
A key authorized to sign for one berth is not thereby authorized for another, even when it belongs to the same physical device and its signature verifies.
**Policy owner.** The admission extension.
**Evidence recorded.** The chain, and the scope each link in it carries.
**Default conclusion.** This key may sign for this berth, for this purpose, now.
**Not concluded.** That it may sign elsewhere, or that it still may tomorrow.
**Override.** None.
Scope is evidence, not preference.

**Status: Contradicted.**
`issue_membership_cert` and `issue_device_link_cert` (`packages/wrasse-trust/wrasse_trust/identity.py:220` and `:240`) bind `team_id` and `teammate_id` and nothing else.
There is no berth in a certificate and no purpose, so today a key trusted anywhere in a team is trusted everywhere in it.
`packages/wrasse-trust/README.md` still describes the device-only, per-team direction.
Supplying the missing scope is [#266](https://github.com/benjaminy/small-sea-collective/issues/266)'s work; this transcript's contribution is to state exactly which check has nothing to check against.

## B10 — Release future encryption keys

**Inputs.** A recognized key from B9, and its current prekey material.
**Provenance.** The reconstructed authority state, for the key; the team database, for the prekey material.
**Check.** The recipient must be recognized under B9 for the berth whose keys are being released, and the prekey material must be bound to that recognized key rather than merely filed under it.
**Policy owner.** The admission extension, under the strictest policy in this document.
**Evidence recorded.** Which key material went to which recognized key, and on what basis.
**Default conclusion.** This recipient will be able to read work encrypted from now on.
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

B1's commitment is created when the inviter creates the invitation, and it freezes the offered view at that moment.
The invitee therefore bootstraps at the exact view the inviter committed to, not at whatever head the store publishes when the invitee gets around to fetching.
Team changes made after the invitation was issued arrive afterwards, through ordinary sync, and face B8 and B9 like any other incoming work.

This is what makes an asynchronous invitation workable.
The two humans do not have to be present at the same moment: the inviter fixes the commitment, conveys it through a channel the invitee already trusts, and the invitee compares it whenever they fetch.
What the two humans must not do is skip the comparison because the fetch succeeded.

The root in B4 is the team's genesis authority, carried in the commitment.
The inviter's own key is not the root; it is a key whose authority the invitee checks by chaining back to the root like any other.

**Trusted to assert.** That this team is the team the invitee's human meant to join, and that this frontier is the team's history as the inviter sees it.
**Not trusted to assert.** That the view is complete, that no teammate was concealed, and that no concurrent branch exists.
The Constitution protocol cannot prove that any peer disclosed everything it knew, and no anchor changes that.
**Residual risk.** A malicious but correctly authenticated inviter can show the invitee a real, well-rooted, internally consistent history that omits events, and the invitee cannot detect the omission from the evidence alone.
The bootstrap design does not solve this; it confines it to a party the invitee's human chose and can hold responsible.

**Status: Contradicted.**
`accept_invitation` (`provisioning.py:5421`) checks only that the token's `invitee_teammate_id` matches the teammate id it was given — a consistency check on the token against itself.
It does not check that the cloned history contains the proposal, that the proposal's signer is the named inviter, or that the head relates to anything in the token.
Whoever controls the URL in the token controls what the invitee accepts as team history.
The inviter's side is protected: `complete_invitation_acceptance` (`provisioning.py:5716`) verifies the acceptance signature, nonce, team id, and invitee id against its own proposal row.
The protection runs in exactly one direction, and it is the wrong one for #262.

## Entry path: sibling device

A person who is already a teammate adds another of their own devices.
This path runs in two stages, and conflating them is why the current implementation has a hole in the middle.

**Stage 1: identity join.** The new device and an existing device of the same person run B1, and the new device receives the person's NoteToSelf database.
The root here is the authorizing device's signing key, pinned by the human comparison.

Stage 1 has a structural problem that no amount of care in the ceremony fixes.
`shared_schema.sql` has no signature column in any table: `user_device`, `team`, `team_device_key`, `cloud_storage`, and `berth_cloud_allocation` are all unsigned rows.
B4 needs authority chains that terminate at the root, and the identity snapshot contains no chains at all.
Its bytes can be bound to the exchange by digest, and that is the whole of what B2 through B6 can do with it.

The required change is to give NoteToSelf signed device-authority records, rooted at the person's first device key and pinned by B1's commitment, so that a newly joined device's authority is evidence rather than an assertion in a row.
The alternative — treating NoteToSelf as an authority-free convenience cache and re-deriving everything from team Constitution evidence — is worse, because the person's device set is exactly the thing no team can establish for them.

**Stage 2: team join.** The new device joins one of the person's teams, generating a fresh team-device key and receiving a device-link certificate from a sibling.

`finalize_linked_device_bootstrap` (`provisioning.py:2839`) checks the authorizer's team-device key against `get_trusted_device_keys_for_teammate`, read from the joiner's *local* team database.
Where that local team database comes from is unresolved.
Every micro test in `test_linked_device_bootstrap.py` supplies it with `_copy_team_baseline`, which copies the authorizer's team folder and inserts the team row by hand.
No production code path clones a team for a linked device.
So the sibling path's trust in the authorizer rests entirely on a baseline that only a test fixture provides, and authenticating that baseline is precisely the question #262 exists to answer.

The identity-join gap and the baseline gap are the same shape twice over: the fetched database is the only source of the key used to check the thing that pointed at the fetched database.
B1 and B4 together are what break the circle, on both paths.

**Trusted to assert.** That this device belongs to this person, and — in stage 2 — that this person holds the teammate identity the certificate names.
**Not trusted to assert.** Which teams the new device should join.
That is a separate decision per team, and a person may deliberately keep a device out of some of their teams.

## Whose work pauses

Every branch in this transcript that cannot proceed pauses something specific.
Naming what stays permitted matters as much as naming what stops, because a pause that blocks everything is indistinguishable from a failure.

| Situation | What pauses | What remains permitted | Who can resolve it |
| --- | --- | --- | --- |
| No recorded B1 comparison | Identity use, team joins, integration of fetched work, key distribution | Inspecting the fetched data locally | The newcomer's human, by comparing, or by accepting an unproved view explicitly |
| B2 digest mismatch | Everything on this attempt | Retrying delivery | Nobody; the bytes are wrong, so fetch again |
| B4 chain does not reach the root | Everything derived from that chain | Inspection; other chains that do reach the root | Nobody by decision — this is a failed binding, not a policy question |
| B6 differs on evidence | Adoption of the view | Inspection, preserving both states | Nobody by decision; a human may proceed with an unproved view, recorded as such |
| B6 differs under policy | Nothing automatically | Everything, under the recorded parameters | The operator, by choosing parameters |
| B6 incomplete ancestry | Conclusions needing the missing closure | Everything not depending on it | Whoever can supply the missing events |
| B8 removed author in relied-upon history | This newcomer's integration of that history | Inspection; unrelated berths; the rest of the team continues unaffected | The newcomer's human, for the past only |

A person's own device may stay paused indefinitely if its human chooses not to resolve the pause.
That harms only that person, and it is a better outcome than a device guessing.

## What this transcript requires the code to change

These are consequences of the design above, not a review of unrelated defects.

1. **Root the certificate graph.** `trusted_device_keys_by_teammate` must take the root as a caller-supplied parameter and reject self-issued certificates that do not match it (B4).
2. **Widen and record the bootstrap commitment.** It must cover scope, both parties' keys, the root, the frontier, and the snapshot digest, in both directions, and the comparison result must be stored with the trust view (B1).
3. **Give the invitation token a commitment and verify it.** `accept_invitation` must check the clone against the commitment before adopting anything (B1, B2).
4. **Make quorum evidence.** Admission quorum must come from a signed record, not from the unsigned `team_setting` table (B5).
5. **Give berths a signed origin.** Signed records currently name `berth_id` values that no signature establishes (B5).
6. **Add a signed removal record.** Until one exists, B8 has no evidence on either side ([#263](https://github.com/benjaminy/small-sea-collective/issues/263)).
7. **Scope certificates to berth and purpose** ([#266](https://github.com/benjaminy/small-sea-collective/issues/266)) (B9).
8. **Bind prekey bundles to the device key they are filed under** (B10).
   Implemented locally with a signed wrapper; freshness and B9 authority remain separate gaps.
9. **Give NoteToSelf signed device-authority records** (sibling path, stage 1).
10. **Reconcile the full-ancestry verifier with the intended retention model** ([#190](https://github.com/benjaminy/small-sea-collective/issues/190), [#266](https://github.com/benjaminy/small-sea-collective/issues/266)) (B8).
11. **Decide whether Manager repositories sign commits.** `Repo.configure_signing` (`packages/cod-sync/cod_sync/repo.py:277`) has no call site outside cod-sync's own tests, so every Manager commit today is unsigned and Git contributes no authorship evidence at all.

## Where this leaves the six questions

| Question | Answer |
| --- | --- |
| What is authenticated independently first? | The whole exchange, in B1, by two humans comparing one string. Not a descriptor, a team name, a decryptable payload, or a signer list. |
| Adopted trust, validated anchor, or both? | Both, and they are different steps. B1 and B4 anchor the history to a root the human authenticated; B7 is the local decision to adopt a particular view. The anchor limits what a dishonest introducer can substitute; it does not make the introducer honest. |
| What binds the ceremony to the exact history? | The frontier and snapshot digest inside the B1 commitment, checked at B2 and B4. On the sibling path this binding does not exist yet, because the baseline has no production delivery mechanism. |
| How is a key recognized for a berth and purpose? | B9, which today has no scope to check against because certificates carry only team and teammate. |
| What about an author removed before joining? | B8. Old signatures stay authentic; the newcomer pauses its own integration for human review; acceptance covers the past, is revisitable, and grants nothing about the future. |
| What happens when evidence is missing or contradictory? | B6's four outcomes and the pause table above. Only "differs on evidence" is an authentication failure; the rest are pauses, and an override moves the device, not the verdict. |

## Open blockers

Two questions remain open, and neither can be settled by writing more of this document.

**How team evidence reaches a sibling device.**
A person's devices may deliberately join different subsets of their teams, so team evidence cannot simply ride along with the identity join.
Until there is a mechanism, the sibling path's stage 2 has no authenticated starting point, and `_copy_team_baseline` remains a test fixture standing in for a design.

**Whether Git retains the ancestry the verifier demands.**
B8's authorship check and the intended deletion of old Git contents pull in opposite directions.
The resolution belongs to #190 and #266 together, and this transcript records the dependency rather than choosing.
