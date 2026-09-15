# Invitation ordering review assessment

The low-effort Opus review identified a stale change-list item that still placed the final commitment in the early token.
It now targets the final response.
The transcript defines authority anchor and trust root as the same extension input and distinguishes them from technical origin.
It also names B2's exact snapshot as B4's input; that snapshot may contain parked events outside its selected frontier closure.

The review argued that an anchor must be known before meeting the introducer.
That is a stronger trust requirement than this design selects.
The newcomer may explicitly trust an authenticated introducer to supply an anchor; independent means independent of downloaded data, not prior knowledge of a team.
A malicious authenticated introducer remains a stated limitation.

Offer expiry and reuse belong to admission policy rather than the Constitution core.
The inviter records the final attempt and applies that policy again before an irreversible action; replay does not grant fresh authority by itself.
The request-substitution walkthrough now distinguishes the newcomer's exact-request check from the human comparison needed to detect two different relayed exchanges.

This is a documentation correction with three local links checked and git diff --check passing.
No wire format or runtime invitation protocol was added.
