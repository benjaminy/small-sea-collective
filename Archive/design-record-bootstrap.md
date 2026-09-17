# Decisions worth retaining

## Why identity recognition has two kinds of evidence

A retained delegation chain records what earlier keys signed.
An explicit local recognition decision records what a person chooses now when that chain is absent or disputed.
The latter cannot repair the former, and forcing it to would make key loss indistinguishable from permanent loss of identity.
The combined policy permits local continuity while preserving which claim rests on signatures and which rests on recognition.
A team may still require fresh admission or its own recovery evidence.

## Why the ordinary team response uses the identity relationship

A complete per-team signed exchange can use an already recognized identity key to authenticate delivery without another human comparison.
It still requires an explicit team-anchor decision and separate scoped enrollment evidence.
The alternative ceremony remains useful when the operator wants another independent check, but a repeated comparison with the same compromised claimant does not create missing historical proof.
Retain the earlier recognition basis and its limits with the team evidence.

## What changed the retention conclusion

The old draft treated unavailable file content as unavailable commit ancestry.
The actual verifier probe showed that a signed commit can still be verified when its old tree or blob cannot be read.
A missing parent commit remains a hard limit for the verifier's full-ancestry claim.
That distinction keeps the design from imposing unnecessary permanent file-content retention merely to preserve commit signatures.
