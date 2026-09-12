# Authenticated publication context — #261

Branch: `hub-authentication-context`.
Issue: [#261 — Bind authenticated publication context at the Hub upload/download boundary](https://github.com/benjaminy/small-sea-collective/issues/261).
Started 2026-09-09 from `b131145`.

## Status and goal

Ready for implementation handoff as of 2026-09-10.
The user accepted proceeding with ordinary-team publication authentication, waiting for missing ownership evidence at dependent read acceptance, and keeping NoteToSelf's existing passthrough contract separate.
The decisions below are settled for this branch; routine encoding, API, and error-code choices belong to the implementer within these constraints.
Initial investigation and local experiments are complete: 26 experimental micro tests and 12 existing group micro tests passed; see [commands, results, and limits](experiments/README.md).
Package implementation has not changed.
The user asked to examine the problem without letting the current implementation determine the solution, and to prefer progress when a mistake or attack has a clear recovery path.

A successful authenticated read must establish that the publisher produced these bytes for the expected writer, team, berth, and logical object path.
Those expectations come from the authorized operation and independently established key associations, not from trusting labels in the downloaded envelope.
The sender signature must bind the context, crypto header, and ciphertext together under an independently associated signing key.
Ordinary authenticated reads return plaintext and commit receiver crypto state only after signature, context, sender-record association, and expected-writer checks succeed.
Authenticate the full header before any iteration-driven key derivation, including Hub's retained-key calculation.
Whether the comparison happens inside the AEAD decision or as a check on authenticated fields afterward is a construction preference, not a requirement; see `notes.md`.
Raw transport routes must have explicit separate contracts where this guarantee does not apply.

## Progress and recovery

Incomplete evidence need not stop fetching, preserving candidate bytes, or unrelated local work.
Use an existing isolated inspection or parked-history workflow where it provides useful progress without treating the candidate as accepted publication.
Do not build a general quarantine or rollback system for this branch.

Do not add provisional plaintext consumption in this branch.
Pause only the dependent acceptance operation while required ownership evidence is missing, including a new invitee's own read before its accepted ownership row arrives.
Reads from an already recognized inviter and unrelated local work can continue when their own prerequisites are satisfied.
Retrying after evidence arrives may be manual; no automatic polling, resolution, or recovery system is required.
A future provisional consumer needs named useful work and a demonstrated repair procedure that preserves unrelated work and evidence; that is separate scope.

## Writer lookup

Use `team_device` in the relevant locally accepted ordinary-team Core view to look up which teammate owns the authenticated sending device.
Compare that teammate with the publisher expected by the read operation.
Sibling devices work because they resolve to the same teammate; this branch needs no new sibling identity model.
Establishing and maintaining Core's accepted identity state belongs to the surrounding framework, not to each publication read.
The device identity must remain cryptographically bound to the payload and its sender key; an unauthenticated envelope label is not sufficient lookup evidence.
Real team provisioning supports the Core lookup, including linked siblings, but an invitee's fetched proposal snapshot lacks its own ownership row until later evidence arrives.
Fresh NoteToSelf has neither the team ownership projection nor group sender-key provisioning; it remains outside this branch's ordinary encrypted-publication contract.
Unknown ownership is an unresolved prerequisite, like a missing sender key; a delayed Core update is one possible explanation, not an established fact.
Retry the affected read when evidence arrives; automatic polling or automatic resolution is not required.
Ownership that resolves to a teammate other than the expected publisher is a rejection.
Ambiguous ownership requires visible human resolution rather than choosing one association.
An absent ownership projection is a distinct named outcome from an available projection with no matching association.
An empty mapping never authorizes skipping the check, and possession of a local private key does not replace accepted ownership evidence.
These outcomes do not authorize membership changes.
Encrypted retained-candidate inspection enforces the same ownership prerequisites; separate raw-byte inspection does not count as authenticated acceptance.

## Settled context and construction

Bind a format version and purpose, team ID, berth ID, and logical object path in the context.
Leave publisher and signing-device identity out of the expected object context; authenticate the sender device ID, chain ID, and iteration in the crypto header.
This separates object coordinates from independently established device ownership.
Keep physical provider location outside the context so copying an object to another authorized location does not invalidate its logical identity.
Make the resolved publisher expectation required at protected acceptance entry points and verify that it is enforced.

Bind the UTF-8 encoding of the logical path string received at the Hub after JSON or query-parameter decoding, without further normalization.
Require the reader to present that same logical string; different strings do not authenticate interchangeably even if a provider treats them as aliases.
Use one unambiguous context encoding and a domain-separated signature transcript that binds context, full crypto header, IV, and ciphertext together.
For today's construction, prefer binding context and header as AEAD associated data as well; the acceptance and state-commit requirements above remain the contract.

Settled since kickoff: implement on today's Sender Keys construction rather than waiting on #264.
The bounded-change test the kickoff set is satisfied by source inspection; see `notes.md`.
Add explicit context to the crypto API and authenticate it with the header and ciphertext; keep team identity in sender-key records distinct from opaque context bytes.
The durable artifacts are the context semantics and the substitution fixtures, not the envelope bytes; a later stored-object envelope should inherit the fixtures.
Do not build ratchet retention or migration machinery here.
If implementation turns up a need for retention, back-fill, or recovery machinery after all, that finding reopens the question and belongs in #264.

## Route scope

| Route | Contract for this branch |
| --- | --- |
| Ordinary encrypted own upload/read | Bind session team, berth, and logical path on upload. Reads expect the local teammate resolved from session management state and accept its independently associated sibling devices. |
| Ordinary encrypted peer read | Expect the teammate selected by the authorized peer request and the session's team, berth, and requested path. A key that decrypts does not choose the expected teammate. |
| Encrypted retained-candidate inspection | Apply ordinary own-read publication checks using the logical path, even at an old physical location. Preserve the existing Manager-only route permissions; successful inspection implies neither integration nor route endorsement. |
| Passthrough own/peer/inspection, including existing NoteToSelf use | Remain raw transport outside publication authentication. Document the limit and the accepting consumer; do not silently downgrade an encrypted operation to passthrough. Do not add NoteToSelf group-key provisioning here. |
| Explicit `/cloud_proxy` | Remain raw transport for invitation acceptance. The NoteToSelf session grants transport access; it cannot supply the remote team's expected publication identity. Identify the Manager bootstrap consumer and its separate verification/known gaps. |
| `/bootstrap/cloud_file` | Remain descriptor/token-bound raw bootstrap transport with a separate acceptance consumer. Downloaded Core cannot establish its own trust anchor. |
| Runtime artifact own/peer reads | Preserve the separate signed sender-key-distribution protocol. Wrapping first-key delivery in ordinary group encryption would be circular; its expected-source and prekey-consumption gaps remain follow-ups. |
| Notification signals | Remain hints, not authenticated acceptance of publication bodies. |

This work does not redesign admission, bootstrap trust (#262), stored-object key retention (#264), berth-scoped keys (#266), or freshness/provider-equivocation policy.
The new-invitee waiting rule governs read acceptance; upload wiring must not invent additional admission policy.
Fixing all separate raw-route consumers is not a prerequisite for the ordinary-team improvement.

## Implementation choices and starting points

The implementer chooses the exact versioned wire representation, function/result shapes, and HTTP error codes and documents them in the Hub spec and relevant crypto documentation.
Keep invalid publication, missing sender key, missing ownership association, absent projection, ambiguous ownership, and provider-confirmed absence distinguishable to callers.
No implementation choice may turn a failed or pending authenticated read into plaintext success or harmless object absence.
Current APIs and the experimental module are evidence, not required designs; do not import the prototype into package code or copy its API solely because it exists.

Start at `packages/cuttlefish/cuttlefish/group.py`, `packages/small-sea-hub/small_sea_hub/crypto.py`, and the own/peer/inspection methods in `packages/small-sea-hub/small_sea_hub/backend.py`.
Trace response handling through `packages/small-sea-hub/small_sea_hub/server.py` and the corresponding `small-sea-client` consumers.
Hub reads Core directly for this framework responsibility; do not add app access to Core or make Cuttlefish depend on Manager policy.
Own team-device public keys in shared NoteToSelf `team_device_key` and device-local secret references establish different facts from accepted team ownership; they are not fallback ownership projections.

Escalate only a finding that changes the accepted scope or guarantee, such as needing new key-retention machinery or discovering that the accepted association cannot be obtained through the existing lifecycle.
Record that evidence and the concrete blocked operation in `notes.md`; routine encoding and error-shape choices do not need another planning round.

## Implementation sequence

1. Initial local reproductions and prototype controls are complete: 26 experimental micro tests and 12 existing group micro tests pass.
   Current HTTP reads accept cross-path, cross-berth, and wrong-writer substitutions; a healthy other team already rejects through missing sender-key state.
   The prototype rejects cross-team context even with the same key available.
   Retained-candidate routing and the final implementation's failure translation remain untested.
2. Implement the agreed context encoding and cryptographic binding at the narrowest shared boundary.
   Keep application identity, routing, and Manager policy out of Cuttlefish; it can authenticate opaque caller-supplied context if that is the chosen API.
   Retain explicit format/version markers and reject unsupported or old unbound formats rather than adding automatic fallback.
   Move signature/header verification ahead of all iteration-driven derivation, including replay-key calculation outside Cuttlefish.
3. Bind the context at upload; resolve the context and the expected writer at each authenticated read entry point.
   Cover own, peer, and retained-candidate reads, including sibling devices; preserve the separate NoteToSelf contract above.
   Do not infer the expected writer from whichever key decrypts successfully.
4. Implement and document the agreed contracts for raw routes and their consumers.
   If a route remains unauthenticated transport, say so in the issue and spec and identify who validates its contents before acceptance.
   Do not describe transport success as authenticated publication.
   Existing gaps in separate consumers can remain focused follow-ups with explicit limits; completing every separate protocol is not a prerequisite for the ordinary-route improvement.
   Retain the narrow prerequisite result; provisional plaintext consumption is outside the accepted scope.
5. Make failed authentication a consistent unsuccessful API result and preserve receiver state.
   Keep missing key/evidence distinguishable from invalid authenticated context; unknown device ownership belongs in the missing bucket.
   Verify the client cannot translate either into successful download or harmless absence.
6. Run the validation below, revise these documents with evidence and remaining limits, and prepare the issue updates in `follow-up.md`.

## Validation: evidence for a skeptical reviewer

Use fresh keys and local fixtures, mocked adapters, and HTTP test clients.
Use MinIO only if provider behavior needs a local integration check; no cloud accounts or external services are needed.
Every rejection needs a passing control using the same fixture and the correct expectations.
Use a compact set of micro tests that proves the chosen contract.
Do not multiply every case across every route: test transcript details once at the shared crypto boundary, then test wiring and failure translation at each protected entry point.

| Claim | Required evidence |
| --- | --- |
| Object context is authenticated (core) | Substitute a valid payload across paths within a berth, across berths within a team, and across teams; each fails while the original context succeeds. |
| Writer expectation matters (core) | Put a valid known sender's payload in another teammate's route; reject it even though its key is available, and accept it on its own route. |
| Failure modes stay distinguishable (core) | Exercise context rejection with the necessary key available, and ownership rejection with valid matching context. Assert which mechanism rejects each case. |
| Multiple devices work (core) | Accept two authorized devices for one teammate, including an own-cloud sibling read; reject a device associated with a different teammate; a device unknown to Core yields the retryable result. |
| Rejection is side-effect free (core) | Snapshot persisted receiver records and in-memory skipped-key maps before failures at current, future, and retained iterations; compare afterward, then successfully read the original payload. |
| Entry points enforce the contract (core) | Exercise real crypto through own, peer, and candidate-inspection backend/API paths with a byte-substituting mock adapter; do not mock out the context check itself. |
| Labels and metadata are authenticated (core) | Parameterize mutations of the signed context and header, IV, ciphertext, and signature at the shared boundary; reject changes without releasing plaintext or changing state, and verify altered iteration is rejected before iteration-driven derivation. Include missing and ambiguous ownership evidence. |
| Sender association is established (core) | Exercise a real distributed signing-key/chain record, its authenticated device identity, the Core-resolved teammate, and the operation's expected publisher together. Reject validly signed device/chain labels that disagree with the independently established record. |
| Path strings survive transport (core) | Round-trip a small set containing percent signs, plus signs, and Unicode through JSON upload and query-parameter download; distinct logical strings fail substitution. |
| Raw routes do not overclaim (core documentation; tests for changed behavior) | State each passthrough, proxy, bootstrap, and runtime contract, its acceptance consumer, and known gaps. Test any changed boundary without requiring implementation of every deferred consumer fix. |
| Failures stay failures (core) | Check HTTP status, response body, client error translation, and absence of plaintext for invalid context; distinguish missing evidence from absent provider objects. |
| Missing evidence can be resolved (core) | Use real new-invitee provisioning: its own read waits without receiver-state changes, while a recognized inviter's read can succeed. After supplying accepted ownership evidence, the same own payload succeeds; a conflicting association still prevents acceptance. Automatic synchronization is not required. |
| Ownership projection is explicit (core) | Ordinary own/peer/inspection reads use the accepted team Core projection. An absent projection yields its own named outcome, distinct from an available mapping with no matching row; an empty mapping never authorizes acceptance. Document and retain NoteToSelf's separate passthrough contract without pretending it has an ordinary ownership projection. |
| Changes preserve useful behavior (affected existing checks) | Run existing checks for correct uploads/readbacks, repeated and out-of-order reads, sibling reads, and conditional-upload failure; add coverage only where changed behavior lacks a meaningful check. |

Broader provider alias surveys, exhaustive route-by-metadata combinations, and automatic recovery machinery do not gate this branch.

For any route with a separate cryptographic consumer, include that consumer's state in the rejection snapshot, not just Hub sender-key rows.
This requirement applies when changing that consumer; it does not expand this branch into fixing deferred raw-route protocols.
If a route remains raw transport, do not claim it passes the encrypted-route substitution matrix.

Start with `packages/cuttlefish/tests/test_group.py` and `packages/small-sea-hub/tests/test_group_crypto.py`.
Extend the relevant cloud API, peer transport/error, runtime watch, and Manager bootstrap micro tests as dictated by the settled contracts.
Run affected package suites after the implementation's focused checks; the current branch contains isolated experiments rather than package changes.

Review the final diff for one authoritative context encoding, explicit context and a required publisher expectation at every protected call site, no new Core access by apps, and no accidental membership-policy or storage-crypto redesign.
Document the exact commands and results when implementation is tested.

## Completion and handoff maintenance

Promote the settled public contract into the Hub spec and relevant crypto documentation, including the explicit raw-route limits.
Move or adapt useful experimental cases into package micro tests against the final implementation.
The characterization probes intentionally assert today's weaknesses; do not keep those assertions as required behavior or let their eventual failure drive compatibility code.
Keep the experiment report as dated evidence and record which final checks supersede it.
The prototype's 26 passing cases are not evidence that the final Hub routes, wire parser, persistence, or client error handling are implemented.

Record the final validation commands and results, remaining limits, and issue-update proposals in these branch documents.
Prepare and stage the implementation for human review; do not commit package changes without human instruction.
Write `final-commit-message.md` near completion and a `design-record.md` only if decisions remain worth retaining outside the public specs.
