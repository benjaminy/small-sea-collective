# 0016 · bug · TeamManager.connect() calls non-existent method

**Status:** open

## Problem

`TeamManager.connect()` in `manager.py` calls `self.client.open_session(...)`, but `SmallSeaClient` has no `open_session` method. The correct flow is the two-step `request_session()` / `confirm_session()` pair.

```python
# manager.py:26-30 — currently broken
def connect(self, team="NoteToSelf"):
    self.session = self.client.open_session(   # ← method does not exist
        self.participant_hex, "SmallSeaCollectiveCore", team, "TeamManager"
    )
```

`SmallSeaBackend.open_session()` exists but is a smoke-test shortcut (only valid when `client="Smoke Tests"`). The real client does not expose it.

## Impact

Any call to `TeamManager.connect()` will raise `AttributeError` at runtime. Currently `connect()` is never called in production code paths (invitation flow, CLI commands) so it hasn't surfaced, but it will need to work before sync is wired up (issue #0015).

## Fix

Rewrite `connect()` to use the two-step session flow:

```python
def connect(self, team="NoteToSelf"):
    pending_id, pin = self.client.request_session(
        self.participant_hex, "SmallSeaCollectiveCore", team, "TeamManager"
    )
    # In production: surface the PIN to the user via OS notification.
    # In tests: pass pin through directly.
    self.session = self.client.confirm_session(pending_id, pin)
```

How the PIN reaches the user (OS notification vs. test fixture) needs to be decided as part of #0015.
