# Publication-context experiments

These experiments test the plan's assumptions without changing package code or choosing a supported API.
The user authorized local experiments and prototypes on 2026-09-09.
Run from the repository root:

```sh
.venv/bin/python -m pytest -q --tb=short .IN_PROGRESS/hub-authentication-context/experiments
```

Result: **26 passed in 7.71s**.
The characterization micro tests intentionally pass when a current weakness is reproduced.
Their assertions are observations, not behavior to preserve in the implementation.
All databases, keys, repositories, and simulated cloud objects are created in temporary directories.
Git configuration and author identity are isolated from personal settings by `conftest.py`.
No provider account, external service, or OS notification is used.

## What the experiments established

| Experiment | Observation | Consequence |
| --- | --- | --- |
| Current own/peer HTTP reads, real sessions and crypto, substituted transport bytes | The same Alice publication succeeds at another path, another app berth, and a peer request naming another writer. Correct-path and correct-writer controls also succeed. | Route selection alone does not enforce publication attribution. |
| Current read through a separately provisioned team | The original team's read succeeds; the other team's read raises missing sender key. | Do not claim every substitution already succeeds. The prototype additionally proves context rejection with the same key deliberately available. |
| Current chain-label mutation | An altered chain ID succeeds with the original signature and ciphertext. | Bind header fields and compare them with the independently established record. |
| Current invalid signature with iteration 8 | Hub computes the iteration-dependent message key before signature failure; persisted receiver state is unchanged and the original succeeds afterward. | Move authentication ahead of all derivation, including Hub's retained-key calculation. No large-iteration resource attack was run. |
| Real creation and admission using LocalFolderStore | Alice's Core maps Alice and Bob; Bob's fetched proposal snapshot maps Alice but not Bob. Bob nevertheless already has his local sender key and Alice's received sender key. Alice does not yet have Bob's received sender key. | Ownership and sender-key delivery are separate prerequisites. A pending own read can occur in normal admission, not just under attack. |
| Fresh NoteToSelf provisioning | Shared `user_device` has a signing key, `team_device` is absent, no NoteToSelf `team_device_key` row or group sender key exists, and encrypted-upload preparation fails. | A generic lookup adapter cannot make NoteToSelf an ordinary encrypted team. The subsequent handoff preserves its existing passthrough path as separate scope. |
| Real identity bootstrap, team-device linking, and signed sender-key redistribution | Both devices map to the same teammate. Received records have the real sender's chain ID and signing key, and the prototype accepts publications in both directions. | Ordinary sibling attribution can use Core ownership without expecting a particular writing device. |
| JSON upload and query-parameter download through the real HTTP boundary | Literal percent escapes, slashes, plus signs, spaces, and composed/decomposed Unicode survive as distinct strings. | Exact decoded UTF-8 is a workable context rule at this boundary. |
| Prototype with fresh Sender Keys records | Cross-team, cross-berth, and cross-path contexts fail even with the signing/decryption key available. | Context adds an independently demonstrated boundary beyond key availability. |
| Prototype with a validly signed false publisher claim | Ownership rejects the wrong expected publisher both with the small context and with a larger context carrying the false publisher label. | Including publisher identity does not make the ownership check untestable or unnecessary. |
| Prototype header tampering and validly signed device relabelling | Tampering fails before derivation; even a valid signature cannot override the device identity in the established sender record. | Authenticating a label and establishing its association are different obligations. |
| Prototype current, future, and retained iterations | Missing, conflicting, and ambiguous ownership and validly signed bad ciphertext release no result and leave input state unchanged. Correct evidence and original ciphertext subsequently succeed. | Returning detached candidate state makes the acceptance boundary explicit without designing rollback machinery. |

## Prototype limits

`publication_prototype.py` is a 108-line experimental verifier over the current Sender Keys KDF.
Its context encoding and signature/AEAD header are explicit, but its in-memory envelope is not a proposed public serialization.
It has no route integration, untrusted wire parser, database transaction, resource budget, or concurrency guarantee.
The ratchet/key-retention helper deliberately reproduces the present readable-key behavior; it does not answer #264.
The caller supplies an independently established sender record and locally accepted ownership evidence.
The sibling fixture exercises one real way of obtaining those inputs; it is not an audit of every bootstrap or distribution path.

The HTTP substitution probe replaces placement/provider operations, including peer transport selection.
It proves the behavior after substituted bytes reach the protected read boundary; it does not prove a provider attack or audit authorization of the requested route.
The path probe replaces backend upload/download methods and establishes HTTP decoding only.
Retained-candidate routing, API/client translation of the new failures, and persistence of prototype candidate state are not implemented or validated.
The prototypes distinguish missing projection from missing association in their messages; final API error codes are not selected.

The admission fixture uses actual provisioning, including the existing test-only route-less acceptance return, and does not insert ownership or key rows itself.
It does not validate Manager courier/route preparation or admission trust anchoring.
The sibling fixture copies an existing Core snapshot as local transport, then uses real linking and redistribution; it does not manufacture its device associations.
The unknown-to-known ownership prototype changes supplied evidence explicitly; automatic Core synchronization or repair is not claimed.

## Baseline controls and execution notes

Existing Cuttlefish and Hub group micro tests also passed:

```sh
env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME='Publication experiment' GIT_AUTHOR_EMAIL=experiment@example.invalid \
  GIT_COMMITTER_NAME='Publication experiment' GIT_COMMITTER_EMAIL=experiment@example.invalid \
  .venv/bin/python -m pytest -q --tb=short \
  packages/cuttlefish/tests/test_group.py packages/small-sea-hub/tests/test_group_crypto.py
```

Result: **12 passed in 0.47s**.
The first experimental run could not create fixture commits because personal Git configuration requested GPG signing.
The fixture now isolates Git settings; no personal configuration was changed.

## Recommendations accepted for handoff — 2026-09-10

The user accepted this direction; [the implementation plan](../plan.md) now records the settled scope and validation requirements.
The results above remain the dated experimental evidence, not a report of completed package implementation.

1. Use the small context because it separates object coordinates from the device-to-owner association, not because larger contexts cannot test ownership.
2. For ordinary teams, use accepted Core `team_device` ownership and keep missing sender keys separate from missing ownership.
   A new invitee's own dependent reads wait for accepted ownership evidence to arrive; possessing its own private key is not evidence that its admission is accepted.
3. Treat existing NoteToSelf passthrough as a separate contract in this branch; its missing encrypted-publication lifecycle is deferred.
   Do not synthesize a teammate association merely to make a uniform interface succeed.
4. Implement authentication before derivation and return or commit receiver state only after successful context and ownership checks.
   Carry these fixtures into the final shared boundary, then add protected-route wiring, retained-candidate, and failure-translation checks.

## Superseded by package micro tests — 2026-09-11

The package micro tests below now carry the evidence these experiments gathered.
The experiments remain as dated evidence of the weaknesses before implementation; their characterization probes are not behavior to preserve.

| Experiment | Replacement |
| --- | --- |
| Own/peer HTTP substitution | `packages/small-sea-hub/tests/test_publication_routes.py`: own, other-berth, peer, and retained-candidate reads |
| Chain-label mutation, header tampering, device relabelling | `packages/cuttlefish/tests/test_group.py`: the mutation table and the relabelling check |
| Derivation before signature failure | `packages/cuttlefish/tests/test_group.py` and, for the invitation consumer, `test_bootstrap_decrypt_does_not_walk_the_chain_for_a_forged_iteration` in `packages/small-sea-manager/tests/test_invitation.py` |
| Admission fixture: invitee lacks its own ownership row | `test_a_new_invitee_waits_for_its_own_accepted_ownership_row` in `test_publication_routes.py` |
| Fresh NoteToSelf | `test_note_to_self_keeps_its_separate_passthrough_contract` in `test_publication_routes.py` |
| Path strings through HTTP | `test_exact_logical_path_strings_survive_the_http_boundary` in `test_publication_routes.py` |
| Prototype context substitution, false publisher, missing/ambiguous ownership, state at current/future/retained iterations | `packages/small-sea-hub/tests/test_group_crypto.py` |
| Real sibling linking and redistribution | Not carried over; `test_group_crypto.py` checks sibling attribution with a synthetic ownership mapping (see [notes](../notes.md#limits-of-this-evidence)) |
