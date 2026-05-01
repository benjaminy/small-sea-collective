# Design Record: App Bootstrap Client Helper

## Decision

`small-sea-client` now treats the Hub's structured `409 app_bootstrap_required` body as its own client error:
`SmallSeaAppBootstrapRequired`.
The exception subclasses `SmallSeaError`, not `SmallSeaConflict`, because app provisioning is not a compare-and-swap conflict.

The error parser now inspects the raw top-level JSON body before deriving the generic `detail` fallback.
This is required because the Hub bootstrap response is not wrapped in `detail`.
Non-JSON error responses still fall through to response text, preserving useful generic errors.

## API Shape

The public surface is an exception rather than a result wrapper.
That fits the existing `request_session()`, `start_session()`, and `open_session()` return shapes without restructuring session callers.

The exception preserves `reason`, `app`, and `team`.
`team` may be `None`.
Unknown future reason strings are preserved raw so later branches can add finer-grained reasons without breaking apps that already understand the stable response code.

The exception exposes `user_message` and passes that same text to `Exception.__init__`.
This makes both app display and `str(exc)` logging use the same Manager-oriented instruction.

## Deliberate Non-Share

The current reason strings are duplicated in client micro tests rather than moved into a shared Hub/client constants module.
That keeps package coupling low while still pinning the wire contract.
