# Draft issue updates for human publication

Nothing in this file has been posted to GitHub.

## #262 — bootstrap transcript

The branch specifies both first authority inputs and both entry sequences.
Invitation authenticates the complete request/response and exact view before an explicit anchor adoption.
Sibling identity join distinguishes signed delegation from local recognition of continuity; each team's enrollment then requires its own evidence.
The team-delivery models start with an empty store and preserve the authenticated response before fetching.
The review packet links the cases, reproduction commands and model limits.
Human review and closure remain separate from the agent's assessment of the draft.

## #266 — berth-scoped signing and verifier wiring

The transcript and probes expose a distinction a flat allowed-signers set cannot express: an accepted finite work set, a key's authority under a selected Constitution basis, and authority for later work are separate inputs.
A signed timestamp or reference to an old grant cannot establish that new work predates removal.
Specify how a Git item binds to the authority basis used to judge it, what the verifier proves, and what remains a local acceptance decision.
Missing or ambiguous basis evidence must differ from an invalid signature.
Identity continuity does not supply team authority, and team enrollment does not confer every berth's signing purpose.
The new models deliberately stop before runtime wiring.
The actual-Git follow-up in `Experiments/berth_authority_basis/` now reproduces both naive key-set failures through SshCommitVerifier itself.
A signed experimental basis trailer does not prove creation before removal, even when both author and committer dates precede it.
The bounded model keeps exact finite acceptance scoped to a device, berth and purpose and pauses absent, unknown or multiply claimed basis inputs.
Define the actual work-to-basis binding and its unambiguous encoding before wiring a current signer set into fetch.

## #190 — retention clarification

The earlier bootstrap draft overstated a conflict between file-content retention and full commit-ancestry verification.
The actual SshCommitVerifier still verifies retained signed commit objects after an old blob or tree is removed; reading that file fails.
A missing parent commit fails the verifier, as does shallow history under its existing contract.
Retain complete original commit metadata where that contract is used, and keep historical content availability separate from signature evidence.
The probe does not implement partial-object bundle transport or prove that unavailable historical contents can be inspected.

## #263 — removed-author acceptance

The newcomer can explicitly accept an exact finite work set under a named view without granting future authority.
That decision remains revisitable and is not a permanent exemption for the signing key.
The finite-history model rejects later work under the old decision and preserves the missing creation-time proof.
A complete removal extension and the mechanism for reconsidering and repairing accepted state remain separate work.

## Identity ceremony policy follow-up

The independent review leaves a policy question about choosing a fresh per-team comparison when stage 1 rests on local recognition rather than retained delegation.
The current draft permits the signed route and carries the missing proof forward; a repeated comparison with the same compromised claimant cannot establish continuity by itself.
A later product review should distinguish a fresh independently known person/channel from merely rechecking the same claimant before changing the default.

## #266 — separate signature evidence from authority

`Experiments/git_signature_evidence` exercises the existing Repo.signature_report with an intentionally empty signer file.
On the tested Git version, an authentic unrecognized signature produces `U` with its fingerprint; a tampered signature produces `B`.
Adding the signer key changes recognition to `G`, preserving the fingerprint.
The conservative SshCommitVerifier still rejects both unrecognized and invalid cases with distinct errors.
Investigate a separate evidence-report interface that preserves these distinctions before Manager applies berth, purpose and local-view policy.
Do not weaken the existing acceptance contract by simply allowing `U`, and retain original-object, ancestry and configuration checks.
