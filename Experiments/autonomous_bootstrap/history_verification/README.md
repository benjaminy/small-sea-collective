# Git history verification observations

Run from the repository root:

```sh
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m pytest Experiments/autonomous_bootstrap/history_verification/test_history.py -q
```

Result on 2026-09-13: 11 passed and 2 expected failures in 6.41 seconds.
The expected failures preserve two current gaps rather than claiming the verifier rejects them.

A quiet legacy graft can hide an unsigned parent from the full-history verifier.
The verifier already disables replace objects and rejects shallow repositories, but Git's legacy graft mechanism can still alter traversal.
This experiment requires control of local repository metadata and configuration; it is not an attack delivered by an ordinary remote bundle.
The probe confirms that setting `GIT_GRAFT_FILE` to an empty file for the subprocess restores the original traversal.
A production fix should choose between explicitly ignoring grafts for verification and refusing repositories with graft configuration.
It must also account for an environment-selected graft file rather than checking only `.git/info/grafts`.

Separately, `log.showSignature=true` makes Git insert human-readable signature text into the machine-readable report.
That rejects an otherwise valid signed history.
The probe confirms that `--no-show-signature` suppresses that prose while `%G?` still reports signature verdicts.

The valid controls exercise signed roots and merges, unsigned merge-side ancestors, replace refs, shallow histories, missing parent objects, and several presentation settings.
These experiments do not settle authorization after removal, pruning policy, or whether a signer was entitled to author a commit.
