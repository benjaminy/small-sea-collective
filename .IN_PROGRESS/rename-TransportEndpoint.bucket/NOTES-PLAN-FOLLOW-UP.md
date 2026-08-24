# Notes

GitHub issue #213 follows #210 and renames the internal berth-routing field `TransportEndpoint.bucket` to `location`.
This is a terminology cleanup, not a schema or protocol change.
`canonical_teammate_berth_storage_announcement_bytes` already signs `location`, and the Manager route projection already emits `location`, so no signature, database, or API payload changes.
No compatibility alias should remain.

There is one field declaration and six live uses:
the declaration and its only construction in `wrasse_trust/transport.py`;
one assertion in `wrasse-trust/tests/test_transport.py`;
publication dedupe and the Core-route projection in `small_sea_manager/provisioning.py`;
peer routing and the own-storage check in `small_sea_hub/backend.py`.
Renaming a Python attribute breaks every consumer at once, so this is a single atomic edit, not a sequence of independently valid states.

Two comments exist only to explain the current mismatch and go with it.
The `provisioning.py` note that the selector's `bucket` field carries the announcement's `location` should be deleted outright rather than reworded.
The `backend.py` dropbox note that refers to `peer.bucket` should name the renamed field.

The Hub's local variable chain `bucket` -> `bucket_name` / `folder_prefix` is renamed to `location` as well.
The dropbox branch is the reason: the value there is a folder prefix, not a bucket.
Leaving the local named `bucket` would satisfy the rename literally while preserving exactly the terminology the issue removes.

## Out of scope

These carry `bucket` for reasons unrelated to berth routing and stay as they are:

- The Hub bootstrap-session descriptor: `BootstrapSessionCreateReq.bucket`, the `bootstrap_session.bucket` column, and its description in `packages/small-sea-hub/spec.md`.
- The `bucket` parameter of `proxy_cloud_file` and the `/cloud_proxy` query parameter behind it.
- Provider-facing S3 bucket names in the adapters, `cod-sync`, and the MinIO-backed tests.
- Archived design records.

# Plan

1. Rename the field and all six uses in one edit, across `wrasse_trust/transport.py`, `wrasse-trust/tests/test_transport.py`, `small_sea_manager/provisioning.py`, and `small_sea_hub/backend.py`.
   Delete the stale `provisioning.py` comment, update the `backend.py` dropbox comment, and rename the Hub locals.
   Verify: `grep -rn "transport\.bucket" packages/` returns nothing.
2. Run the affected micro tests:
   `uv run pytest packages/wrasse-trust/tests/test_transport.py packages/small-sea-manager/tests/test_core_route_status.py packages/small-sea-manager/tests/test_publish_core_state.py packages/small-sea-hub/tests/test_peer_transport.py`.
   The Hub peer-transport tests spawn a local `minio` binary.
   If it is unavailable, report that rather than treating a skipped run as a pass.
3. Review the remaining `bucket` hits under `packages/` against the out-of-scope list.
   Verify: every survivor is unrelated to `TransportEndpoint` berth routing.

# Follow-up

Reviewing the surviving `bucket` hits exposed an unrelated issue.
The SQLAlchemy `TeamDevice` model in `small_sea_manager/provisioning.py` still declares `protocol`, `url`, and `bucket` columns.
The raw DDL that actually creates `team_device` omits them, and `test_create_team.py` and `test_peer_transport.py` assert they are absent.
The model declaration is dead and contradicts issue #138.
Left in place; worth a separate small cleanup.

No documentation changes were needed.
The `bucket` mentions in `packages/small-sea-hub/spec.md` describe the bootstrap-session descriptor.
The ones in `packages/small-sea-manager/spec.md` describe provider-facing S3 names and the already-removed `team_device` columns.
