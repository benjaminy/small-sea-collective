# Notes

Issue: [#209](https://github.com/benjaminy/small-sea-collective/issues/209),
"Invitation token and errors are destroyed by their own OOB swap."

The Manager's create-invitation form in `fragments/invitations.html` targets `#invitation-token-area` with `hx-swap="innerHTML"`.
The response from `POST /teams/{team}/invitations` also carries an out-of-band `outerHTML` swap of `#invitations-{team}`, and that element contains the primary target.
htmx applies out-of-band swaps before the primary swap, and it resolves the primary target when the request is issued rather than when the response arrives.
The out-of-band swap therefore detaches the target node, and the token or the error text is written into an element that is no longer in the document.
Nothing reaches the screen on either path.

That ordering constrains the fix, not just the description.
A fix that renders a fresh `#invitation-token-area` inside the out-of-band content still shows nothing, because the swap writes into the stale node reference captured at request time.
Any accepted shape must keep the primary target outside every out-of-band element in the response.

The same response carries a second defect.
`fragments/invitation_token.html` wraps `{% include "fragments/invitations.html" %}` in a `<div id="invitations-{team}" hx-swap-oob="outerHTML">`, but that fragment's own root already carries the same id, and the admission-events block above it has the identical shape.
With `outerHTML` the wrapper itself lands in the DOM, so one successful create leaves `<div id="invitations-X"><div id="invitations-X">...</div></div>` on the page.
htmx resolves out-of-band targets with `querySelectorAll`, so later swaps act on both copies.
This did not cause #209, but it sits inside the response being repaired, and the fix keeps an out-of-band swap on that id.

## Selected shape

Move `#invitation-token-area` out of `fragments/invitations.html` and into `fragments/team_detail.html`, immediately after the include.
This is the second option listed in #209.
The id is declared in exactly one template today, so the change is one div moved up a level, and the out-of-band boundary then cannot contain the target under any swap ordering.
The cost is that the form's `hx-target` names an element in its parent template; htmx resolves targets globally, so this is a readability cost rather than a behavioral one.

The alternative was splitting `invitations.html` into a list fragment and a form fragment so the out-of-band swap could be narrowed to the list.
That needs a new template and edits in three files to reach the same guarantee.
Reconsider it only if some caller turns out to need the list refreshed independently of the form.

## Scope

Presentation layer only.
No invitation-protocol changes, no cloud-allocation recovery, no compatibility behavior, no deployment hardening.

Within the presentation layer, the duplicate-id wrappers are fixed in `fragments/invitation_token.html` only.
`fragments/admission_events_watch.html` has the same defect in a different response and is left alone; see Follow-up.

# Plan

1. Add `tests/test_invitation_token_ui.py` covering both outcomes against the real endpoints.
   Setup is local and cheap: `add_cloud_storage(protocol="localfolder")` before `create_team` for the success case, and a team whose Core berth has no cloud allocation for the failure case.
   No Hub session and no MinIO; `TestClient(create_app(root, participant_hex))` is enough, as `test_invitation_route_delivery.py` already demonstrates for `GET /teams/{team}`.
   → verify: check the fixtures' allocation precondition without calling the mutating invitation-creation method: `derive_team_join_state(...)["allocation"]` is present for the success fixture and absent for the failure fixture.
   Let the POST response itself prove that the success fixture produces a token and that the failure fixture renders the no-allocation error.

2. Assert the token itself, not a container.
   Parse the `.token-box` contents out of the POST response, base64-decode, and assert the payload's `team_name` and `invitee_label` match what the form submitted.
   Assert the submitted label also appears inside the out-of-band `invitations-{team}` element, which is the evidence that the list actually refreshed.
   For the failure case, assert the exact exception text is rendered in the response.
   → verify: these assertions pass before the fix as well; they cover the endpoint gap #209 names, and they are not the regression test.

3. Assert the structural invariant that makes the bug impossible.
   On `GET /teams/{team}`: exactly one `invitation-token-area`, exactly one `invitations-{team}`, and the former is not a descendant of the latter.
   On the POST response: no element carrying `hx-swap-oob` contains an `invitation-token-area`, and no id appears twice.
   A small `html.parser` subclass is enough, following the pattern in `tests/test_app_sightings_ui.py`.
   → verify: these fail on the current templates and pass after steps 4 and 5.

   No htmx simulator. Once the primary content carries the token and the target provably survives the response, visibility follows from those two facts; a hand-written swap model would only restate them, and would restate them in whatever semantics the model happened to encode.

4. Move the token area.
   Delete the `#invitation-token-area` div from the end of `fragments/invitations.html` and add it to `fragments/team_detail.html` after the invitations include, keeping its existing markup.
   → verify: step 3's invariants pass; the token and error assertions from step 2 still pass.

5. Put `hx-swap-oob` on the fragment roots instead of wrappers.
   Give the roots of `fragments/invitations.html` and `fragments/admission_events.html` a conditional `hx-swap-oob="outerHTML"` driven by an `oob` flag, and have `fragments/invitation_token.html` pass `oob=true` through its existing `{% with %}` blocks instead of wrapping the includes.
   → verify: the POST response contains no duplicate ids, and the admission-events and invitation sections still refresh after a successful create.

6. Run the tests and confirm the real thing once in a browser.
   Run the new file plus `tests/test_admission_proposals.py`, `tests/test_invitation_route_delivery.py`, and `tests/test_manager.py`, none of which need external services.
   `tests/test_invitation.py` and `tests/test_hub_invitation_flow.py` need MinIO; run them if it is available, and say so either way.
   Then load the team detail with the team session inactive and immediately create an invitation in the Manager UI, so the known admission-events poller race does not confound this check.
   Read the token off the page, then force the no-allocation failure and read the error off the page.
   Record that the team session was inactive and whether any poll request overlapped either POST.
   → verify: #209's first two validation bullets, which are manual by construction.

7. Review the diff.
   Every changed line should be a template edit or a new test.
   → verify: no changes under `manager.py`, `provisioning.py`, or `web.py`.

The branch is complete when the regression is guarded by an invariant that fails on today's templates, the token and error assertions cover the endpoint, and the browser confirmation in step 6 is recorded here with what was observed.

# Browser confirmation (step 6)

Confirmed 2026-08-23 against a scratch root with two teams under one participant:
`GoodTeam`, created after `add_cloud_storage`, so its Core berth has an allocation;
`NoAllocTeam`, created before it, so it has none.
Manager UI served on `127.0.0.1:8731` with no Hub, so the team session was inactive throughout
(the detail pane showed "Request session", never an active badge).

Success path: the token rendered in the page under "Step 1 done." (1380 characters in `.token-box`),
and the invitations table refreshed in the same response to show the `Dana` row as `awaiting_invitee`.
Failure path: `No cloud allocation for Core berth in team 'NoAllocTeam'` rendered in red below the create form.
Both are #209's first two validation bullets.

No poll overlapped either POST.
The uvicorn access log shows `admission-events/watch` completing before the POST and the next one starting after it, on both teams.
With the session inactive the watch delay is long enough that this was never close.

One live-DOM observation for the Follow-up below.
After the successful create, `document.querySelectorAll('[id]')` reported exactly one `invitations-GoodTeam`,
one `invitation-token-area`, and that the token area is not inside the invitations block --
but two `admission-events-GoodTeam`, produced by the watch response, not by the create response.
That is the second follow-up item, observed rather than inferred.

# Follow-up

Two items are recorded as focused GitHub issues rather than fixed here.

**[#215](https://github.com/benjaminy/small-sea-collective/issues/215): The create-invitation POST can be aborted by the admission-events poller.**
The create form in `fragments/invitations.html` and the watcher in `fragments/team_detail.html` both declare `hx-sync="#team-detail:replace"`, and the watcher polls every 0.2s while the team session is active (`web.py`, `_watch_delay`).
`replace` aborts the in-flight request, so a poll landing mid-POST discards the response and the user sees nothing: the same symptom as #209 from an unrelated cause.
Invitation creation is local work only (SQLite, a git head read, one signature), so the POST usually wins, but it is a race and no `TestClient` test can observe it.
Watch for it during step 6.

**[#216](https://github.com/benjaminy/small-sea-collective/issues/216): `fragments/admission_events_watch.html` has the same duplicate-id wrapper.**
It wraps the self-rooted `fragments/admission_events.html` in a `<div id="admission-events-{team}" hx-swap-oob="outerHTML">`, so the watch response nests two elements with that id.
Step 5 gives that fragment the conditional `oob` flag it would need, so the fix is one line, but it belongs to a different response than the one #209 is about.
