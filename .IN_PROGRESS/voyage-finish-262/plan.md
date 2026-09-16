# Finish the bootstrap trust transcript

Status: complete for human review; all work remains local.
Preflight passed, including scheduled state recovery.
The operating agreement and current orientation live in [the voyage README](../Voyages/Finish-262/README.md).
The voyage root is intentionally ignored by Git; this tracked handoff does not replace it.

## Outcome

Resolve #262's identity-authority and sibling evidence-delivery decisions through concrete comparisons, then finish `Documentation/bootstrap-trust.md` for human review.
Preserve prior human choices recorded in `.IN_PROGRESS/issue-262-bootstrap-transcript/notes.md`.
Runtime verifier wiring and the broader removal policy remain in #266 and #263.

On 2026-09-16, the human prioritized compelling demos and asked to defer configurable policy work.
[Configurable team policies](../../Documentation/configurable-team-policies.md) records that direction: use explicit fixed policies for demonstrated flows and defer the general framework.

## Next work

1. Preflight completed; operating limits and the enduring-identity priority are recorded in the voyage README.
2. Completed: compare retained identity delegation with explicit local recognition; preserve the exact decision and distinguish team authority.
3. Completed: compare both delivery routes from empty state; select the identity-signed route with explicit team authority checks.
4. Completed: independent review findings resolved; the focused checkup found no blockers in scope.
   The actual verifier confirms that missing file content differs from missing commit ancestry.
5. Completed: review packet, local issue-update drafts and final commit-message draft prepared.
6. Completed bounded follow-up: actual signed Git commits expose the failures of historical-union and current-only key sets.
   The contextual model keeps finite accepted history separate from current local berth authority and missing basis evidence.
   No runtime API or verifier wiring is approved by this experiment.
7. Completed bounded fetch follow-up: the actual fetch hook rejects simulated authority failures before pin movement, but cannot keep a captured view current after it returns.
   A stale fetch verifies its exact observed head while retaining a preexisting descendant pin that this invocation did not verify.
   These are #266 research results, not a runtime or API choice and not a condition of #262 readiness for human review.
8. Completed: record configurable policy choices as deferred work and link the boundary from the architecture and bootstrap transcript.
   Validate that the documents preserve evidence requirements, distinguish proposed policy from core rules, and avoid making the full research backlog a demo prerequisite.
   Check local links and whitespace; no runtime behavior changes or micro tests are needed for this documentation update.

## Evidence required

The comparisons must name each independently authenticated input, explicit human trust decision, retained record and missing-evidence pause.
Pair attacks with valid controls and preserve a weakened baseline that accepts a self-consistent substitute.
Exercise first-device loss with retained and missing delegation evidence, compromised siblings, conflicting claims and team-subset enrollment.
Start sibling team bootstrap from an empty store rather than a copied trusted fixture.

The final transcript must cover removed authors, substituted Core, an authenticated false projection, missing comparison or ancestry, conflicting views, and a key from another berth.
Separate accepting finite past history from future authority and key distribution.
Treat unavailable old Git contents as an explicit limit, not verified history.
Check Manager/Hub/Constitution responsibilities, implementation-status claims, local links and `git diff --check`.
Run focused local micro tests when a changed behavior or runtime claim needs execution evidence.
