# Planning notes

## Selected bootstrap design

The human selected option D from the subsequent discussion: authenticate snapshot delivery through the bootstrap exchange, then reconstruct and compare the authority-bearing state from permanent Constitution evidence.
This option D is distinct from the earlier brief's A/B/C alternatives.

The independently compared exchange binds the exact Constitution frontier (selected events and their ancestors), the snapshot digest, and the operation's identities, keys, and scope.
The newcomer checks that the received snapshot matches the digest, establishing which bytes the introducer offered.
The newcomer separately verifies Constitution evidence and applies named extension rules and local policy to reconstruct the authority-bearing semantic state.
It compares that state with the snapshot; a matching delivery digest alone does not establish correct authority.
Git may carry the transient snapshot, but permanent Constitution evidence must support lasting authority claims without requiring permanent Git ancestry.
This choice does not add permanent snapshot endorsement events or authorize a runtime retention change.

The next task is to classify every bootstrap-critical field as derived from Constitution evidence, a participant-local choice, or delivery information.
For any essential field the permanent evidence cannot establish, name the additional evidence or explicit local trust decision needed.
Define reconstruction in the presence of concurrent events and policy differences; do not assume that event integrity yields a unique membership verdict.
Compare semantic state rather than SQLite file bytes, while retaining the snapshot digest for exact delivery binding.

The preceding human discussion also selected exchange comparison over a reusable fingerprint anchor, introduction plus authority validation, and bootstrap at the exact offered view before later updates.
The human accepted fresh sibling exchanges and asynchronous invitations as the working direction.
Different devices must be able to join different subsets of teams; the mechanism for delivering team evidence remains open.
Manual comparison first was the assistant's proposed way to exercise untrusted artifact delivery; direct scanning remains a later product path, not a settled implementation requirement.
For removed-author history, the human selected pausing the newcomer's integration for local review with the evidence preserved.
For absent authentication, default to inspection with ordinary integration and sensitive actions paused, while allowing explicit local acceptance that leaves the authentication result unchanged.
These are local implementation and acceptance policies, not rules that every wire-compatible implementation can be forced to obey.

The earlier brief needs two technical corrections when its material enters the durable transcript.
A root commit plus a governance projection digest does not identify an exact later history.
A sufficiently strong independent comparison of the complete exchange can detect substitution of both artifact and history; recording the comparison is a separate obligation for either ceremony.
Preserve authentication of the joining request as well as the response; comparing only the authorizer's key cannot replace both jobs.

The current full-ancestry Git verifier contract and the intended deletion of old Git contents need explicit reconciliation.
Do not claim that selecting this design changes the verifier or makes current bootstrap enforce it.
Validation must include an authentic delivery whose snapshot misrepresents Constitution evidence, as well as substitution during delivery and unavailable old Git contents.

## Starting evidence — 2026-09-12

The branch began with a clean working tree.
The kickoff read issue bodies and repository documentation; the function and micro-test references below are starting points for the code audit, not completed verification.

`packages/small-sea-manager/spec.md`, “Link new device — primary flow,” already explains the circularity: the welcome bundle selects the remote descriptor, and its signer is checked against the fetched NoteToSelf database.
The second human comparison or equivalently authenticated delivery supplies the independent authorizer binding.
The spec says the current API returns a confirmation string without recording whether the comparison happened or matched.
It separately describes conservative intended policy and a research override; the audit must distinguish these from enforced behavior.

The linked-team section explicitly requires an existing readable team baseline.
Its micro tests supply that baseline through `_copy_team_baseline(...)`.
Authenticating that baseline is part of #262's problem, not a prerequisite the transcript can assume without explanation.
The flow creates a fresh team-device key and transfers current sender-key receiver state; this does not by itself establish berth-scoped Git signing authority.

The invitation sections describe a proposal anchored to a team-history commit and governance digest, acceptance bound to the allocated teammate ID and proposal nonce, and a completed admission transcript endorsed under Manager policy.
The next audit must locate the independently authenticated input and determine exactly how it binds the downloaded history.

`packages/small-sea-hub/spec.md` explicitly assigns bootstrap trust to #262 for both raw invitation reads and descriptor-bound bootstrap transport.
Transport authorization alone does not authenticate the fetched team's history.

`packages/cod-sync/README.md` documents an explicit-key verifier that checks history before fetch advances a pin or publication accepts a stored observation.
No runtime caller supplies it yet.
Its evidence identifies commit signers; Manager owns key authority.

`architecture.md` and `Documentation/team-constitution.md` keep event integrity and ancestry separate from membership and local acceptance policy.
`packages/wrasse-trust/README.md` still describes a device-only, per-team signing-key direction; #266 explicitly requires reconciling that with berth-scoped keys.
This branch should expose the distinction without silently performing #266's redesign.

## Code audit entry points

- `packages/small-sea-manager/small_sea_manager/provisioning.py`: identity welcome creation/consumption, `accept_invitation`, `prepare_linked_device_team_join`, `create_linked_device_bootstrap`, and `finalize_linked_device_bootstrap`.
- `packages/small-sea-manager/tests/test_identity_bootstrap.py`, `test_linked_device_bootstrap.py`, `test_invitation.py`, and `test_hub_invitation_flow.py`: ceremony checks and fixture-supplied trust.
- `packages/cod-sync/cod_sync/verify.py` and `packages/cod-sync/tests/test_verify.py`: supplied-key contract, full required ancestry, and unknown-signer behavior.
- `packages/wrasse-trust/wrasse_trust/constitution.py`: the implemented boundary between structural evidence and policy.

## Prior art

#262 preserves the prior survey of MLS credential authentication, TUF initial root delivery and root evolution, and Git's supplied allowed-signers set.
The old `.IN_PROGRESS/issue-190-verify-signatures/notes.md` is absent from this checkout.
Use the issue's preserved summary as a research lead; check primary sources before making new detailed claims about those systems.
No fresh prior-art audit or micro-test run was performed during kickoff.

## Code trace — 2026-09-12

Static reading of `packages/small-sea-manager/small_sea_manager/provisioning.py`, `note_to_self_sync.py`, `packages/small-sea-note-to-self/small_sea_note_to_self/bootstrap.py`, and the four micro-test files.
No micro tests were run; the claims below are about what the code checks, not what it was observed to do.

### Identity join (new device, same person)

`create_identity_join_request` generates fresh encryption and signing keys and an `auth_string`: a hash of the join-request artifact.
`authorize_identity_join` writes the joiner's public keys into the authorizer's NoteToSelf `user_device` table, commits, signs a welcome bundle with the authorizer's device signing key, and seals it to the joiner's encryption key.
The bundle carries participant hex, joiner device id and key, identity label, the remote descriptor, expiry, and the authorizer's label.
It carries no commit hash and no history commitment.
`second_confirmation_string` hashes the artifact, the bundle plaintext, and the signature; it is returned to both sides and compared by a human, in principle.

`prepare_identity_bootstrap` decrypts the bundle and checks it names the pending join request; that proves only that the sender held the joiner's public key, which is public.
`bootstrap_existing_identity` fetches NoteToSelf from the descriptor in the bundle and adopts it.
`finalize_identity_bootstrap` then looks up the authorizing device's signing key in the *fetched* database and verifies the bundle signature with it.
This is the circularity named in the Manager spec: an attacker who controls the descriptor can supply a database that lists their own key as the authorizer and a bundle signed with that key, and every code check passes.
The only thing that catches it is the human comparing `second_confirmation_string`, and no code path records whether that comparison happened.
Repo-wide grep: nothing outside `provisioning.py` and `bootstrap.py` consumes the confirmation string; it is exported and displayed, never stored.

Adoption checks are structural: `_require_note_to_self_tree` requires exactly one regular `core.db` blob, and `_adopt_source` refuses a source that shares no ancestry with local history.
Neither checks a signature.
The blank-install case goes through `_adopt_into_unborn_repo`, where there is no local history to compare against.

Micro tests:
`test_identity_bootstrap_rejects_unknown_signer_and_blocks_installation` rewrites the bundle's signer id to an unknown device and expects failure plus a persistent `identity_bootstrap_untrusted` block.
`test_identity_bootstrap_rejects_wrong_known_signing_key` covers a known device id with a mismatched key.
Both tests hold the fetched database fixed and vary the bundle.
No micro test varies the fetched database to list an attacker's key, which is the #262 substitution case.
The round-trip test asserts the two confirmation strings are equal in-process, which is the human comparison simulated by the test harness.

### Linked-device team join (sibling device, same teammate)

`prepare_linked_device_team_join` requires the team row to already exist in the joiner's NoteToSelf, and requires the joiner to have no sender key yet.
It generates a fresh team-device key and signs the request with both the NoteToSelf device key and the new team-device key.
`create_linked_device_bootstrap` on the authorizer checks the NoteToSelf signature against its own `user_device` table (so identity join must have completed and synced first), issues a device-link cert for the new team-device key, and sends sender-key receiver state under X3DH plus a ratchet.
`finalize_linked_device_bootstrap` checks the authorizer's team-device key against `get_trusted_device_keys_for_teammate` read from the joiner's *local* team database, verifies the response signature and the cert against team id, teammate id, and the session's own key, then writes the cert and device row into its team database and commits.

Where the local team database comes from is the unresolved step.
Every micro test in `test_linked_device_bootstrap.py` supplies it with `_copy_team_baseline`, which `shutil.copytree`s the authorizer's team folder and inserts the team row by hand.
No production code path clones a team for a linked device; the spec says it needs an existing readable baseline and leaves the fetch for later.
So in the sibling path the trust in the authorizer's team-device key comes entirely from the baseline, and the baseline's authenticity is exactly what #262 has to settle.
The identity-join circularity and this baseline gap are the same problem twice: the fetched database is the sole source of the key used to check the thing that pointed at the fetched database.

### Invitation acceptance (new teammate)

`create_invitation` writes a signed `admission_proposal` record into the inviter's Core, anchored to the inviter's current head commit and constitution digest, then commits.
The token handed to the invitee is unsigned base64 JSON: proposal id, nonce, team id and name, inviter and invitee teammate ids, display name, cloud protocol/url/bucket, and the inviter's sender-key distribution.
No anchor commit, no digest, no inviter public key, no signature.

`accept_invitation` clones from the store named in the token, checks out the observed head, inserts the team row into NoteToSelf, generates a fresh team-device key, and signs an acceptance bound to the proposal id and nonce.
It performs no check that the cloned history contains the proposal, that the proposal's signer is the named inviter, or that the head relates to any evidence in the token.
Whoever controls the URL in the token controls what the invitee accepts as team history.
`complete_invitation_acceptance` (inviter side) does verify the acceptance signature, nonce, team id, and invitee id against its own proposal row, so the *inviter's* view is protected; the invitee's is not.
Cod Sync's explicit-key verifier is not called on either path.

Micro tests: `test_full_invitation_flow` passes the token in-process and asserts nothing about the invitee's verification of the clone; there is no adversarial-clone test.

### What each path independently authenticates today

| Path | Independent input | Bound to history? | Recorded? |
| --- | --- | --- | --- |
| Identity join | `auth_string` (artifact hash) and `second_confirmation_string` (artifact + bundle + signature), both compared by a human | No; the bundle carries a descriptor, not a commit | No |
| Linked-device team join | Nothing; relies on a team baseline that only test fixtures supply | Baseline is the history | N/A |
| Invitation | Nothing; unsigned token, URL-controlled clone | No | No |

Everything else the code checks is internal consistency of material fetched from the same source the check is meant to validate.

## Snapshot field classification — 2026-09-12

Option D asks the newcomer to reconstruct authority-bearing state from permanent Constitution evidence and compare it against the delivered snapshot.
That is only meaningful for fields the evidence can actually establish.
This section classifies the fields in the two databases a newcomer receives and names what the comparison cannot cover.

Method: static reading of `core_other_team.sql`, `shared_schema.sql`, and every writer of those tables in `provisioning.py`, plus the cert resolver in `wrasse_trust/identity.py`.
No micro tests were run.
The claims are about which bytes are covered by a signature and which code inserts each row.

### Classes

- **Constitution-derived.** The value sits inside the canonical signed bytes of a record, and the newcomer can verify that signature.
- **Local choice.** The value is an unsigned row. Whichever device wrote it chose it, and no permanent evidence pins it down.
- **Delivery.** The value describes this exchange or this fetch, not the team.

A second question cuts across all three: when a field is a local choice, can the newcomer make that choice for itself, or is it stuck adopting the introducer's?
Those are very different risks, and the classification below marks the difference.

### Team Core database

| Table and field | Class | Basis |
| --- | --- | --- |
| `admission_proposal` except `invitee_label_payload` | Constitution-derived | Signed by the inviter's team-device key over canonical JSON; `mode_plan`, `invitee_teammate_id`, `team_id`, `nonce`, `expires_at` are all inside the signed bytes. |
| `admission_proposal.invitee_label_payload` | Local choice, introducer's | Deliberately excluded from the signed bytes as droppable PII. `invitee_label_commitment` is nullable and no writer populates it (#168), so nothing commits to the label at all. |
| `admission_acceptance` | Constitution-derived | Signed by the invitee under an embedded key; `author_device_key_id` must equal the key id derived from that key. |
| `endorsement`, `finalization` | Constitution-derived | Signed; `finalization.endorsement_count` is signed over endorsements the finalizer re-verified. |
| `admission_revocation` | Local choice, introducer's | The schema comment states it is a mutable projection, not a signed record. A newcomer cannot tell a revoked proposal from an unrevoked one by evidence. |
| `key_certificate` | Constitution-derived, but unrooted | Each cert verifies. The graph they form does not identify a team; see "The root is self-asserted" below. |
| `team_device` | Constitution-derived (redundant) | A projection of the cert graph. `trusted_device_keys_by_teammate` is the authoritative form and ignores this table. |
| `teammate.id` | Constitution-derived for admitted teammates | `invitee_teammate_id` is a signed proposal field and `finalization` makes it effective. The founder's own id has no signed record; only a self-issued membership cert asserts it. |
| `teammate.display_name` | Local choice, introducer's | `finalize_admission` writes it from `proposal_row[13]`, the unsigned `invitee_label_payload`. |
| `teammate.identity_public_key` | Absent | No caller of `_upsert_teammate_row` passes it; the column is always NULL. Teammate-to-key binding exists only in the cert graph. |
| `app`, `team_app_berth` | Local choice, introducer's | `_ensure_team_app_activation` inserts fresh uuid7 rows with no signed record. Signed records then reference `berth_id` values that no signature establishes. |
| `berth_role` | Split | `set_teammate_integration_mode` and `_expand_mode_plan_at_finalization` write roles backed by signed `integration_mode_change` records. `_ensure_team_app_activation` also inserts initial roles unsigned, including the founder's Core role. |
| `integration_mode_change` | Constitution-derived | Signed, with `teammate_id`, `berth_id`, and `mode` inside the signed bytes. |
| `team_setting` | Local choice, introducer's | Plain key/value. `admission_quorum` and `proposal_expiry_seconds` decide whether a `finalization` was legitimate, and nothing signs them. |
| `teammate_berth_storage_announcement` | Constitution-derived content, delivery meaning | Signed, so the announcement is authentic; what it announces is a URL and location, which is delivery information. |
| `device_prekey_bundle` | Local choice, introducer's | No signature column. X3DH verifies `signed_prekey` against `identity_signing_public_key` carried in the same bundle, and nothing checks that key against the trusted device key the row is filed under. |
| `invitation` | Local choice, introducer's | Mutable status columns from the older flow. The Hub and `admission_events` read it; no production writer remains in the Manager. |
| `anchor_commit`, `constitution_digest`, `constitution_snapshot_json` on every record | Constitution-derived assertion, not independent evidence | Signed, so the author really claimed this view. The digest is a live query over current `teammate`/`team_device`/`berth_role` state, so it commits to a projection the newcomer must first reconstruct. |

### NoteToSelf database

`shared_schema.sql` has no signature column in any table.
`user_device`, `team`, `team_device_key`, `cloud_storage`, `berth_cloud_allocation` are all unsigned rows.
Every field in the identity snapshot is therefore a local choice, and on the identity-join path the newcomer is adopting the introducer's.

This is the sharpest result of the classification.
Option D reconstructs authority-bearing state from permanent Constitution evidence, and on the identity path there is no such evidence to reconstruct from.
The identity snapshot can be bound to the exchange by digest, and that is all.
Its authority content rests entirely on the human comparison of `second_confirmation_string`, which no code path records.

### The root is self-asserted

`trusted_device_keys_by_teammate` seeds trust like this:

```
if cert.issuer_participant_id == admitted_teammate_id:
    issuer_keys = [cert.subject_public_key]
```

Any self-issued membership cert is a trust root.
The resolver accepts as many roots as the snapshot contains and never asks which one is this team's genesis.
An attacker who fabricates a team database fabricates its genesis membership too, and the graph resolves cleanly.

So reconstruction from the snapshot's own records cannot distinguish the real team from a manufactured one, no matter how carefully each signature is checked.
Option D's comparison has force only if the exchange independently carries the root: the team id together with the genesis membership cert's subject key, or an equivalent pin on the inviter's team-device public key.
Without that, reconstruction and snapshot agree because they are the same forgery read twice.

### Essential fields permanent evidence cannot establish

1. **Which team this is.** Needs the exchange to carry `team_id` plus a genesis or inviter device public key.
   Nothing in the snapshot supplies it.
2. **Admission quorum.** `team_setting` is unsigned, so whoever wrote the snapshot chose the threshold that its `finalization` records had to clear.
   Needs either a signed policy record or an exchange-carried value the newcomer adopts as an explicit local decision.
3. **Berth existence and initial roles.** `app` and `team_app_berth` rows and the founder's Core `berth_role` have no signed origin, yet `integration_mode_change` and `mode_plan` expansion both name `berth_id` values.
   Needs a signed berth-creation record, or the newcomer treats berth identity as adopted rather than verified.
4. **Display names.** Unsigned and droppable, with the commitment column unpopulated.
   The newcomer can only treat a name as the introducer's claim.
   This is consistent with keeping PII off the durable chain; the transcript should say so rather than imply a gap.
5. **Prekey bundles.** Unsigned and unbound to the device key id they are filed under.
6. **Removal.** There is no signed removal or exclusion record anywhere in the schema; `admission_revocation` is a mutable projection of a proposal the inviter abandoned.
   #263's removed-author case therefore has no permanent evidence on either side, which the transcript must state plainly.
7. **Berth and purpose scoping.** `issue_membership_cert` and `issue_device_link_cert` bind `team_id` and `teammate_id` and nothing else.
   A key trusted for one berth is trusted for all of them.
   This is #266's gap, visible here as a field the classification cannot populate.
8. **Git commit authorship.** `Repo.configure_signing` has no call site outside cod-sync's own tests, so every commit the Manager writes today is unsigned.
   Git currently carries no authorship evidence at all, which makes the snapshot's delivery digest the only binding Git contributes.

### Reconstruction under conflict and missing evidence

The projection is not policy-independent, and three specific mechanisms make that concrete.

**Quorum is local.**
Two devices holding the same records but different `team_setting` rows reconstruct different sets of legitimate finalizations.
Reconstruction must therefore be defined as a function of explicitly named policy parameters, and the transcript must report the parameters alongside the verdict.

**The digest depends on activation.**
`_constitution_digest` runs over current `teammate`, `team_device`, and `berth_role` state, so its value depends on which records the device has already activated.
`_admission_status` returns `invalidated` on any drift.
A newcomer reconstructing from a frontier can reach a different activation order than the introducer did and compute a different digest from the same evidence.
Reconstruction must not treat a digest mismatch as forgery.

**Ancestry gaps are not forgeries.**
`endorsement` and `finalization` carry foreign keys to their proposal.
A newcomer missing that proposal cannot verify the rows that reference it.
Following the Constitution doc's handling states, those rows are ancestry-incomplete: hold them, do not drop them, and do not let anything that needs their closure proceed.

Given all three, the comparison step should produce one of four outcomes rather than a boolean:

- **Agrees.** Reconstruction under the named policy matches the snapshot's semantic state.
- **Differs under policy.** The states differ only in ways the policy parameters explain. Record both and the parameter responsible.
- **Differs on evidence.** The snapshot asserts state that the records do not support. Stop and show the first field that diverges.
- **Incomplete.** Required ancestry is missing. Pause, preserve what arrived, and name what is absent.

Only the third is an authentication failure.
The other two are pauses, and a human override changes which of them the device acts on without changing what was verified.
