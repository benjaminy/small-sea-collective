# Two ways to decide that a new device belongs to a person

Keys are lost and stolen; people last for decades.
This probe compares two policies a newcomer can use to decide whether the device introducing it speaks for a person's enduring identity.
Both run over the same authenticated exchange, so the comparison is about authority policy, not about delivery.

- **Durable anchor (DA).** The newcomer adopts an identity anchor carried by the authenticated exchange, and the introducer must present retained signed delegation records running from that anchor to its own key.
- **Sibling delegation (SD).** The operator of the new device signs an explicit, scoped decision authorizing the compared sibling key. Historical delegation is not claimed.
The model signs the local record with a disposable operator key to test record binding; that signature does not prove human presence or recognition.

Run from the repository root:

```sh
.venv/bin/python Experiments/bootstrap_identity/probe.py
```

The command asserts an expected outcome for every scenario and prints the verdicts, with their retained evidence and independent audit, as JSON.
Keys are disposable and generated per run; there is no network and no database.
These are micro tests.

## Three claims, kept apart

1. **A key signed a statement.** Every signature check in this file establishes only this.
2. **Local recognition.** *This* device treats a successor as the same enduring identity.
3. **Team authority.** A team recognizes that successor for some power over team state.

Both policies can reach claim 2.
Neither reaches claim 3, and the probe asserts that across every scenario: no verdict in the file carries a team-authority claim.
Every accepting verdict records who decided, what scope the decision covers, which prior evidence it cites, and what it does not prove.

## Expected outcomes, written before the run

| Scenario | Expected |
| --- | --- |
| `da_valid_control` | Accept. The chain from the anchor reaches the compared introducer. |
| `sd_valid_control` | Accept. The operator's signed decision names this attempt, this newcomer key and this sibling. |
| `da_first_device_lost_chain_retained` | Accept. The first device's private key is gone; its public anchor and two retained delegations still carry the introduction. |
| `da_chain_absent` | Pause. The claim is the same; the evidence is not there. The verdict names the missing record. |
| `sd_chain_absent_human_recognition` | Accept, as local recognition only. `missing_proof` says continuity with any earlier key is unproved. A fresh recognition must not read as old continuity. |
| `da_compromised_authenticated_sibling` | Accept. A genuine chain in a thief's hands passes. The independent audit reports `accepted_despite_stolen_key`. |
| `da_conflicting_successors` | Pause, preserving both competing successor records. |
| `da_fabricated_self_rooted` | Reject. The snapshot is internally perfect and rooted in the attacker's own key; only the independently retained comparison catches it. |
| `weakened_baseline_accepts_forgery` | Accept — the deliberately weakened baseline. One condition changes: the independent comparison is skipped. Signature, chain and scope checks still run, and the forgery passes. |
| `da_scope_misuse` | Reject. A berth-scoped delegation is not identity-device evidence. |
| `sd_decision_for_another_exchange` | Reject. Reusing the same attempt and keys does not authorize a different snapshot or complete exchange. |
| `sd_scope_misuse` | Reject. A human decision scoped to `team-authority` is refused by the identity policy. |
| `paired_claimant_and_stolen_key` | Identical inputs, identical verdicts. The model reports no distinguishing evidence. |

## The check that is not the acceptance logic

`audit()` re-verifies each retained delegation directly from raw bytes, using `cryptography` and not the model's `authentic`/policy functions, and compares the verdict against a ground-truth record of what actually happened in the scenario.
That is how `accepted_despite_forged_root` and `accepted_despite_stolen_key` are reported: the probe knows facts the newcomer cannot know, and says so instead of letting a passing check stand in for honesty.

## What the comparison shows

DA proves something about the past when evidence survives, and stops when it does not.
With a matching explicit operator decision, SD permits local recognition.
That decision may cite earlier evidence, but the citation is not a verified delegation chain.
The dangerous mistake is to let SD's output later be read as DA's, so SD's verdict carries its `missing_proof` and `others_may_refuse` fields with it.

The paired case sets the limit.
A person returning after losing keys and an attacker holding a stolen key can present byte-identical signed input.
No signature check separates them.
Only human recognition or an independent endorsement can, and those are decisions about now, not proofs about then.

## What is simplified

The comparison channel supplies a **full digest** of the completed signed response.
A real ceremony compares a short string a human can read aloud; this model does not implement it, does not measure human comparison error, and does not model replay storage or physical key custody.
Keep that distinction when carrying any conclusion here into a wire protocol.

Delegation is a **linear chain with a `supersedes` marker**, not a general event DAG.
There is no revocation, quorum, expiry, timestamp ordering, or partial-order merge.
Conflict detection finds two live delegations of one scope from one delegator; a real DAG has conflicts this cannot see.

Neither policy can infer an honest or globally complete view.
The newcomer sees only what the introducer offers.
An authenticated introducer that simply withholds a record leaves no trace in the exchange; the previous probe (`Experiments/bootstrap_exchange`) demonstrates that separately.
No root is inferred from downloaded rows: the anchor arrives inside the authenticated exchange, and the comparison commitment is retained independently of everything delivered.

The JSON encoder serves generated fixtures only and is not hardened for untrusted input.

The orchestrator added exact-exchange binding and retained the complete local decision, including its basis and prior evidence references, after reviewing the first run.
The added case rejects a decision reused for a changed view under the same attempt and keys.
The model does not require a lost private key during verification; it does not simulate physical key erasure.
