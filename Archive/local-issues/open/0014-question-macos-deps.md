> Migrated to GitHub issue #7.

---
id: 0014
title: Address macOS-only dependencies
type: question
priority: medium
---

## Context

The project has dependencies on `pyobjus` and `plyer` which are macOS-specific (or have degraded behavior on other platforms). This limits who can run the project and may complicate future deployment or CI.

## Open questions

- Is Linux support a goal? If so, when?
- Are `pyobjus` / `plyer` used for notifications? If so, is there a cross-platform notification fallback strategy?
- Should CI explicitly run on Linux to catch platform-specific regressions?
- Are there other hidden macOS assumptions in the codebase (file paths, keychain access, etc.)?

## References

- `Scratch/suggestions.md` — original source
