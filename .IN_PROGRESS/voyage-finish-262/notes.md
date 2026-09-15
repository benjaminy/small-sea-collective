# Findings

## Identity continuity

The signed chain and explicit local recognition candidates answer different questions.
A surviving chain records delegations by keys; it does not prove those keys remain with the same person.
A human decision can recognize a returning person without recreating lost signatures.
Keep the complete local decision, its exact exchange, actor, scope, basis, earlier evidence references and missing proof distinct from the chain.
Neither identity conclusion grants team authority.
The adopted design combines these paths without forcing another device or team to accept the recognition.

## Team evidence delivery

Both a fresh comparison and a response signed through the already recognized identity relationship can bind the request and selected team evidence.
The signed route is the working choice; it saves a ceremony but inherits the identity key's compromise risk.
A separate signed identity-to-team binding and team enrollment authority check remain necessary.
The experiments start with an empty store and retain the authenticated commitment before fetch, including when the pinned snapshot is unavailable.
Unsigned NoteToSelf rows never supply the trusted team baseline.

## Retention correction

The previous transcript conflated old file content with commit ancestry.
The actual verifier accepted retained signed commits with an old blob or tree absent, while reading the file failed.
A missing parent commit failed verification.
The intended content window therefore does not inherently contradict commit-signature verification; retention transport and complete authority evidence remain separate obligations.
See the reproduction commands and limits in [the review packet](review-packet.md).

## Validation boundaries

The new models use real Ed25519 signatures but small generated schemas and full-digest comparison fixtures.
They do not implement the wire ceremony, encrypted transport, general event-DAG policy, or runtime Manager provisioning.
The historical-work model binds each work statement to an authority basis, which #266 still must specify for actual Git work.
The local skill update records direct authorization of repository processing by the named cloud-agent provider as a preflight concern; it lives outside this Git repository.

## Bounded #266 follow-up

The actual SshCommitVerifier accepts a newly made backdated commit under the historical union of berth-A keys.
Using only the current berth-A key rejects the current head because its accepted parent used the removed key.
The contextual model retains exact finite acceptance separately from current local authority and treats the commit's signed basis citation as a claim, not proof of creation time.
It distinguishes missing or ambiguous basis from bad signatures, rejects another berth's key, and shows that changing the local authority view changes a decision without changing the signature evidence.
The first worker fixture reassigned a berth-B key to A; the orchestrator replaced it with removal of A's own current key to preserve berth-key separation.
The probe and its simulated inputs are in `Experiments/berth_authority_basis/`; no runtime API or policy was implemented.

## Contextual fetch boundary

The existing verifier hook can reject simulated authority failures before a peer pin moves.
It cannot by itself establish that a view remains applicable after the hook returns.
A deterministic injection at the actual `advance_ref` call reproduces that gap; a control changing the view before verifier return is rejected.
The experiment's receipts and grants are fixed local simulated inputs, not a Constitution-derived or globally current policy view.
It records a commit-to-fingerprint map and the captured view in memory; that map identifies which key signed each checked commit but is not a durable audit record or complete authority proof.
The pin retains only an object id.
This separates a decision under a named view from a guarantee about policy at a later action.
The transport pin is not application acceptance or key-release authority.

The retained-pin control sharpens the result.
An old-store fetch verifies signed A as its exact `observed_head`, while `advance_ref` reports `stale` and leaves a preexisting descendant pin at B.
The A-only verifier rejects B when asked to verify it directly, and a fresh-pin control points to verified A.
No new unverified pin advancement occurs: the fixture presupposes an earlier operation that imported B and seeded the pin.

Run `.venv/bin/python Experiments/contextual_git_fetch/probe.py` and `.venv/bin/python Experiments/contextual_git_fetch/retained_pin.py`.
Both use temporary signed repositories and local stores; neither selects a runtime API, synchronization mechanism, integration decision or key-release policy.
These bounded #266 findings do not change the transcript's readiness for human review, and no issue text has been published.
