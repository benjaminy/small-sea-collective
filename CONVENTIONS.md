
## Testing

The creator of the Small Sea Collective project has an unreasonable dislike of the term unit testing.
Quick tests intended to be run frequently to catch simple mistakes are called micro tests.

## Architecture

**All internet communication goes through the hub.** Applications (including Small Sea Manager), cod-sync, and any other packages must never make direct network calls to cloud storage, peers, or external services. The hub is the sole gateway for all traffic that leaves the device. This is what makes transparent end-to-end encryption possible — there is exactly one chokepoint to intercept.

**Only the Manager reads the SmallSeaCollectiveCore database directly.** The `{team}/Sync/core.db` SQLite database is an internal implementation detail of the Manager. Other apps must obtain session identity information (station ID, team name, participant hex, etc.) through the Hub API — specifically `GET /session/info`. Direct SQLite reads from outside the Manager package are forbidden.
