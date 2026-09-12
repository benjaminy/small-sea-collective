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
