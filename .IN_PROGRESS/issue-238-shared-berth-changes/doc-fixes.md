# Proposed side quest: remove two false Hub spec claims

Status: reviewed and supported in the 2026-09-06 narrow round; the user plans to make the corrections separately on `main`.
The `main` edit is not yet verified; no spec file is modified on this branch.
This agreement concerns only the two documentation corrections, not a #238 protocol.

## Returning after the `main` detour

The evidence and line numbers below describe this branch at `f03ee3d`, before the proposed corrections.
On return, inspect `main`'s Hub spec and its history to establish whether the corrections landed, and record the commit here if they did.
This branch may still contain the old passages until it incorporates that change.
Do not mistake that stale checkout for a new defect, repeat an already completed correction, or restore the old claims when reconciling the branches.
If the corrections did not land, leave them pending.
Their completion does not resolve the delayed-signing race or close #238; resume the open discussion in [plan.md](plan.md).

## Review basis

These two claims are wrong today, independently of which design #238 accepts.
Neither depends on the open protocol questions in [plan.md](plan.md), so neither needs to wait for them.
Removing a false safety claim is not a design decision.

## 1. Independent sibling locations

`packages/small-sea-hub/spec.md` lines 338-341 state that this device's local allocation describes where this device writes,
and that a sibling device may have written the same berth to a different location.

[#224's decision](https://github.com/benjaminy/small-sea-collective/issues/224#issuecomment-5548215384) settled the opposite:
one participant's devices share one allocation and one route per berth, and there is no owner device.
That issue is closed and decided.
The spec text contradicts it.

## 2. The concurrency section's safety claim

`packages/small-sea-hub/spec.md` lines 357-366 describe cross-device first-use races as recoverable clutter rather than a correctness failure,
on the grounds that peers select the newest valid announcement by UUIDv7 `announcement_id`.

That reasoning fails on this branch's delayed-signing schedule.
`packages/small-sea-manager/small_sea_manager/provisioning.py:3519` mints the ordering value immediately before signing,
from the predecessor this device last observed.
A device that settles a route, pauses, and resumes after a sibling has published a successor can mint a *greater* ID for the *earlier* selection when its UUID timestamp is later than the successor's.
This is a permitted schedule, not a claim that every later signing produces a greater ID across devices with skewed clocks.
Newest-by-UUIDv7 then routes peers to the superseded location, which is the correctness failure the passage denies.

The Manager spec already says this plainly at `packages/small-sea-manager/spec.md:1172`:
"Ordering across devices publishing concurrently is not settled by this rule."
So the two specs contradict each other today.

## Proposed scope

Correct those two passages in the Hub spec and stop there.
State what is decided (shared participant-owned allocation) and what is open (cross-device ordering), citing #238 for the latter.
Preserve the rule that sibling reads use signed announcements, and acknowledge that local views and materialization results can diverge before coordination.
Shared ownership does not establish that the current implementation safely coordinates siblings.
Do not describe an unaccepted protocol as designed behavior.

Explicitly out of scope: `packages/small-sea-manager/spec.md:847` and the selection rules near line 875.
Those accurately describe current behavior.
Whether they change is a #238 design question, not a documentation defect.
