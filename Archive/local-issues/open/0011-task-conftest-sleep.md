> Migrated to GitHub issue #17.

---
id: 0011
title: Replace sleep hack in Hub startup fixture
type: task
priority: low
---

## Context

The Hub startup fixture in `tests/conftest.py` uses a 1-second `time.sleep()` to wait for the Hub process to be ready. The comment acknowledges this is a hack.

## Work to do

- Find a reliable readiness signal from the Hub process (e.g., poll a health endpoint, wait for a specific log line, use a socket probe)
- Replace the sleep with a proper wait-until-ready approach
- Avoids flaky tests on slow machines and unnecessary delay on fast ones

## References

- `tests/conftest.py:90`
