# 0016 · bug · TeamManager.connect() calls non-existent method

**Status:** done

## Problem

`TeamManager.connect()` in `manager.py` called `self.client.open_session(...)`.
`SmallSeaClient.open_session()` was subsequently added, but it only works when
the Hub is in auto-approve mode — not suitable for production.

## Fix applied

`connect()` now uses the two-step flow with an explicit `pin_provider` callback:

```python
def connect(self, team="NoteToSelf", pin_provider=None):
    pending_id = self.client.request_session(
        self.participant_hex, "SmallSeaCollectiveCore", team, "TeamManager"
    )
    if pin_provider is None:
        raise RuntimeError("connect() requires a pin_provider callable(pending_id) → pin. ...")
    pin = pin_provider(pending_id)
    self.session = self.client.confirm_session(pending_id, pin)
```

`pin_provider` is a `callable(pending_id) → pin` supplied by the caller.
In tests, a backend-capturing lambda is passed. In production, the Hub sends
the PIN via OS notification; the caller is responsible for collecting it.

Also fixed: `backend.request_session()` now swallows `plyer.notification.notify`
failures (e.g. headless/test environments) so a missing notification centre does
not abort the session request.

Tests added in `test_cloud_roundtrip.py`:
- `test_team_manager_connect` — end-to-end PIN flow through Hub TestClient
- `test_team_manager_connect_requires_pin_provider` — no-provider raises clearly

## Remaining

Production PIN delivery UI (sandbox dashboard, issue 0021) is still needed
for the watcher daemon use case.
