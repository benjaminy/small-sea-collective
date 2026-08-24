Rename `TransportEndpoint.bucket` to `location` (#213)

The berth-routing selector field is renamed to match the terminology already used by the signed announcement, the Core-route projection, and the berth cloud allocation.
Terminology only: no schema, signature, or API payload changes, and no compatibility alias.

The Hub's peer-download local variable chain (`bucket` -> `bucket_name` / `folder_prefix`) collapses to a single `location` local.
On the Dropbox path, the value is a folder prefix rather than a bucket.
The `bucket` names that remain under `packages/` belong to the Hub bootstrap-session descriptor, `/cloud_proxy`, and provider-facing S3 bucket names.
They are unrelated to berth routing and are unchanged.
