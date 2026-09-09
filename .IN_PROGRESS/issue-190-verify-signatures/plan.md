# Issue 190: Cod Sync commit signatures and acceptance boundary

Branch: `issue-190-verify-signatures`.
Issue: [#190 — Verify Cod Sync link signatures on the fetch path](https://github.com/benjaminy/small-sea-collective/issues/190).
Status: steps 1 through 4 and review corrections implemented and validated 2026-09-09; see `notes.md`.
The remaining GitHub work is in `follow-up.md`.

## Working direction

Use Git SSH commit signatures for durable attribution to a signing key.
Signatures survive re-bundling and re-encryption when the original commit objects are retained.
A signature establishes that a key signed the commit, not who originally created copied content; Git author text is not authenticated identity evidence by itself.
A link signature can separately attest publication of a collection containing several authors' commits.
Keep existing link signatures in this branch; any removal needs separate justification.

A physical device must use distinct berth-scoped keys across berths.
The same physical device is not one cryptographic principal across those berths.
Do not reuse the current team-device key as the universal app commit-signing key.
This direction conflicts with the device-only, per-team model in `packages/wrasse-trust/README.md` and must supersede it explicitly when the berth-scoped keys and Manager wiring follow-up issue is completed.

The Hub enforces its declared session, key-scope, and authenticated-context checks during upload/download.
Cod Sync verifies commit signatures, ancestry, bundles, and its own acceptance boundary.
Apps own application semantics and integration decisions within framework authorization; the framework cannot force arbitrary apps to verify Git objects or use Cod Sync.
The Manager continues to own membership, key authorization, and other management decisions.
Apps obtain framework trust information through the Hub and do not open Core databases.

## Sequencing

Narrow #190 to the Cod Sync library work below.
Give each deferred deliverable its own GitHub issue, scope, validation, and completion boundary, as drafted in `follow-up.md`.
Those issues are follow-ups to #190, not additional branches required to finish #190.
File and link them when updating #190; close #190 when its library contract and validation are complete, without claiming runtime verification.
Hub publication context can proceed independently.
Berth-scoped keys and Manager wiring depends on #190 and must resolve its bootstrap and authority prerequisites before claiming verified runtime history.

### #190, this branch: Cod Sync acceptance boundary

All four steps and the review corrections are implemented.
Step 2 took the tolerates-untrusted-objects option rather than a scratch repository.

All changes stay inside `packages/cod-sync` and its tests.
The verifier takes an explicit key set and does not know where keys come from.

1. When a signing key is supplied, sign every commit created by supported Cod Sync workflows, covering every creation path in `repo.py` (`commit`, `commit_tree`, `commit_paths`, `merge`), including merge and rollup commits made during publication.
   Use `gpg.format=ssh` with an Ed25519 key supplied to the repo, and record the git version the evidence was produced on rather than claiming a tested version floor.
   Leave signing unconfigured for existing callers until the wiring follow-up supplies keys; a configured signing failure must never fall back to unsigned creation.
   Leave a signing-failed `merge` in progress for retry; add no automatic abort behavior.
2. Add an optional verifier to `CodSync`.
   When present, enforce this acceptance invariant: every commit relied upon by an accepted head has passed the required checks.
   Existing objects are not evidence of verification or acceptance.
   Cover already-present heads, prerequisites, and all merge ancestry in both `fetch` and publication's stored-head observation.
   `_import` alone is insufficient because `_already_satisfied` and the predecessor walk skip it for existing commits.
   Verify in a scratch repository, or define a verification boundary that tolerates untrusted objects in the repository, so rejection leaves refs and pins unchanged and a retry cannot succeed without verification.
   Distinguish cryptographic failure, unknown key, and inability to check in the raised error.
   Own the allowed-signers file and effective verification configuration; `%G?` alone cannot classify configuration or tool failures (see `notes.md`).
   Accept only `G`, reject every other `%G?` status including unrecognized ones, and capture the `%GF` fingerprint as evidence of which key signed.
3. Leave the verifier absent at Manager call sites.
   The merged state has verification wired only in tests until the wiring follow-up supplies keys; document this limitation on the Cod Sync constructor and in its README.
4. Leave `extensions.signatures`, `signed_link`, and `verify_link_signature` as they are.
   The spikes support signature preservation, not link-signature redundancy; track any later change separately.
   Keep schema/version markers in place.

Follow-up scopes and validation are in `follow-up.md`.
The provisional removal policy and bootstrap transcript requirements remain in `notes.md`.

## Validation: evidence for a skeptical reviewer

Micro tests in `packages/cod-sync/tests` with fresh Ed25519 keys; no Hub, Manager, or cloud services.
Pair each rejection with a passing control so failures prove the intended boundary.

- With signing configured, every creation path in `repo.py` produces a commit that `git verify-commit` accepts against an allowed-signers file naming that key.
- Isolate git config in every micro test (`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` at `/dev/null`) and assert that each negative control has the signature state it is supposed to have.
  Without this a developer's own signing config silently signs the unsigned control.
- Reject an unsigned commit, a signed commit with changed content, and a commit signed by an unknown key, with distinct errors, before refs or pins move.
  Verify merge-side ancestry and pre-existing unsigned objects, not only the head.
- Reproduce the verification-config matrix in `notes.md`, distinguishing setup/tool failure from an intentionally empty or nonmatching key set.
- Reject a merge commit signed by an unknown key whose merge tree adds a change its signed parents do not contain, and pair it with a control whose merge signer is recognized.
- Reject a head whose required ancestors are not present, so that commits which happen to verify cannot stand in for a complete history.
- Reject a bad bundle, then retry the same fetch and the same publication observation with its objects still present.
  Neither retry may bypass verification through `_already_satisfied` or an existing predecessor.
  Also cover failure after earlier chain entries succeeded and assert no upload after failed initial publication observation.
- A bundle with commits by two authors verifies each author separately, and an onboarding-style rollup still verifies the original authors' commit signatures.
- With signing and verification unconfigured, existing fetch and publish tests pass unchanged, and the Cod Sync API documentation states the limitation.
  With signing configured but unavailable or failing, commit creation fails without creating an unsigned replacement.
  Assert divergent fixture tips and expected merge parents.
  Cover the merge case end to end: the merge fails, a retry with signing still required also fails and creates nothing, and the retry succeeds once the key is available.
- Existing link-signature tests in `test_format.py` and `packages/small-sea-manager/tests/test_signed_bundles.py` continue to pass.

Run Cod Sync and Manager micro tests after the edits.
Review validation: 643 passed in the full run; all 25 final verification cases passed separately, including two added after full-suite collection.
The signing evidence is in `packages/cod-sync/tests/test_signing.py` and the acceptance boundary in `tests/test_verify.py`.
Review that no policy about key sources or membership leaked into `packages/cod-sync`.

## Explicitly deferred

All follow-up issues listed above are outside #190's completion scope.
Neither valid signatures nor authenticated context prove that a provider returned the latest state.
See `follow-up.md` for issue plans and documentation promotion.

## Review correction validation

Reject shallow history and inspect original commit objects despite replacement refs.
Propagate invalid `commit.gpgsign` values before `commit_tree` creates an object.
Validate supplied public keys and classify signer-file/tool failures as verification unavailable.
Exercise both fetch and publication rejection/retry over incremental history, with and without an existing predecessor, asserting unchanged refs and storage on every rejection.
Retain passing controls and rerun the Cod Sync and Manager micro tests under isolated Git configuration.
