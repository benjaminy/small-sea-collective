# A delayed distribution replaces a newer sender chain

A valid old sender-key distribution can replace newer receiver state when it arrives for the first time after a rotation.
Atomic prekey consumption prevents duplicate receipt but does not establish ordering between two authentic distributions using different prekeys.

## Reproduction

From the repository root:

```sh
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m pytest -q Experiments/delayed_sender_key
```

Observed: **2 passed**.
These are passing observations of a gap, not regression assertions that the behavior is safe.

1. Alice creates a distribution for her current sender chain; retain it without delivery.
2. Alice rotates her chain and creates another distribution for Bob using a different advertised one-time prekey.
3. Bob receives the new distribution successfully; his stored chain is the new chain.
4. Bob receives the delayed old distribution successfully; his stored chain becomes the old chain.
5. Exact replay of the last artifact fails because its prekey is consumed.

The control delivers old then new and ends on the new chain.
Both cases inspect the stored `peer_sender_key.chain_id` after each receipt.
All artifact signatures, encryption and receiver checks run normally.

## Simplification

The sender-side helper selects the second advertised prekey for the newer artifact.
The current default selects the first prekey for both artifacts; that known collision would reject the second receipt and mask this separate issue.
The selection helper neither changes Bob's private keys nor patches receiver validation.
It models a valid choice available to a sender, and a condition a future allocation fix will make ordinary.

This does not demonstrate secret disclosure or a forged sender.
It demonstrates replacement of newer receiver state by an authentic delayed message.
It does not yet measure the effect on later application-message decryption or lost skipped keys.

## Why receipt atomicity does not solve this

`receive_sender_key_distribution` authenticates and decrypts before conditionally consuming the selected prekey.
It then saves the receiver record in the same transaction.
`save_peer_sender_key` uses `INSERT OR REPLACE` for that sender's row, with no chain-supersession check.
Two unused prekeys establish two distinct receivable messages; they do not tell Bob which chain supersedes which.
A random chain ID and sender-provided wall-clock time do not independently establish that order.

A next experiment should compare an explicit signed supersession relation with preserving both chains and pausing an ambiguous replacement.
That comparison must include a device receiving a chain for the first time and concurrent rotations; a monotonic local counter alone should not be assumed to settle either case.
No runtime fix or project policy is chosen here.
