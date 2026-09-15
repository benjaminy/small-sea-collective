# Prekey consumption observations

The local micro tests cover bundle collisions, transaction rollback, validation, and concurrent receipt.

```sh
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m pytest Experiments/autonomous_bootstrap/prekey_consumption/test_consumption.py -q
```

Two distinct Cuttlefish senders using the same 20-key bundle select the same first one-time prekey.
Their shared secrets differ, so this is not evidence of a shared-secret collision.
The bundle has no decentralized reservation mechanism.

Two valid Manager distributions created before the recipient refreshes its bundle also select the same prekey.
The recipient accepts the first and rejects the second because it has already consumed that prekey.
This case uses the same sender twice and the real Manager receiver; the separate Cuttlefish case establishes the selection problem across distinct senders.
Neither case uses a network or assumes a central prekey server.

The receiver now validates the decrypted distribution before changing the database.
It consumes the still-available prekey and saves the receiver record in one SQLite transaction.
Injected exceptions before and after the receiver-record write roll back both records, allowing retry of the original artifact.
These exceptions test transaction rollback, not power loss or filesystem durability.
Invalid decrypted sender, chain, and group fields leave the database unchanged and permit retry with the original artifact.
Two receiver threads pause after decryption, then compete to consume the same prekey through separate database connections.
Exactly one succeeds; the loser rejects the unavailable prekey without writing a receiver record.

## Separate decisions

Atomic local persistence closes the interruption gap without solving shared-bundle selection or stale-replay policy.
Concurrent selection needs its own policy.
Random selection reduces collisions but cannot guarantee uniqueness, and retry requires a fresh authenticated bundle.
Per-sender prekeys can avoid cross-sender collisions but require explicitly scoped publication and management as devices join or leave.
Retaining a supposedly one-time secret for multiple uses changes its security claim and deletion policy.
An interactive receiver can allocate keys while online, but requiring that for every exchange weakens the existing asynchronous design.
A visible retry after fresh bundle delivery may be acceptable for small human groups; it must not silently pretend that the current shared bundle provides one-time allocation.
No alternative is implemented by these observation tests.

## Focused validation after the fix

```sh
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m pytest packages/small-sea-manager/tests/test_sender_key_rotation.py Experiments/autonomous_bootstrap/prekey_binding/test_prekey_observations.py Experiments/autonomous_bootstrap/prekey_consumption/test_consumption.py -q
```

Result: 30 passed in 11.57 seconds.
