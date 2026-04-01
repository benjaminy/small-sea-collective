---
id: 0027
title: Cloud storage configuration in the Manager web UI
type: task
priority: medium
---

## Context

There is currently no way to configure cloud storage from the Manager web UI or
CLI. `add_cloud_storage` exists in the provisioning layer and is called by the
setup scripts (`setup_dropbox_workspace.py`, `setup_dropbox_auth.py`), but the
`TeamManager` class and the web UI do not expose it. A user starting fresh must
drop to a script to wire up their cloud provider before any sync can happen.

## Phases

### Phase 1 — S3 / MinIO (simple credential form)

S3-compatible providers (including the local MinIO dev server) only need static
credentials: endpoint URL, access key, and secret key. No OAuth dance required.

Add to the Manager web UI:
- A "Cloud storage" card on the index page showing the currently configured
  provider(s) (protocol, URL, masked credentials).
- A form to add an S3/MinIO provider: endpoint URL, access key, secret key.
- A remove/replace action (one active provider at a time is the common case).

Backend:
- Expose `add_cloud_storage` / `list_cloud_storage` / `remove_cloud_storage` on
  `TeamManager` (thin wrappers over provisioning).
- Add corresponding `POST /cloud-storage` and `DELETE /cloud-storage/{id}`
  routes in `web.py`.

This unblocks local dev and MinIO-backed demo setups without any OAuth work.

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

- `packages/small-sea-manager/small_sea_manager/provisioning.py` —
  `add_cloud_storage`
- `scripts/setup_dropbox_auth.py` — existing Dropbox OAuth flow (for reference)
- `packages/small-sea-manager/small_sea_manager/web.py` — web UI to extend
- Issue 0024 — cloud storage adapter abstraction in the Hub backend
