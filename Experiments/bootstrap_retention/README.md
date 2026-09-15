# Commit signatures after content removal

The transcript currently says the full-ancestry verifier conflicts with removal of old Git contents.
Cod Sync's retention description keeps commit objects and removes old file contents.
Those are different objects, so the claimed conflict needs an executable check.

## Prediction before execution

Removing an old blob or tree while retaining all commit objects should leave SSH commit-signature verification possible, because the signature covers the commit object and its tree identifier.
Reading the removed file should fail.
Removing a required parent commit should make the full-ancestry verifier fail.
The probe must restore each object and verify the passing control again.

Run from the repository root:

```sh
.venv/bin/python Experiments/bootstrap_retention/probe.py
```

The probe uses a disposable local repository and temporary SSH keys.
It runs the actual SshCommitVerifier, not a model of it.
Removing loose objects is a controlled fixture, not an implementation of partial-clone retention.
No network access or existing repository objects are involved.

## Observed result

On Git 2.54.0 (Apple Git-157), removing either the old blob or the old tree left both commit signatures verifiable, while `git show` could no longer read the old file.
Removing the parent commit raised `VerificationUnavailableError`.
Restoring each object restored file access and the passing verifier control.
The result corrects the transcript's former claim of an inherent conflict between content retention and commit-signature verification.
It does not prove that bundle transport supports pruned objects or that historical contents can be checked without fetching them.
