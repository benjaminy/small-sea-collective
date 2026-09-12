# Bootstrap trust transcript

Branch: `issue-262-bootstrap-transcript`.
Issue: [#262 — Write the Small Sea bootstrap trust transcript](https://github.com/benjaminy/small-sea-collective/issues/262).
Status: code trace done; the human selected exchange-authenticated snapshot delivery plus reconstruction of authority-bearing state from Constitution evidence (option D in the subsequent discussion).
See `notes.md` for the decision and remaining validation work; `trust-anchor-brief.md` preserves the earlier proposal.

## Outcome and scope

Write one Small Sea bootstrap transcript that follows independently authenticated invitation or sibling-device evidence through Core history to berth-scoped signing keys.
Use a common sequence with explicit differences between the two entry paths.
At every step, identify the actor, the evidence already held, the check performed, the authority decision, and the exact conclusion the newcomer may draw.
State what the inviter or sibling is trusted to assert and what remains unproved.

The intended durable home is `Documentation/bootstrap-trust.md`, with narrow links or corrections in the relevant specs once the design is settled.
This branch is a design audit, not runtime key provisioning or verifier wiring.
Keep implementation gaps visible; a written protocol does not demonstrate that current code enforces it.

## Work sequence

- [x] Read #262, #190, #263, and #266; inspect the architecture and relevant Manager, Hub, Cod Sync, and Wrasse documentation.
- [x] Record starting evidence, gaps, and issue boundaries in `notes.md` and `follow-up.md`.
- [x] Trace invitation acceptance, identity linking, and linked-team bootstrap through provisioning code and their micro tests.
  Record what each path actually authenticates, including any fixture that supplies a baseline outside the ceremony.
- [x] Select the initial trust approach (human checkpoint).
  Authenticate the complete bootstrap exchange, including the snapshot digest and exact Constitution frontier.
  Reconstruct authority-bearing state from that frontier and its ancestry under explicitly named extension rules and local policy, then compare the snapshot's corresponding semantic state.
  Git carries the transient snapshot; permanent Constitution evidence supports authority decisions.
- [ ] Classify the bootstrap-critical snapshot fields and complete the selected trust contract.
  Identify fields derived from Constitution events, participant-local choices, and delivery information.
  Name any essential field that permanent evidence cannot establish and the additional evidence or explicit local decision it requires.
  Specify how conflicts and missing evidence affect reconstruction; do not assume a single policy-independent projection.
- [ ] Draft the transcript from the initial evidence to acceptance of a particular Core view and recognition of keys for a particular berth and purpose.
  Name the authorization extension or local policy at each transition; Constitution core verification alone supplies no membership verdict.
  Separate identity join, team admission/device enrollment, historical authorship, local history acceptance, and distribution of future encryption keys.
- [ ] Work through the validation cases below and revise the transcript wherever a conclusion lacks evidence.
- [ ] Reconcile narrow documentation claims, record remaining work by issue, and prepare the design for human review.

## Questions the transcript must answer

1. What does the newcomer authenticate independently before fetched Core can supply meaningful authority evidence?
   A transport descriptor, friendly team name, decryptable payload, or self-consistent signer list must not stand in for that evidence.
2. Does the newcomer adopt an authenticated inviter's selected history as a local trust decision, validate an authority history from an independent anchor, or combine the two?
   Define the scope of the assertion and the residual risk from a mistaken or malicious authenticated inviter.
3. What evidence binds the invitation or sibling ceremony to the exact Core history being considered?
   For sibling bootstrap, account for the currently assumed baseline clone instead of starting after the unresolved step.
4. How does the resulting trust view recognize a signing key for the exact team, berth, and purpose?
   Distinguish current team-device keys and sender-key encryption state from #266's intended berth-scoped commit-signing keys.
5. What can a newcomer conclude about an original author removed before it joined?
   Separate the inviter's earlier acceptance from the newcomer's decision and from permission to accept new work by that author.
   Coordinate with #263; do not treat timestamps as independent proof of when a malicious signer acted or acceptance as permanent.
6. When evidence is missing, contradictory, or unauthenticated, whose work pauses and which actions remain permitted?
   State what evidence is preserved, who can resolve the pause, and what additional evidence or explicit decision permits progress.
   A human override changes local policy, not the underlying authentication result.

## Validation and review evidence

The primary validation is a step-by-step argument a reviewer can replay without assuming the desired conclusion.
Give each transcript step an identifier and list its inputs, provenance, checks, and policy owner, then three outcomes: the evidence recorded, the default conclusion, and what a human may override.
Keep precise check results separate from local acceptance decisions.
An override changes local policy; the recorded evidence and the authentication result stay as they were.
Expect the two adversarial cases to differ in kind: a substituted history is a failed binding whose default is to stop and show the mismatch, while a removed author is a policy question whose selected default is to pause the newcomer's integration for local review with the evidence preserved.
Every authority claim must lead back to independently authenticated evidence or an explicitly named local trust decision.
Mark each mechanism as implemented, specified but unenforced, or proposed, with file/function or micro-test references for implementation claims.

| Case | Evidence the walkthrough must show |
| --- | --- |
| Valid invitation | The independently authenticated inviter evidence binds the selected team and Core view; each subsequent authority claim has a stated basis. |
| Valid sibling bootstrap | Identity binding precedes team trust; baseline delivery and authentication are accounted for; enrollment uses a fresh team-device key. |
| Original author removed before joining | Authentic old commit signatures do not disappear with removal; retained evidence and local policy explain whether the newcomer accepts or pauses the history, without granting future authority or promising permanent acceptance. |
| Substituted Core listing attacker keys | An internally valid replacement history cannot authenticate itself; identify the first failed independent binding or missing-evidence pause, and pair it with a valid control. |
| Missing or mismatched ceremony evidence | Decryption and fetched signer membership do not silently enable ordinary identity use, team joins, or key distribution; distinguish intended policy from current enforcement. |
| Missing ancestry or conflicting authority views | The transcript names the missing evidence or disagreement, preserves alternatives, and defines the scope of the pause without choosing by arrival order or timestamp. |
| Key from another berth | The transcript identifies which evidence fails the scope check, even if the key has a valid signature and belongs to the same physical device. |
| Authenticated snapshot misrepresents Constitution evidence | The snapshot digest matches the exchange, but reconstruction reveals different authority-bearing state; authenticated delivery does not establish correctness. |
| Old Git contents unavailable | Identify which authority claims remain checkable from permanent Constitution evidence and which current storage or verifier assumptions still prevent bootstrap. |

For the substitution case, let the attacker control the downloaded history, roster, routing descriptor, and signatures under its own keys while holding the independently authenticated evidence fixed.
Also state the limits when the authenticated inviter itself is malicious; the transcript must not promise that an anchor proves an honest or complete team view.

Review the removed-author case against #190's requirement to check every commit relied upon by an accepted head, including original authors retained in rollups and merge-side ancestry.
That current verifier contract conflicts with a future retention model that discards old Git contents.
Record the needed contract change or dependency explicitly; this decision does not implement pruning or change the current verifier.
Do not silently drop required ancestors or add every fetched key to the verifier's input.
Record the contract #266 needs and any unresolved #263 policy dependency.

Check repository integrity by confirming that Manager still owns authority decisions, Hub still mediates Small Sea network traffic and app trust access, apps open no Core database, and Cod Sync still receives an explicit key set without learning membership policy.
Keep Constitution event signatures separate from Git commit signatures and sender-key encryption.
Avoid introducing endpoints, schemas, migration machinery, or automatic repair solely to make the prose appear complete.

For this documentation work, inspect linked code and micro tests and verify local links and `git diff --check`.
Run focused local micro tests only when a behavior claim needs execution evidence; record the command and result in `notes.md`.
Do not claim the design is enforced merely because existing round-trip micro tests pass.

## Completion criteria

The durable transcript covers both entry paths, both required adversarial cases, the exact trust delegated to the inviter or sibling, and the transition to berth-scoped authority.
Every missing-evidence branch has an explicit pause and resolution condition.
Open product choices are resolved with a human or clearly left as blockers; a placeholder for the initial trust source does not complete #262.
Current implementation limits and dependencies on #263 and #266 remain explicit.
Near branch completion, update this plan briefly and draft `final-commit-message.md`; create `design-record.md` only if there are lasting decisions that do not belong in the durable documentation.
