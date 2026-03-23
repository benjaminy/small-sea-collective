---
id: 0005
title: Fill in Hub spec gaps
type: spec
priority: medium
---

## Context

`packages/small-sea-hub/spec.md` has nine TODO placeholders where sections haven't been written yet. These block collaborators from understanding how the Hub works.

## Work to do

- Session lifecycle: opening, approval, duration, expiry (line 30)
- Shared database contract: which tables the Hub reads, where they live on disk (line 37)
- Cloud storage adapters: S3, Google Drive, Dropbox — auth models (line 46)
- Credential storage: likely to change (keyring, vault, encryption-layer integration) (line 53)
- Notifications: not yet implemented — how Hub routes to correct apps/teams (line 57)
- VPN/P2P: not yet implemented — how Hub negotiates connections between devices (line 61)
- Encryption layer: transparent encrypt/decrypt for outbound/inbound data (line 65)
- HTTP API: endpoints, request/response formats, error handling (line 69)
- Local database schema and on-disk directory layout (line 75)

Some of these describe features that aren't implemented yet — those sections can be marked as speculative/planned rather than left blank.

## References

- `packages/small-sea-hub/spec.md`
- `Documentation/open-architecture-questions.md` — settled decisions that can inform some sections
