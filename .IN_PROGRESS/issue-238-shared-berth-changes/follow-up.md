# Issue follow-up after the design decision

No implementation handoff is ready; [plan.md](plan.md) holds the remaining questions and validation obligations.
These are local proposals, not posted issue changes.

The tentative split is one established-team Core implementation followed by broader integration and reports.
The first must define a contract and identify paths outside it; the second must account for creation, invitations, changes, repair, integration, generic publication and courier delivery.
Concrete scope follows the accepted design, not today's helpers or tables.

- **#238:** record the decision, rejected alternatives, evidence, limitations and agreed implementation split.
  Keep it open until runtime correctness requirements are implemented and validated.
- **#224:** if public predecessor links are accepted, obtain the issue owner's agreement and explain the substantive change and diagnostic/privacy tradeoff.
  A wire change alone is not an ownership or trust change.
- **#139 / #235:** record actual dependencies and interfaces for arbitrary app berths and Core/Files use.
  Their app provisioning UX and Files capstone scope remain separate.
- **#237:** preserve the distinction between shared accounts and device-local credential repair when discussing overlap.
- **Independent defects:** prepare focused issue proposals for demonstrated problems outside the agreed scope.
  No duplicated-selector-policy defect was found; provider migration, old-location cleanup and speculative recovery remain outside this work.

After acceptance, put the appropriate semantics in Manager/Hub specs and architecture, clearly separating designed from implemented behavior and removing unsupported safety claims.
The two already-completed Hub spec corrections have their own status in [doc-fixes.md](doc-fixes.md).
