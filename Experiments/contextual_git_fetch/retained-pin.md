# Retained descendant pin probe

## Prediction

An old-store fetch with an A-only `SshCommitVerifier` will verify signed commit A and report A as `observed_head`.
If the local pin already points to signed descendant B, `Repo.advance_ref` will report `stale` and retain B even though this fetch did not verify B.
The same verifier will reject B with `UnknownSignerError` when asked to verify B directly.
A fresh pin fetched from the old store will point to verified A.

## Fixture

The probe creates temporary SSH keys and signed Git commits, then publishes A to one `LocalFolderStore` and its descendant B to another.
It imports the later store without a pin and seeds the pin at B to represent an earlier transport observation.
It then runs the real `CodSync.fetch`, `SshCommitVerifier`, and `Repo.advance_ref` path against the old store.
Git ignores global and system configuration, and the probe removes `SSH_AUTH_SOCK`.
The probe uses only temporary local files.

## Result

Run with `.venv/bin/python Experiments/contextual_git_fetch/retained_pin.py`.
The saved run produced:

```json
{
  "observed": {
    "A_only_verifier_rejects_B": true,
    "fresh_pin_points_to_verified_A": true,
    "later_head_rejection": "UnknownSignerError",
    "pin_disposition": "stale",
    "stale_fetch_observes_and_verifies_A": true,
    "stale_fetch_retains_descendant_B": true
  },
  "prediction": {
    "A_only_verifier_rejects_B": true,
    "fresh_pin_points_to_verified_A": true,
    "stale_fetch_observes_and_verifies_A": true,
    "stale_fetch_retains_descendant_B": true
  }
}
```

## Claim and limits

The probe tests whether one fetch invocation can return a `pinned_head` outside the history that invocation's verifier accepted.
It does not show a new unverified pin advancement: B was already pinned, and `advance_ref` leaves it in place.
The fixture therefore presupposes an earlier operation that imported B and seeded the pin.

Pins record transport observations.
Retaining B does not integrate B into an app branch and does not release or authorize B's signing key.
The probe covers one linear history, one process, and `LocalFolderStore`; it does not model concurrent ref writers or remote storage.
