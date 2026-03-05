
## Testing

The creator of the Small Sea Collective project has an unreasonable dislike of the term unit testing.
Quick tests intended to be run frequently to catch simple mistakes are called micro tests.

## Architecture

**All internet communication goes through the hub.** Applications (including the team manager), cod-sync, and any other packages must never make direct network calls to cloud storage, peers, or external services. The hub is the sole gateway for all traffic that leaves the device. This is what makes transparent end-to-end encryption possible — there is exactly one chokepoint to intercept.
