# Bootstrap trust anchor: decision brief

Status: historical proposal, 2026-09-12; superseded by the human discussion recorded in `notes.md` under "Selected bootstrap design".
The selected design authenticates the exchange and reconstructs authority-bearing snapshot state from permanent Constitution evidence.
The option A recommendation, option B substitution claim, and removed-author default below are retained as review history, not current decisions.
Companion to `plan.md` and `notes.md`; the code trace in `notes.md` is the evidence base.
This brief asks for one decision: what a newcomer authenticates independently before it trusts a fetched Small Sea history.

## The problem in one paragraph

A device that has never synced gets a welcome bundle or invitation token, fetches a history from wherever that artifact points, and then checks the artifact against keys it found in the fetched history.
An attacker who controls the fetch location can supply a history listing their own keys and an artifact signed with those keys, and every code check passes.
The Manager spec already names this circularity for identity linking.
The code trace shows the same shape on all three entry paths, with different amounts of human ceremony on top.

## What each path authenticates today

| Path | Independent evidence | Binds a specific history? | Comparison recorded? |
| --- | --- | --- | --- |
| Identity join (new device, same person) | Two human-compared strings: a hash of the join request, then a hash of request + welcome bundle + signature | No; the bundle names a remote descriptor, not a commit | No |
| Linked-device team join (sibling device) | None; the authorizer's team key is checked against a local team database that only test fixtures supply | The baseline is the history, and nothing authenticates the baseline | Not applicable |
| Invitation (new teammate) | None; the token is unsigned JSON with a cloud URL and ids | No; the invitee clones whatever the URL serves and checks nothing about the proposal or head | No |

The inviter side of invitation does verify the acceptance against its own proposal row, so the inviter's view is protected.
The newcomer's view is not, on any path.
Cod Sync's explicit-key verifier exists and is called by nothing.
No micro test varies the fetched history to list an attacker's key; the existing rejection tests hold the database fixed and tamper with the bundle.

## Posture

We are not drawing one line between safe and broken.
Each transcript step should record what evidence was gathered, apply a default, and leave a human able to override the default with the evidence preserved.
An override changes local policy; it does not rewrite the authentication result.
Prior art is borrowed where it fits:

- TUF separates initial root delivery, which it leaves to the adopter, from verifiable root evolution.
  Small Sea's out-of-band step is the adopter's delivery; Core history signed by already-recognized keys is the evolution.
- MLS requires credential validation when joining through GroupInfo; encrypted delivery alone does not establish identity.
  That is the welcome bundle: decryption proves the sender held a public key, nothing more.
- Git's allowed-signers file makes the key set an input to verification, not an output of it.
  That is the contract Cod Sync's verifier already exposes.

## The two adversarial cases differ in kind

**Substituted history listing attacker keys** is a failed binding.
The default is to stop, show which independent evidence the fetched history failed to match, and keep both the artifact and the fetched history for inspection.
A human may proceed anyway, and the record then says the history was accepted on that person's say-so without an independent match.

**Original author removed before the newcomer joins** is a policy question.
The author's old commit signatures are still authentic, and removal is a later event in the same history.
The default is to accept the history with the removal recorded and grant that key no authority over new work.
A stricter team may choose to pause on any history containing a removed author's commits; that is a local policy choice, not a verification failure.
The reconsideration policy itself belongs to #263 and is not settled here.

## Anchor options

The decision is what independent evidence the newcomer holds before fetching.
All three options apply to all three entry paths; the sibling and invitation paths currently have nothing and would gain the most.

### Option A: signed anchor statement with out-of-band fingerprint (recommended)

The authorizer signs a short statement naming the team or participant, the root commit of the history being offered, and the current governance digest.
The statement travels with the bundle or token.
The authorizer's signing-key fingerprint is compared by the two humans out of band, replacing today's confirmation strings.
The newcomer verifies the statement with the fingerprinted key, fetches, and checks that the fetched history's root and governance digest match.

Why it closes the loop: the key arrives outside the fetched data, and the statement commits to a specific history rather than a location.
What the authorizer is trusted for: that this history is the team they mean, as of the digest.
What it does not prove: that the authorizer is honest or that their view is complete.
Cost: one new small artifact, and the humans compare a fingerprint instead of a hash of the bundle.
Both current bootstrap flows already have a comparison step, so the ceremony does not get longer.

### Option B: extend the confirmation string to cover the root commit

Keep today's mechanism.
Add the root commit and governance digest to the welcome bundle and to the invitation token, and let the existing confirmation hash cover them.
The newcomer checks the fetched history's root against the bundle after fetch.

Why it helps: no new artifact, and the history is now named.
What it still lacks: the comparison is still unrecorded, and the bundle signature is still verified against a key from the fetched history.
An attacker who controls the fetch location and can intercept the bundle still wins; an attacker who controls only the fetch location now loses.
This is the smaller change with the weaker guarantee.

### Option C: trust the first fetch and record it as unverified

No new ceremony.
The newcomer fetches, adopts, and records that this history was accepted without independent evidence.
Later evidence, such as a second device or a peer disagreeing about the root, surfaces the conflict for a human.

Why it fits the posture: it is honest about what happened and preserves the evidence.
Why it is not enough on its own: substitution is undetectable until a second source disagrees, and a lone attacker who is also the only sync peer never produces one.
This is an acceptable recorded fallback when the ceremony is skipped, not a default.

### Recommendation

Option A as the default, with Option C's recording as the fallback state when a human chooses to skip or cannot complete the comparison.
Option B is Option A without the recorded, independent key, and the difference is exactly the substitution case #262 asks about.

## Questions for reviewers

1. Is a fingerprint comparison acceptable ceremony for the regular-people audience, given both current flows already ask for a string comparison?
2. Should the sibling-device path get its own anchor statement, or should it inherit the identity-join anchor and re-check the team root against it?
   Inheriting means one ceremony per person-device, not per team; the cost is that the identity anchor must then name every team's root, or the sibling must fetch team roots through the already-authenticated NoteToSelf.
3. For the removed-author default, is "accept history, grant no new authority, record the removal" the right default for a team with no stated policy?
4. Is Option C's recorded-unverified state something a team can legitimately live in, or must it block ordinary use until resolved?

## What this branch will not do

Runtime wiring of the verifier, key derivation, and the Hub interface stay in #266.
The reconsideration policy for removed authors stays in #263.
Nothing here adds endpoints, schemas, or migration machinery.
