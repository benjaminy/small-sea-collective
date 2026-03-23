---
id: 0010
title: Add permission checks to Hub cloud location methods
type: task
priority: low
---

## Context

Two methods in the Hub backend have TODOs noting that permission checks are probably needed but not yet implemented. There's also an unhandled edge case when a cloud location query returns multiple results.

## Work to do

- `_register_cloud_location()`: decide what permissions are required and add checks (line 464)
- `_get_cloud_link()`: same (line 482)
- `_get_cloud_link()`: handle the case where `len(results) != 1` — currently prints a TODO and falls through (line 488). Should this raise? Return an error? Which cases are actually possible?

## References

- `packages/small-sea-hub/small_sea_hub/backend.py:464,482,488`
