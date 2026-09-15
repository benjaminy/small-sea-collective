# Bootstrap transcript review packet

The proposed design is in [the transcript](../../Documentation/bootstrap-trust.md).
This packet maps its claims to executable evidence; the models do not implement the runtime bootstrap APIs.

## Selected rules

Invitation begins with a complete, independently authenticated exchange, including the fresh request and exact snapshot commitment.
The proposed design requires an explicit local adoption of the offered authority anchor; a fetched graph cannot choose it.
The delivery model receives a simulated decision naming the exact response, anchor, policy, team and device, and rejects missing or mismatched decisions.
That exercise establishes record binding, not proof of human consent.
Sibling identity join uses retained signed delegation where it survives and permits a separately recorded, scoped local continuity decision when it does not.
That local decision preserves missing or disputed history and grants no team authority.
Per-team delivery uses the already recognized identity keys to authenticate a complete request and response, with a fresh human comparison retained as an alternative.
Manager records the commitment before fetching through the Hub, then checks the sibling's team enrollment authority under the explicitly adopted team anchor.

## Cases and evidence

| Claim or attack | Executable evidence | Limit |
| --- | --- | --- |
| Valid invitation and substituted whole Core view | `bootstrap_exchange`: `valid`, `self_consistent_substitution`, `naive_self_consistency_baseline` | Single-anchor grant model; full-digest comparison, not wire protocol. |
| Authenticated false projection | `bootstrap_exchange`: `authentic_false_projection`; both `bootstrap_team_delivery` routes: `false_projection` | Preserves delivered and reconstructed values; no runtime reconstruction engine. |
| Missing comparison, wrong attempt, wrong snapshot bytes | `bootstrap_exchange`: `missing_comparison`, `another_attempt`, `changed_bytes`; corresponding team cases | Local generated artifacts. |
| Lost first device, retained delegation | `bootstrap_identity`: `da_first_device_lost_chain_retained` | Verification uses public evidence; physical key erasure is not simulated. |
| Missing identity chain and fresh local recognition | `bootstrap_identity`: `da_chain_absent`, `sd_chain_absent_human_recognition` | Decision is local; operator signature does not prove human presence. |
| Compromised sibling, indistinguishable returning claimant | `bootstrap_identity`: `da_compromised_authenticated_sibling`, `paired_claimant_and_stolen_key` | Same signed input cannot distinguish person from thief. |
| Conflicting successors and wrong identity scope | `bootstrap_identity`: `da_conflicting_successors`, `da_scope_misuse`, `sd_scope_misuse` | Linear successor model; not all event-DAG conflicts. |
| Human decision reused for another snapshot | `bootstrap_identity`: `sd_decision_for_another_exchange` | Exact-exchange binding prevents widening a local decision. |
| Team delivery from empty store | Both `bootstrap_team_delivery` routes: `valid`, `unavailable_snapshot` | Commitment and simulated local anchor decision retained before fetch; no proof of human consent, ciphertext or runtime clone. |
| Wrong team, attempt, fresh key, baseline, identity-to-team binding | Both team routes: matching named rejection cases | Identity recognition is a prior local input, not derived here. |
| Identity-linked sibling lacks team enrollment power | Both team routes: `no_team_enrollment`, `wrong_grant_team`, `wrong_grant_berth`, `wrong_grant_purpose` | One anchor-signed grant and sibling-signed enrollment, not a full team policy. |
| Removed-author history accepted without future power | `bootstrap_history`: `removed_before_join`, `explicit_finite_acceptance`, `same_key_later_work` | Signed work records name a basis; #266 must define the actual Git authority binding. |
| Missing authority ancestry, changed view, wrong berth, invalid signature despite override | `bootstrap_history`: corresponding cases | Special case of one grant and one removal child. |
| Missing old content versus missing commit ancestry | `bootstrap_retention`: removes/restores old blob, old tree, parent commit | Actual SshCommitVerifier; no retention transport implementation. |
| Undisclosed events | `bootstrap_exchange`: `authentic_undisclosed_grant`; team probe holds an independent omitted grant | Successful authentication cannot establish global completeness. |

## Reproduce

Run from the repository root:

```sh
.venv/bin/python Experiments/bootstrap_exchange/probe.py
.venv/bin/python Experiments/bootstrap_identity/probe.py
.venv/bin/python Experiments/bootstrap_team_delivery/probe.py
.venv/bin/python Experiments/bootstrap_history/probe.py
.venv/bin/python Experiments/bootstrap_retention/probe.py
.venv/bin/python -m pytest packages/cod-sync/tests/test_verify.py -q
git diff --check
```

The verifier micro tests passed: 25 cases.
The retention probe used Git 2.54.0 (Apple Git-157).
Raw model outputs and the voyage log are under the ignored `.IN_PROGRESS/Voyages/Finish-262/` directory; the models and this packet belong to the reviewable branch.

## Remaining work

Runtime authority records, event-DAG evaluation, Manager integration, encrypted baseline transport, retention transport and the short-string wire ceremony remain implementation or protocol work.
No model proves introducer honesty, global completeness, human identity or trusted time.
Fresh team encryption-key release still requires B9/B10 authority; identity continuity alone cannot supply it.
The transcript's readiness for human review is separate from human acceptance and GitHub issue closure.

## Independent review resolution

The review reproduced all five probes and the 25 verifier micro tests.
Its current-authority finding was accepted: B9 now states recognition under a selected local view, and B10 requires the releasing device's own policy and evidence.
The history model no longer labels absence of a removal as globally current authority and never authorizes key release.
The anchor-adoption finding was addressed by replacing the default-true flag with a required simulated decision bound to the complete exchange and documenting the human-consent limit.
The signed team route now retains the prior identity decision and its missing-proof fields.
The review's suggestion that the recognition-only route removes every human comparison was rejected: stage 1 still calls B1 authentication; the separate absence-of-comparison override remains explicit.
Finite history acceptance now names the deciding device, with a case rejecting its use by another device.
The full review and response live in the voyage root.

The focused review checkup reproduced the revised team and history models and found no remaining blockers in its scope.
It leaves default ceremony routing for recognition-only identities as a policy follow-up; the draft records the inherited missing proof and makes no claim that repeating a comparison repairs it.
