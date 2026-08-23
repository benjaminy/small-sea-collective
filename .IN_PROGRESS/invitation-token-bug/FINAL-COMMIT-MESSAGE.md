Show the invitation token and its errors (#209)

The Manager's create-invitation form targeted `#invitation-token-area`,
which sat inside the `#invitations-{team}` block that the same response
swapped out of band.
htmx applies out-of-band swaps first and resolves the primary target when
the request is issued, so the swap detached the target and both the token
and the error text were written into a node no longer in the document.
The token area moves up one level into `fragments/team_detail.html`, which
puts it outside every out-of-band element in the response regardless of
swap ordering.

The same response also nested two elements per id, because
`fragments/invitation_token.html` wrapped fragments that already carried
those ids on their own roots.
The roots now take a conditional `hx-swap-oob` instead, so the wrappers go
away.
`tests/test_invitation_token_ui.py` covers both endpoint outcomes and
guards the structural invariant: the primary target is never a descendant
of an out-of-band element, and no id is declared twice.
