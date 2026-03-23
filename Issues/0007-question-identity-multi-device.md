---
id: 0007
title: Settle identity model for NoteToSelf and multi-device
type: question
priority: medium
---

## Context

Of the five open architecture questions tracked in `Documentation/open-architecture-questions.md`, the identity/multi-device story is the least resolved. The others (encryption layer shape, Hub↔Manager DB contract, session lifecycle, Cod Sync chain format) are mostly settled.

## Open questions

- How does a user establish identity on a new device? (Key import? QR code? PIN-based device link?)
- What is the exact relationship between the NoteToSelf station and per-device keys?
- When a new device joins, what does it get access to retroactively?
- How are device revocations handled?
- Is the identity model compatible with the Cuttlefish key dimensions (DAILY / GUARDED / BURIED)?
- Multi-device sync for the NoteToSelf station itself: who is authoritative?

## References

- `Documentation/open-architecture-questions.md` — section 5: Identity Model
- `packages/cuttlefish/README.md` — key dimensions and identity design
- `packages/cuttlefish/cuttlefish/identity.py` — current identity implementation
