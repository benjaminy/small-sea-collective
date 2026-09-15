# Stored prekey substitution

The original two local micro tests disclosed the actual sender-key distribution to an attacker who could replace a fetched `device_prekey_bundle` row.
The attacker needed no private key for the trusted device named by the row.
Both matching and mismatching bundle `participant_id` values passed redistribution.
The original result was 2 passed in 1.09 seconds.

The Manager now stores a signed wrapper in the existing JSON column.
The team-device key signs canonical JSON containing version 1, the team ID, the device ID, and the full X3DH bundle, prefixed with `SmallSea/device-prekey-bundle/v1` and a NUL byte.
Redistribution verifies the signature against the independently selected trusted team-device public key before X3DH encryption, and checks both scope fields and the bundle participant ID.
Unsigned rows fail closed; this research change adds no migration path.
An invalid row stops the redistribution call with an error rather than silently skipping the target.
The stored publication timestamp is informational and is not part of authentication.

The revised micro tests reject unsigned rows, attacker signatures, incorrect scopes, incorrect participant IDs, unsupported versions, and mutations of identity keys or prekeys before encryption.
The positive case decrypts the actual sender-key distribution with a separately generated X3DH identity authorized by the target team-device key.
Existing sender rotation and linked-device bootstrap micro tests also exercise legitimate publication and receipt.

```sh
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m pytest -q packages/small-sea-manager/tests/test_sender_key_rotation.py packages/small-sea-manager/tests/test_linked_device_bootstrap.py Experiments/autonomous_bootstrap/prekey_binding/test_prekey_observations.py
```

Result: 30 passed in 19.44 seconds.
These tests use disposable local identities and no network services.

The team-device and X3DH identity signing keys remain separate.
Checking only `participant_id` would not stop substitution because an attacker can copy that public ID.
The existing signed-prekey signature authenticates an X3DH prekey under a signing key supplied by the same untrusted bundle; the new outer signature authenticates that entire bundle.

The signature does not establish freshness or prevent replay of an older authentic bundle for the same team and device.
The current global first-one-time-prekey choice also needs a separate concurrent-sender experiment because concurrent senders can select the same prekey ID.
This change does not alter prekey consumption or add replay tracking.
