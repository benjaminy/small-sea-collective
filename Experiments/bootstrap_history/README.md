# Finite history acceptance and future authority

This local policy model covers the historical-author case in B8 and keeps it separate from B9.
It uses real signatures on small work records that name their authority basis; these are not Git commits and do not implement #266's missing commit-to-Constitution binding.
The actual Git-signature boundary is exercised separately by `bootstrap_retention` and Cod Sync's verifier micro tests.

## Expected outcomes before execution

A valid work record with its signed scoped grant present can be accepted under a view that has not removed the author.
A view containing a signed removal pauses this newcomer's integration of that work.
An explicit local decision covering that exact work and Constitution view permits its integration while leaving recognition under that removal view false.
The same decision must not cover a new work record, another view, another berth, or an invalid signature.
Missing authority evidence pauses historical conclusions even if the work signature is valid.
Different selected views may reach different recognition decisions under their own selected views without a canonical winner.
A signed timestamp cannot prove that an apparently old work record was created before a removal.

Run from the repository root:

```sh
.venv/bin/python Experiments/bootstrap_history/probe.py
```

The anchor is an explicit local input assumed authenticated by an earlier exchange; it never comes from the fetched grant.
The fixture has one anchor-signed grant and an optional anchor-signed removal naming that grant as parent.
That special case exposes the past/future boundary without implementing a general event DAG, team recovery, quorum or Git authority evaluator.
The acceptance record is a device-local policy input, not a new proof of authorship or time.

After independent review, the output names `recognized_in_selected_view` rather than claiming globally current authority.
An absent removal in a supplied view is not evidence that no removal exists.
`future_key_release` remains false in every case: the releasing device needs its own separate current policy decision.
Finite acceptance names the deciding device and does not authorize another device's integration.
