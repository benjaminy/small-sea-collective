> Migrated to GitHub issue #10.

---
id: 0027
title: Cloud storage configuration in the Manager web UI
type: task
priority: medium
---

## Context

This issue is partially implemented and needs to be updated to reflect the
current Manager UI.

What is already present:

- The Manager index page has a "Cloud storage" card.
- `TeamManager` exposes `add_cloud_storage`, `list_cloud_storage`, and
  `remove_cloud_storage`.
- The web UI exposes `GET /cloud-storage`, `POST /cloud-storage`, and
  `POST /cloud-storage/{id}/remove`.
- The current fragment supports adding S3 / MinIO credentials and removing
  existing providers.

This means the original "no way to configure cloud storage from the Manager web
UI" statement is no longer true for the S3 / MinIO path.

What remains is finishing the more polished and broader provider story, most
notably Dropbox OAuth.

## Phases

### Phase 1 — S3 / MinIO form polish

The basic S3 / MinIO flow is already implemented and usable for local dev and
MinIO-backed demos.

Remaining follow-up questions:

- Should the UI enforce a single active provider, or is multiple-provider
  support intentional?
- Should "replace" be a first-class action, or is remove-then-add sufficient?
- Should the route shape be normalized later (for example true `DELETE`) or is
  the current htmx-friendly `POST .../remove` fine?

This phase is no longer the primary missing work.

### Phase 2 — Dropbox OAuth callback

Dropbox uses a browser-redirect OAuth flow. The Manager is already running as a
local HTTP server, so it can host the callback endpoint:

1. `GET /oauth/dropbox/start` — redirect the browser to the Dropbox auth URL
   (built from `client_id` stored in a config field or entered by the user).
2. Dropbox redirects back to `GET /oauth/dropbox/callback?code=…`.
3. The callback exchanges the code for tokens and calls `add_cloud_storage` with
   `protocol="dropbox"` and the resulting `refresh_token` / `access_token` /
   `token_expiry`.

The Manager's redirect URI must be registered in the Dropbox app settings
(e.g. `http://localhost:8001/oauth/dropbox/callback`). The app key and secret
can be entered in the UI before starting the flow, or pre-configured in
`~/.config/small-sea/manager.toml`.

This appears to be the main missing feature in this issue. The provisioning
layer and Hub already have Dropbox-related fields and token-refresh support,
but the Manager web UI does not yet expose a Dropbox start/callback flow.

### Phase 3 — Additional providers

Once the S3 form and Dropbox OAuth path exist, further providers (Google Drive,
OneDrive, etc.) can be added by implementing the appropriate OAuth route or
credential form and wiring into the existing `add_cloud_storage` provisioning
function.

## Not in scope

- Validating credentials by making a test upload (useful but separable).
- Multi-provider round-robin or failover (issue 0024 covers the adapter layer).
- Token refresh UI (tokens are refreshed transparently by the Hub backend).

## References

- `packages/small-sea-manager/small_sea_manager/templates/fragments/cloud_storage.html`
  — current S3 / MinIO form and provider list
- `packages/small-sea-manager/small_sea_manager/manager.py` —
  `add_cloud_storage`, `list_cloud_storage`, `remove_cloud_storage`
- `packages/small-sea-manager/small_sea_manager/provisioning.py` —
  `add_cloud_storage`
- `scripts/setup_dropbox_auth.py` — existing Dropbox OAuth flow (for reference)
- `packages/small-sea-manager/small_sea_manager/web.py` — web UI to extend
- Issue 0024 — cloud storage adapter abstraction in the Hub backend
