# Planning notes

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
