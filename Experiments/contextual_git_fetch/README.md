# Contextual authority at the fetch boundary

A signature says which key signed a commit.
Whether that key was allowed to write in this berth is a separate, local decision.
This probe puts both on the actual CodSync fetch path and watches the one durable ref that fetch can move: the peer pin.

It is a research probe for [#266](https://github.com/benjaminy/small-sea-collective/issues/266).
No runtime module is changed.
No Constitution evaluator is implemented, and no Hub API is approved.

## How to run

```sh
.venv/bin/python Experiments/contextual_git_fetch/probe.py
```

Everything is disposable and local: temporary repositories, temporary `LocalFolderStore` directories, fresh ed25519 keys deleted with the temporary directory, and `GIT_CONFIG_GLOBAL=/dev/null` plus `GIT_CONFIG_SYSTEM=/dev/null` so the machine's Git configuration is neither read nor changed.

## What it builds

`ContextualVerifier` is a small object passed to the existing `CodSync(repo, store, verifier=...)` parameter.
`CodSync.fetch` calls its `verify_history` through `_require_verified` before moving the pin.

Evidence comes from `Repo.signature_report` with an **intentionally empty** allowed-signers file.
A valid signature by a key the caller has not recognized reports `U` with its fingerprint instead of collapsing into a rejection.
`B` fails as `SignatureInvalidError` and `N` as `UnsignedCommitError`.
Only `RepoError` and `OSError` from the report call become `VerificationUnavailableError`; a programming error inside the verifier propagates as itself rather than being relabelled as verification unavailable.

Policy is a separate explicit simulated input, a `PolicyView` holding a fingerprint-to-(berth, purpose) grant map and a finite set of exact previously accepted commit ids.
The caller supplies one fixed berth and purpose for the repository being fetched; commits do not carry or select that scope.
A commit named by a receipt is accepted as itself.
Otherwise an unknown fingerprint raises `AuthorityPaused` and a key grant that does not match the caller's fixed scope raises `AuthorityRefused`.
An unknown authority pauses; it never becomes a bad signature.

The verifier deep-copies the policy view on entry and decides against that copy, then compares the live view id against the captured one before returning.
It appends a record holding the captured view id, a verified commit-to-fingerprint map, and the outcome.
That map omits the Git status, commit bytes, signature, allowed-signers configuration and diagnostics.
It appends that record on policy failures as well as successes, so an unknown authority does not erase the signature result already established.
Those records live in memory in the verifier object for the length of the run.
The pin itself holds a commit id and nothing else: no view id, no evidence, nothing durable or atomic tying policy to the ref write.

## Predictions, written before running

- Authorized scoped work advances the pin.
- Unknown authority, a key whose grant does not match the caller's berth, an invalid signature, unsigned work, a missing allowed-signers file, and later work by a removed key each leave the pin unmoved.
- Unsigned work fails as `UnsignedCommitError`, not as unavailable; the signed authorized case is the control that the same path accepts.
- A missing allowed-signers file fails as `VerificationUnavailableError`.
- A finite exact accepted ancestor can coexist with current authorized work: the head advances even though one ancestor's signer no longer holds a grant.
- A policy change that lands while the verifier is still deciding is caught by the final view-id check: `PolicyViewChanged`, pin unmoved.
- A policy change that lands after the verifier returned and before the pin moves is not caught: the pin advances anyway.

## The race

The injection now sits outside the verifier.
`fetch_case` wraps the local `Repo.advance_ref` with an experiment-only function that runs the policy change and then delegates to the real `advance_ref`.
That point is after `_require_verified` returned and before the ref write, which is exactly the boundary in question.
The runtime modules are untouched.

Two injections, two outcomes:

- **Before the verifier returns** (`policy_change_before_return`): the live view id no longer matches the captured one, the verifier raises `PolicyViewChanged`, and the pin stays where it was.
- **After the verifier returns, before the pin moves** (`policy_change_after_return`): the pin advances under captured view `v3` while the live view at pin movement is `v4` with no grant at all.

Checking the view inside `verify_history` does **not** prevent the second case.
`_require_verified` is one call that returns a value; the ref write is a later, separate step, and nothing carries the view forward or rechecks it.

Two readings of what acceptance means:

1. **Acceptance under a captured view.** The verifier's in-memory record says that the captured view `v3` authorized this head. The probe does this. It is honest, and it does not claim `v3` still held when the pin moved. The pin does not carry the view id; only the record does, and only in memory.
2. **A guarantee that the latest view still holds when the pin moves.** The existing hook cannot provide this, and the counterexample above is the proof.

Reading 2 would need coordination the current interface does not provide.
One possible design would return the verifier's view identifier and serialize the pin write against view changes, with `advance_ref` failing when that view is no longer current.
The coordination could instead live outside the verifier and ref method, provided it covers both verification and the ref write.
This probe does not implement that.

## Limitations

- Grants, removals and receipts are hand-written local inputs. Nothing here derives authority from a Constitution, and nothing reads a globally current view.
- A removal represented as an absent grant reads as `AuthorityPaused`, not as a refusal. The probe cannot tell "never granted" from "granted then removed"; #263 still owes real signed removal evidence.
- The caller supplies the repository's fixed berth and purpose; the probe does not model a signed work-to-scope binding.
- Moving the transport pin is not app integration and is not key release. It says only where this device's view of the peer's history now points.
- The verifier's records are in-memory only. Nothing durable or atomic attaches policy evidence to the pin, which holds a commit id alone.
- The final view-id check narrows the window; it does not close it. It catches only changes visible before the verifier returns.
- Everything is one store implementation, one linear history, one process, with policy changes injected deterministically. This is not a concurrency model.
