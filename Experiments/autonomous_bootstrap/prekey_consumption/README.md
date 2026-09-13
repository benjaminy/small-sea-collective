# Prekey consumption observations

Three local micro tests passed in 1.19 seconds on 2026-09-13.

```sh
.venv/bin/python -m pytest Experiments/autonomous_bootstrap/prekey_consumption/test_consumption.py -q
```

Two distinct Cuttlefish senders using the same 20-key bundle select the same first one-time prekey.
Their shared secrets differ, so this is not evidence of a shared-secret collision.
The bundle has no decentralized reservation mechanism.

Two valid Manager distributions created before the recipient refreshes its bundle also select the same prekey.
The recipient accepts the first and rejects the second because it has already consumed that prekey.
This case uses the same sender twice and the real Manager receiver; the separate Cuttlefish case establishes the selection problem across distinct senders.
Neither case uses a network or assumes a central prekey server.

An injected interruption after prekey consumption but before `save_peer_sender_key` prevents retry of the original artifact.
The test raises at the save boundary; it does not simulate power loss or prove filesystem durability.
The production code consumes the prekey in a separate database operation before saving the received sender key.

## Separate decisions

Atomic local persistence of consumed-prekey state and received-key state would close the interruption gap without solving concurrent selection.
Validate the decrypted distribution before consuming the prekey, and commit its receiver state together with consumption in one local transaction.
This is the next narrow implementation candidate.

Concurrent selection needs its own policy.
Random selection reduces collisions but cannot guarantee uniqueness, and retry requires a fresh authenticated bundle.
Per-sender prekeys can avoid cross-sender collisions but require explicitly scoped publication and management as devices join or leave.
Retaining a supposedly one-time secret for multiple uses changes its security claim and deletion policy.
An interactive receiver can allocate keys while online, but requiring that for every exchange weakens the existing asynchronous design.
A visible retry after fresh bundle delivery may be acceptable for small human groups; it must not silently pretend that the current shared bundle provides one-time allocation.
No alternative is implemented by these observation tests.
