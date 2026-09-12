# Investigation and decisions

## Accepted handoff — 2026-09-10

The user accepted the recommendations after the experiment report and plain-language discussion, then requested an implementer handoff.
The current [plan](plan.md) is authoritative for implementation scope.
The investigation sections below preserve the reasoning and earlier alternatives; statements there describing decisions as provisional or open are historical.

- Implement ordinary-team publication authentication using today's Sender Keys construction.
  The context contains purpose/version, team, berth, and exact decoded logical path; the authenticated header contains device, chain, and iteration.
  Keep device ownership separate from object context and use the locally accepted team Core `team_device` projection.
- Missing ownership evidence pauses only the dependent authenticated acceptance, including a new invitee's own read.
  Existing progress whose prerequisites are satisfied can continue, and retry can be manual.
  No provisional plaintext consumer or general recovery system is included.
- Preserve NoteToSelf's existing passthrough contract as separate scope.
  Do not add its missing group-key provisioning or invent an ownership mapping to fit the ordinary-team API.
- Own, peer, and encrypted retained-candidate reads receive the publication guarantee.
  Passthrough, explicit proxy, bootstrap, runtime distribution, and signals keep explicit separate contracts and known limits.
  Runtime expected-source and prekey-consumption defects remain follow-ups rather than expanding this implementation.

The implementer may choose the final encoding, API structure, and error-code names within the plan's invariants.
The prototype is experimental evidence and does not constrain those APIs.
A discovery requiring different scope or a weaker guarantee must be recorded with the blocked operation and brought back for a decision.
No package implementation or new validation run was performed while preparing this documentation handoff.

## Initial assessment

This is a real boundary defect with a bounded central fix and a few consequential design choices.
It does not inherently require redesigning the Constitution, admission, Git signatures, or all storage crypto.
The broadest question is whether to settle the stored-object encryption model first (#264), not how many extra arguments today's functions need.
The current code is evidence of affected paths, not the specification of the desired protocol.

The intended claim is that a recognized publisher produced an object for a particular context.
A signature does not prove that publisher performed the provider upload, that the provider served the object from its original location, or that the object is the newest version.
Copying identical bytes preserves their signatures.
Freshness and provider equivocation remain separate from context binding.

## Source findings

- `packages/cuttlefish/cuttlefish/group.py`: `group_encrypt` signs IV plus ciphertext and uses only `group_id` as AEAD associated data.
  `group_decrypt` verifies that signature, but does not bind the message's device ID or chain ID to the supplied record.
  It constructs replacement records rather than persisting them itself.
- `packages/small-sea-hub/small_sea_hub/crypto.py`: `decrypt_group_payload` selects a peer record using session team plus the envelope's device ID, with no expected teammate, berth, or path argument.
  It computes a replay key before signature verification and saves receiver state only after `group_decrypt` returns successfully.
  Both upload and download retain message keys for later reads, the mismatch already tracked in #264.
- `packages/small-sea-hub/small_sea_hub/backend.py`: ordinary own, peer, and candidate-inspection reads all call that same context-free helper.
  Peer transport selection already has the expected teammate and session berth, but those expectations are not passed to crypto.
- `packages/small-sea-manager/small_sea_manager/provisioning.py`: `get_trusted_device_keys_by_teammate` shows an existing certificate-based association mechanism to investigate, not a trust source this branch has established as sufficient.
  Sender-key records themselves do not contain a teammate association.
  The team Core schema also has a direct `team_device` mapping from `device_key_id` to `teammate_id` and `public_key`.
  Implementation should identify the authoritative local projection rather than introduce another ownership model.
- `packages/small-sea-hub/spec.md`: the encryption section still says encryption is not implemented.
  Replace that stale description with the actual agreed contract as part of implementation, without turning this into a general spec cleanup.

These were source-inspection findings at kickoff.
The later local experiments below distinguish reproduced behavior from remaining assumptions.

## Route inventory

| Route | Current behavior | Contract to settle |
| --- | --- | --- |
| Ordinary encrypted own upload/read | Uses session team crypto; upload receives a path but does not authenticate it. | Expected local publisher, team, berth, and logical path; allow appropriate sibling-device publishers on reads. |
| Ordinary encrypted peer read | Resolves teammate-plus-berth transport, then accepts a decryptable team sender. | Bind that expected teammate to the authenticated device/chain and bind session team, berth, and requested path. |
| Retained candidate inspection | Manager-only saved-route read; decrypts in encrypted mode while bypassing the placement pause. | Same publication expectations as an own-berth read, even for an old candidate location; reading must not imply integration or route endorsement. |
| Passthrough own/peer/inspection | Returns raw bytes. | Decide whether this explicitly unsafe research mode remains outside publication authentication; document and test its limit. |
| Explicit `/cloud_proxy` | NoteToSelf-authorized raw read from an explicit remote descriptor for invitation acceptance. | Session identifies transport permission, not the remote team's publisher; identify the invitation/bootstrap consumer's trust boundary. |
| `/bootstrap/cloud_file` | Raw read using a descriptor-bound bootstrap token; no ordinary berth session. | Explicit bootstrap transport contract and independently anchored consumer validation; do not invent identity from the fetched bytes. |
| Runtime artifact own/peer reads | Bypass group encryption to deliver sender-key redistribution. | A separate artifact protocol must supply its own authenticated expectations; group encryption would make first-key delivery circular. |

Runtime redistribution already signs a body containing team, sender device, chain, target device, and its encrypted messages.
Its receiver checks team, target, signature, and whether the signing device is trusted somewhere in the team.
The inbox constructs a path from the expected sender and recipient, but passes only payload and team into `receive_sender_key_distribution`.
That receiver does not receive the requested teammate/path as expectations, and its signed body contains no berth or path field.
Thus its existing signature is not evidence that it implements #261's ordinary publication contract.
Inspect its state-write ordering too if this branch strengthens that consumer: it consumes a one-time prekey before later decrypted-distribution checks.

Signals also use raw provider reads.
They are notification hints rather than authenticated object acceptance; avoid extending a claim about publication bodies to `signals.yaml` without a separate decision.

## Writer lookup clarification

The user's intended model is a lookup in the relevant Core database to determine who a device belongs to.
That is sufficient as the ownership boundary for this branch: authenticate the sending device, resolve its teammate in the locally accepted Core view, and require that teammate to match the expected publisher.
Two sibling devices resolve to the same teammate without special publication semantics.
The earlier notes overstated this as an open identity-design problem.

This lookup consumes the framework's accepted identity state; it does not reestablish bootstrap trust or decide membership on every download.
Unknown ownership and contradicted ownership are different cases.
Unknown ownership may result from delayed Core synchronization or from an invalid claim; the reader cannot yet distinguish them.
Treat it as an unresolved prerequisite alongside a missing sender key, without requiring automatic retries.
A device whose Core-resolved teammate disagrees with the expected publisher is a rejection.
Ambiguous ownership remains visible for human resolution.
Provisional consumption can be worthwhile if a specific consumer can preserve uncertainty and recover from later contradiction; an attached device identity alone does not establish that recovery path.
Keep ordinary authenticated success tied to the expected publisher, and define provisional consumption separately if it earns its implementation cost.
The remaining cryptographic obligation is to bind the payload to the actual device identity used in that lookup, including the sender-chain association with today's construction.
Selecting the right existing projection and handling NoteToSelf's representation are implementation questions.

## Candidate context design, not a decision

Prefer a small, versioned publication-context value derived at the Hub boundary.
Keep the provider descriptor outside that value unless an actual requirement calls for physical-location binding.
A logical berth-relative path can remain stable when an authorized object is copied between storage locations, including candidate inspection and relocation.
Specify its exact encoding and path rules once; do not inherit S3, Dropbox, or Drive naming quirks as protocol rules.

Treat the teammate as the expected publisher scope and the device/chain as the cryptographic signer, using the Core ownership lookup described above.
Checking only the current device would break valid sibling reads.
Accepting every known sender in the team would preserve the reported unexpected-writer gap.
A publisher ID inside a signed payload does not override Core's device ownership mapping.
Missing or ambiguous associations can block the affected local read visibly until the person resolves them; no automatic winner is needed.

One plausible implementation authenticates the canonical context and complete crypto header with the sender signature and uses the agreed context/header as AEAD associated data.
The ordinary authenticated-read verifier must incorporate the caller's expected context before returning plaintext or committing receiver state.
Adding labels only to JSON, or signing only the old IV/ciphertext bytes, is insufficient.
Comparing signed context after the AEAD opens the ciphertext is not insufficient in this threat model: every reader already holds the team keys, so an early-opened plaintext that is never returned discloses nothing new.
Propose clarifying the issue's "not a comparison after decrypt" wording: the signature binds context, header, and ciphertext together, and neither plaintext release nor receiver-state commit precedes the successful context check.
This permits internal AEAD opening before comparison without treating it as successful publication acceptance.
Associated-data binding remains the preferred construction because a forgotten check then fails closed, but it is a preference, and a stored-object envelope chosen under #264 may satisfy the requirements differently.
Recommendation for now: prove the substitutions and define the context, then use today's construction if the change remains bounded to the transcript/API and state-commit boundary.
Keep record team identity separate from the new opaque context argument.
Reconsider sequencing under #264 if implementation needs new retention, back-fill, or recovery machinery.
Reusable context semantics and fixtures justify this sequence; preserving today's envelope format does not.

Path recommendation: bind the UTF-8 encoding of the logical string received by the Hub after JSON or query-parameter decoding, and require the reader to present the same string.
No canonicalization, no alias rules; a different string is a different path.
This chooses a small protocol rule and keeps provider naming quirks out of the authentication contract.
Check that upload and download transport preserve that rule with a few encoding cases; provider aliases can still cause availability problems without making different authenticated paths interchangeable.
Settle the transcript and domain separation before coding, including which metadata is checked before any iteration-driven derivation.

Alternatives worth comparing are a context-aware Sender Keys envelope now and a stored-object envelope after #264.
Per-berth key provisioning alone cannot prevent substitution between paths in the same berth, so #266 is not an alternative to publication context.
Conversely, binding berth IDs here does not deliver #266's distinct keys or its trust interface.

## Revision: progress with a concrete recovery path

The user clarified that progress before every requirement is satisfied can be a good Small Sea tradeoff when a mistake or attack has a clear recovery path.
That changes the assessment of retrospective ownership checks: they are not inherently the wrong design, but they need a consumer contract that explains what happens before and after confirmation.
The earlier critique was too categorical in rejecting that option for useful work, although ordinary authenticated publication still needs an unambiguous meaning.

Evaluate a proposed provisional operation by naming the useful work, the uncertainty shown to the person, the evidence retained, the trigger for rechecking, and the exact repair after contradiction.
Manual repair is acceptable; a generic promise to roll back later is not a recovery procedure.
An isolated candidate history is a plausible example if the consumer can keep independent edits and restore its previous accepted state without losing evidence.
An external action or disclosure cannot be undone merely by restoring history.
No consumer implementation establishing these properties has been demonstrated in this investigation.

The proposed default therefore allows fetching and existing isolated inspection workflows to proceed, while only dependent acceptance waits for evidence.
Provisional use beyond that remains an option when a named consumer and a local recovery demonstration justify it.
Do not create general rollback machinery merely to avoid a small local pause.

Research implementation can also progress without closing #264 or repairing every raw-route consumer.
Keep those limits explicit and reuse context fixtures across constructions.
Validation should establish the guarantee and any claimed recovery, with shared transcript checks and thin route checks rather than an exhaustive cross-product.
Error translation, authenticated metadata, and a small path-encoding check remain necessary evidence; broader provider surveys and unrelated protocol fixes can wait.

## Revision: separate object context from ownership

Recommendation: keep the context minimal — format version and purpose, team ID, berth ID, and logical path — and authenticate device ID, chain ID, and iteration in the header.
This separates object coordinates from the independent association between the signing device and its owner.
Sibling reads remain natural because the reader need not guess which device wrote an object.

The earlier rationale claimed that a larger context would hide the ownership mechanism from every useful experiment.
That claim was incorrect and has been replaced after the local prototype demonstrated the counterexample.
A recognized sender can sign a context falsely claiming another publisher's ID; matching the reader's expected publisher label does not make the claim true.
The ownership lookup rejects that object even with matching context, so both context designs can test the association boundary.
Including publisher identity offers an additional substitution check, but does not make ownership lookup optional.

Make the publisher expectation required at protected entry points to prevent accidental omission, and test that the verifier enforces it.
A required argument does not establish the enforcement by itself.
The field-set recommendation remains provisional; the experiment supports the separation without proving it is the only acceptable format.

## Revision: the ownership projection is a design question, not wiring

The writer-lookup section defers the authoritative local projection and NoteToSelf's representation to implementation.
Source inspection says that deferral does not survive.

- `packages/small-sea-hub/small_sea_hub/backend.py`: `_device_public_keys_by_key_id` selects `device_key_id` and `public_key` from `team_device` and drops `teammate_id`, which `packages/small-sea-hub/tests/test_session_flow.py` shows the table carries.
  It also guards on `_table_exists` and returns an empty mapping when the table is absent.
- The same file's `_team_db_path_for_session` special-cases `team_name == "NoteToSelf"`.
  Own team-device public keys live in shared NoteToSelf `team_device_key`; secret references are device-local.
  The initial description of that public-key table as device-local was incorrect.

So device ownership has at least two possible answers today depending on session kind, and one of them can be missing entirely.
The consequence is not cosmetic.
This branch specifies unknown ownership as a retryable prerequisite whose expected cause is delayed Core synchronization.
If the projection is simply unpopulated for a session kind, then every read on that path returns retryable-pending on a healthy system, with nothing to wait for and nothing to retry.

Treat an absent projection as a distinct named outcome rather than folding it into missing evidence.
Do not add a fallback that treats an empty mapping as permission to skip the lookup; the existing `_table_exists` guard is a research-stage shortcut, not evidence of the intended design.
The cheapest way to settle which store is authoritative is to build the own, peer, and NoteToSelf fixtures first and record what each session kind actually contains, rather than choosing on paper.

## Revision: #264 sequencing, with the bounded-change test applied

The kickoff made proceeding on today's construction conditional on the change staying inside the transcript, the API, and the state-commit boundary.
Source inspection satisfies that condition.

- `packages/cuttlefish/cuttlefish/group.py`: `group_encrypt` signs `iv + ciphertext` and passes `group_id` as associated data, and `group_decrypt` verifies that signature.
  Adding a context argument, extending the associated data to cover context and header, and extending the signed transcript to match are local to those two functions.
- `packages/small-sea-hub/small_sea_hub/crypto.py`: `decrypt_group_payload` already calls `save_peer_sender_key` only after `group_decrypt` returns, so deferring the receiver-state commit past the context check is a reordering rather than new machinery.
- Nothing here needs retention, back-fill, or migration work, which was the stated trigger for handing the question to #264.

Proceed on today's construction.
The reason is not that it is the low-risk envelope.
It is that the context semantics and the substitution fixtures are the durable research artifact, and today's envelope is the cheapest substrate on which to run them.
A later stored-object envelope should inherit the fixtures, not the bytes.

## Boundaries with existing issues

- [#190](https://github.com/benjaminy/small-sea-collective/issues/190) supplies Cod Sync signing and verification seams; its accepted scope explicitly leaves this Hub boundary separate.
- [#262](https://github.com/benjaminy/small-sea-collective/issues/262) owns the bootstrap trust transcript.
  This branch must name that limit rather than claim downloaded Core establishes its own authority.
- [#264](https://github.com/benjaminy/small-sea-collective/issues/264) questions Sender Keys for repeatedly readable storage.
  Do not silently settle it by preserving the current ratchet API or expanding retained-key machinery.
- [#266](https://github.com/benjaminy/small-sea-collective/issues/266) owns berth-scoped signing keys and runtime verifier wiring, including the evidence recognizing keys.
  This branch consumes Core's accepted device ownership mapping; it need not implement #266's broader trust interface to perform that lookup.
- #190 also names #263 for removal/reconsideration and #265 for provider rollback/equivocation.
  Their full issue bodies were not reviewed in this initial pass; do not infer detailed policies from those titles.

At this stage of investigation, the context field set and route contracts remained recommendations rather than a completed implementer handoff.
The plan records proceeding on today's construction without waiting for #264.
The user subsequently authorized local experiments and prototype coding; the results below narrowed the ownership question without assuming a NoteToSelf redesign.
The accepted handoff at the top of this document records the later scope decision.

## Executed experiments — 2026-09-09

See [the reproducible experiment report](experiments/README.md) for commands, per-experiment observations, and limits.
The isolated experimental micro tests pass 26 cases, and the existing Cuttlefish/Hub group micro tests pass 12 cases.
Package code has not changed.

The current Hub returns plaintext through real HTTP routes after substitutions across paths, app berths, and expected writers when the provider bytes are replaced by a local stub.
A separately provisioned team's read already rejects through missing sender-key state; no universal cross-team success is claimed.
The current chain-ID label can be changed without invalidating a read, and Hub's retained-key calculation runs before signature verification.
The latter probe used iteration 8, preserved persisted receiver state, and successfully read the original afterward.

The prototype explicitly authenticates context and header, verifies the established record's device/chain association, checks Core-derived ownership, and returns detached candidate state.
It rejects context substitution even with the key available, distinguishes wrong ownership from missing evidence, and exercises failures at current, future, and retained iterations.
A valid signature over bad ciphertext still fails AEAD without changing input state.
Real sibling linking and sender-key redistribution provide the chain signing-key/device/teammate association for successful prototype reads in both directions.
This is evidence for that association path, not an audit of all bootstrap trust.

The ownership fixtures reveal two separate questions that the original plan combined:

- An ordinary invitee has a local sender key before its fetched proposal snapshot has its own accepted `team_device` row.
  The inviter's Core contains the invitee after finalization, but that evidence has not yet arrived in the invitee's snapshot.
  This is a normal intermediate state, so a missing-ownership outcome is not automatically a broken projection.
  Recommendation: let the dependent own acceptance wait for that evidence, while reads from the already recognized inviter can still proceed.
  The experiment does not implement that synchronization or make local key possession substitute for accepted admission.
- Fresh NoteToSelf has a shared `user_device` signing key, no `team_device` table, no corresponding `team_device_key` row, and no group sender key.
  Encrypted upload preparation fails even though an encrypted session can be opened.
  Source inspection of `TeamManager._open_note_to_self_session` and its callers confirms the existing passthrough default/use.
  Recommendation: explicitly scope that existing path as a separate contract for this branch, unless the user chooses to add encrypted NoteToSelf publication and its provisioning lifecycle.
  An absent-projection error cannot manufacture that missing lifecycle.

HTTP decoding preserves exact logical strings for literal percent escapes, plus signs, spaces, and composed/decomposed Unicode in the tested cases.
This supports the proposed decoded UTF-8 rule; it does not survey provider aliases.

Remaining implementation work includes final context/wire encoding, protected-route integration including retained candidates, consistent API/client failure translation, and persistence ordering under the actual Hub call path.
The prototype deliberately supplies no database transaction, wire parser, concurrency guarantee, new retention mechanism, or provisional consumer workflow.

## Implementation — 2026-09-10

Package implementation is complete and staged for human review.
The settled scope in [plan.md](plan.md) held; nothing in it needed reopening.
No new retention, back-fill, or recovery machinery was required, which was the stated trigger for handing sequencing back to #264.

### What was built

`packages/cuttlefish/cuttlefish/group.py` gained an opaque `context` argument on `group_encrypt` and an `expected_context` argument on `group_decrypt`, plus a `context` field on `GroupMessage`.
The associated data is now a length-prefixed encoding of a domain tag, the group id, the context, the sender device key id, the chain id, and the iteration; the signature covers a second domain-tagged transcript over that associated data, the IV and the ciphertext.
Length prefixes rather than concatenation, so no field boundary can be moved.
Cuttlefish still interprets nothing about the context.

`group_decrypt` now runs its whole decision before touching the chain: iteration range, signature, header-versus-record agreement (`SenderHeaderMismatch`), then context equality (`ContextMismatch`), then derivation and AEAD.

`packages/small-sea-hub/small_sea_hub/crypto.py` owns the context encoding, the envelope format marker, the ownership check, and the failure taxonomy.
`publication_context` is compact UTF-8 JSON of `["small-sea/object-publication", 1, team hex, berth hex, path]`.
The envelope carries `"format": "small-sea/group-publication/1"`; anything else is refused with no fallback.
`decrypt_group_payload` takes `expected_context`, `expected_publisher` and `device_ownership` as required keyword arguments.

The Hub's retained-key calculation moved after `group_decrypt` returns.
`_message_key_for` is pure in the pre-read record, so computing it later derives exactly the same key while nothing iteration-driven runs before authentication.
This turned out to be a two-line move rather than the reordering the plan anticipated.

`backend.py` resolves the context and expected publisher at each protected entry point and reads Core's `team_device` projection as `dict[device_key_id, set[teammate_id]]`, with `None` for an absent table.
Sets rather than single values so an ambiguity cannot disappear while the mapping is built, even though `team_device.device_key_id` is a primary key today and therefore cannot actually carry two rows.
That is deliberate: the projection's shape is the thing that would change if ownership evidence ever came from somewhere with weaker uniqueness, and the check is one comparison.

### Findings during implementation

The `_table_exists` guard on `team_device` was left in place but its meaning changed.
It previously produced an empty mapping that silently weakened `_device_public_keys_by_key_id`'s announcement verification; for publication it produces a distinct `OwnershipProjectionAbsentExn`.
`_device_public_keys_by_key_id` still returns `{}` on an absent table.
That pre-existing shortcut was left alone, but it is worth a focused issue: an absent projection there quietly weakens storage-announcement verification the same way an empty ownership mapping would have weakened publication.

The invitation bootstrap consumer had to change and could not be given the guarantee.
`decrypt_invitation_bootstrap_payload` reads through `/cloud_proxy` during acceptance, when the acceptor has no team session, no Core, and no berth id — the invitation token carries `team_id` but no Core berth id, and `ExplicitProxyStore._transform_download` receives only bytes.
It now parses the bound envelope and passes the envelope's own context as the expected context, with a comment naming exactly what that does and does not establish.
Adding a real expected context there would still leave the ownership half unanswerable until #262, so it was not worth the token and store changes.

Fresh NoteToSelf behaves as the experiments predicted.
`_device_ownership_by_key_id` returns `None` for it, and an encrypted-mode read of passthrough bytes is refused as an unreadable envelope rather than handed back raw.
Its passthrough contract is unchanged.

### Validation

Run from the repository root with the developer's ordinary Git configuration:

```sh
.venv/bin/python -m pytest -q packages tests
```

The `packages/ssc-files` suite fails in a Git-isolated environment (`GIT_CONFIG_GLOBAL=/dev/null`) both before and after this branch — 36 identical failures on either side of the change, all `NoLocalHeadError` from commits that cannot be authored.
Those tests need a real Git identity; the isolated invocation used during the experiments is not a valid way to run them.

New and changed micro tests:

| File | What it establishes |
|---|---|
| `packages/cuttlefish/tests/test_group.py` | Transcript-level binding, once, at the shared boundary: context mismatch with the key available, a validly signed device/chain relabelling, a mutation table over every authenticated field, and that an altered iteration is refused before any `_derive_message_key` call. |
| `packages/small-sea-hub/tests/test_group_crypto.py` | The Hub's publication decision: two devices of one teammate accepted and kept distinct, another teammate's device refused with its key available, unknown device pending, absent projection distinct from an empty mapping, ambiguity refused, cross-path/berth/team substitution refused with a passing control each time, context and ownership refusals told apart, and refusals leaving persisted receiver records and skipped-key maps unchanged at current, future and retained iterations. |
| `packages/small-sea-hub/tests/test_publication_routes.py` | Real provisioning, real sessions, real HTTP, real crypto, with only placement and provider I/O substituted: own, peer and retained-candidate entry points; exact logical path strings including composed and decomposed Unicode; a new invitee's own read pending with no receiver-state change while the recognized inviter's read succeeds, then succeeding once the ownership row arrives; a contradicted association refused; NoteToSelf's separate contract. |
| `packages/small-sea-client/tests/test_client.py` | Neither a pending prerequisite nor a refused publication can reach a caller as success or as absence. |
| `packages/small-sea-hub/tests/test_peer_cloud_file_errors.py` | The peer route's prerequisite codes, now including missing ownership. |

The wire code for a missing sender key changed from `peer_sender_key_unavailable` to `sender_key_unavailable`, because own and candidate reads now report the same condition and two codes for one condition would be worse than renaming one.

### Limits of this evidence

The route tests substitute provider I/O, so they establish behavior after substituted bytes reach the read boundary; they do not demonstrate a provider attack.
Sibling-device attribution is tested at the crypto boundary with a synthetic ownership mapping; the real linked-device fixture that produced the same result lives in `experiments/test_linked_device_bootstrap`-style code and was not carried into the package suite, because it costs a full second installation and two bootstrap rounds to re-prove a comparison the boundary test already makes.
The ambiguity branch is exercised only with a synthetic mapping, since `team_device` cannot produce one.
No MinIO or provider integration run was added; nothing in this change is provider-specific.

## PR review — 2026-09-11

Reviewed implementation commit `2912787` against the accepted scope.
The implementation is committed; the earlier staged-only status above is stale.
Two defects were reproduced with temporary micro tests; no implementation changes were made.

- The Hub renamed `peer_sender_key_unavailable` to `sender_key_unavailable`, but `PeerSmallSeaStore` still recognizes only the old code.
  A missing-key response now raises `StoreProviderError`, so Manager's Core fetch reports `CoreFetchRemoteError` instead of its missing-key prerequisite.
  Update Cod Sync's response handling and cover the new ownership prerequisites for the affected stores as well.
- An otherwise readable envelope with iteration `-1` or `2**64` produces HTTP 500 instead of the documented 502 publication rejection.
  Cuttlefish's new range check raises `ValueError`, which the Hub's publication exception translation does not handle.
  Both reproductions first uploaded and successfully read a valid control, then changed only its iteration.

Validation used `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false` to disable personal Git signing only for the test process.
Running `.venv/bin/python -m pytest -q packages/cuttlefish/tests packages/small-sea-hub/tests packages/small-sea-client/tests packages/small-sea-manager/tests/test_linked_device_bootstrap.py packages/small-sea-manager/tests/test_sender_key_rotation.py` passed 224 checks; 8 failed and 28 errored because the sandbox blocked local-service ports.
Rerunning the Hub's `test_backend_smoke.py`, `test_cloud_api.py`, `test_peer_transport.py`, `test_notifications.py`, and `test_s3_storage.py` with local-service access passed 38 checks, including MinIO coverage.
The notification check remained blocked because `docker ps` failed during fixture setup.
The three temporary review probes failed as expected: two iteration cases and one missing-key classification case.
`git diff --check` passed before this notes update.

## PR review fixes — 2026-09-11

Both review findings are fixed and covered by package micro tests.
Cod Sync recognizes `sender_key_unavailable` and preserves all four ownership prerequisite reasons for own, peer, and retained-candidate reads.
Manager's peer Core fetch preserves ownership prerequisite reasons in `CorePublicationPendingError`, while retaining its existing missing-sender-key exception.
Cuttlefish raises `InvalidIteration` for iterations outside its header range, and the Hub translates that exception into a 502 publication rejection with reason `invalid_iteration`.
The HTTP checks cover negative and overflowing iterations, unchanged receiver state, and successful reads of the original bytes after rejection.

Validation: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest -q packages/cuttlefish/tests/test_group.py packages/small-sea-hub/tests/test_group_crypto.py packages/small-sea-hub/tests/test_publication_routes.py packages/cod-sync/tests/test_store.py packages/small-sea-manager/tests/test_core_peer_fetch.py -k 'not s3' --tb=short` passed 124 checks, with two S3 checks deselected and one existing settings warning.
These fixes change error classification, so this run uses local fixtures without provider services.
They were committed in `4f7e740`.

## Second PR review — 2026-09-11

Reviewed the branch at `4f7e740` and fixed two findings; the fixes are staged for human review and commit.

- Manager's `decrypt_invitation_bootstrap_payload` still computed the retained message key before `group_decrypt` verified the signature, the same ordering this branch had already fixed in the Hub.
  Whoever controls the downloaded bytes could choose the iteration and make the invitee's device walk the chain that many steps before rejection.
  A scratch probe measured 0.5 s at iteration 200,000 and 5.0 s at 2,000,000, both ending in `InvalidSignature`; the time grows linearly, so a 2^40 iteration would effectively hang acceptance.
  The key calculation now runs after `group_decrypt` returns.
  The route remains raw transport; this fixes only the pre-authentication work.
- The Hub checked device ownership before the signature, so altered bytes naming a device with no ownership row were reported as a 409 pending prerequisite rather than a 502 rejection.
  They were never accepted, but a caller would wait for evidence that could not help.
  The ownership check now runs after `group_decrypt` and still before any receiver state is saved; the Hub spec states the order.

New micro tests: `test_bytes_that_fail_authentication_are_refused_rather_than_left_pending` in `packages/small-sea-hub/tests/test_group_crypto.py`, and `test_bootstrap_decrypt_does_not_walk_the_chain_for_a_forged_iteration` in `packages/small-sea-manager/tests/test_invitation.py`.
Both fail against the previous source ordering and pass after the change.

Validation: `.venv/bin/python -m pytest -q -p no:cacheprovider packages/cuttlefish/tests packages/small-sea-hub/tests packages/small-sea-client/tests packages/cod-sync/tests packages/small-sea-manager/tests` passed 908 checks with 2 errors, neither in code this branch touches.
`test_notifications.py` needs a running Docker daemon.
One `test_invitation_route_delivery.py` case errored in fixture teardown when removing its temporary directory (`Directory not empty`); rerunning that file alone passed all 40 checks, and the cause was not investigated.
Do not run the Cod Sync signing suites with the `GIT_CONFIG_COUNT` override used in earlier runs: it disables the commit signing those tests configure, and 34 of them fail for that reason alone.

## Content readiness review — 2026-09-11

Reviewed `c91d5d3` against local `main`, excluding Git and GitHub wrap-up as requested.
No new blocking finding: the ordinary publication boundary, protected route wiring, state-commit ordering, and downstream prerequisite handling meet the accepted scope.
The separate bootstrap, runtime-distribution, NoteToSelf, and stored-object retention limits remain as documented above.

Fresh validation passed 171 micro tests, with two S3 checks deselected and the existing settings warning:

```sh
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false .venv/bin/python -m pytest -q -p no:cacheprovider packages/cuttlefish/tests/test_group.py packages/small-sea-hub/tests/test_group_crypto.py packages/small-sea-hub/tests/test_publication_routes.py packages/small-sea-client/tests/test_client.py packages/cod-sync/tests/test_store.py packages/small-sea-manager/tests/test_core_peer_fetch.py packages/small-sea-manager/tests/test_invitation.py::test_bootstrap_decrypt_does_not_walk_the_chain_for_a_forged_iteration -k 'not s3' --tb=short
```

An initial run without the signing override and including the whole invitation file hit sandbox restrictions on personal GPG signing and local server ports (125 passed, 4 failed, 46 setup errors).
The successful focused run above does not cover provider-service checks or replace the broader suite evidence from the second review.
`git diff --check main...HEAD` also passed.
