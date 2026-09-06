# Completed Hub spec correction

Both independent corrections landed in `2ebe9833051e56a1549e16496c0adcca96fc439e` (`Update spec.md`).
The review of `2f5ff00` verified that this commit is an ancestor of the current branch and the Hub spec has no differences from that revision.
The earlier branch documents incorrectly listed incorporation as pending; no merge or repeated correction is needed.

The commit changes only two passages in `packages/small-sea-hub/spec.md`:

- The sibling-location passage formerly at lines 338–341 contradicted #224's participant-owned shared allocation and equal-sibling decision.
  The correction preserves announcement-based sibling reads and acknowledges divergent local views without claiming independent device ownership.
- The concurrency passage formerly at lines 357–366 called first-use races recoverable clutter because newest UUIDv7 announcement wins.
  Delayed signing can give an earlier selection a greater ID than a sibling's observed replacement, so this rule does not establish cross-device safety.
  The Manager spec already acknowledged the unresolved ordering at line 1172.

Those line references come from the original pre-correction review; current status was checked through commit ancestry and the spec diff.
Manager spec behavior near lines 847/875 was explicitly outside the correction: it accurately described current behavior, whose redesign belongs to #238.
The correction accepts no #238 protocol and resolves no runtime race.
Resume the remaining work in [plan.md](plan.md).
