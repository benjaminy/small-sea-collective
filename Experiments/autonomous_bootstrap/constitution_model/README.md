# Constitution intake model

This standalone Python model explores the active-tip bound in `architecture.md` and ancestry-closed local handling in `Documentation/team-constitution.md`.
It does not exercise production code or prove cryptographic correctness.
Integer event IDs stand for immutable content digests.
Generated graphs are acyclic; this model does not implement cycle detection, canonical decoding, signatures, origin verification, byte limits, extension effects, or explicit human reconciliation.
An envelope-valid flag allows focused micro tests to distinguish missing ancestry from invalid ancestry.

Run from the repository root:

```sh
python3 Experiments/autonomous_bootstrap/constitution_model/model.py
python3 Experiments/autonomous_bootstrap/constitution_model/model.py --exhaustive-size 5 --seconds 3600 --seed 262
```

The second command first explores all five-node DAGs compatible with numeric topological order, every delivery permutation, and every cap from one through five.
It then runs seeded randomized DAGs for one hour, increasing coverage through fresh graphs and delivery orders.
The time limit covers only the randomized phase.
Each trial checks ancestry closure, exact minimal heads, the active-tip cap, and persistent parking.
A separate slow oracle computes heads from the full active set rather than updating the head set incrementally.
Both implementations choose the first ready event in delivery order; agreement does not imply that other local choices converge.
An assertion failure during random exploration prints the seed, trial, graph, delivery order, and cap for reproduction.

## Findings

The initial five-node run passed 614,400 exhaustive cases and 6,953 seeded random trials in the following 60 seconds (seed 262).
These results cover the model and its oracle, not an implementation of the repository's protocol.

**A merge cannot revive an already parked branch under the modeled rule.**
For a cap of one, all 24 delivery permutations of a root, two siblings, and their merge park the merge.
The two possible final bases show why devices holding the same events need not select the same view.
Receiving a child before its parent leaves it incomplete until the parent arrives.
Receiving an invalid parent instead makes the child invalid, separately from parking.

**The rule bounds simultaneous active heads, not the graph's maximum concurrent width.**
For cap two, receive a root, siblings `a` and `b`, their merge `m`, sibling `c` of `a` and `b`, and merge `z(m,c)`.
Every event activates and the final view has one head, although `a`, `b`, and `c` are three concurrent events.
The micro tests contain this witness.
All those events could already be buffered with that local ready ordering.
The architecture's statement that a batch with more concurrent branches than the bound must park a branch is therefore broader than the operative parent-before-child rule supports.
The narrower statement is that an attempted activation which would exceed the current active-tip bound parks that event, and a later merge cannot reverse that decision.

**Tip limits do not bound total history or validation cost.**
A signed chain consumes one active tip forever, and rolling merges can admit unlimited siblings without exceeding two simultaneous heads.
The specification already calls for separate storage, closure, and intake budgets.
Those limits remain essential even when the tip-bound rule works exactly as written.
The model deliberately uses simple repeated readiness scans and full invariant checks, so its throughput is not a production performance estimate.

## Alternatives

Keep the per-event rule and narrow the explanatory sentence.
This is the simplest option: it enforces small bookmarks and prevents received merges from overriding a prior local parking decision.
Use separate budgets for work and history size.

A stronger rule could finish a whole generation of ready siblings before allowing descendants to activate.
That catches the three-sibling example if all siblings arrive in one batch, but changes behavior when the same sender splits its transport batches.
It cannot establish a global concurrency-width limit in an asynchronous network: a device cannot know that another concurrent sibling will never arrive.
Trying to close that gap needs a different policy and sacrifices prompt local progress.
I favor the first option unless bounding historical concurrency itself becomes a stated requirement.
