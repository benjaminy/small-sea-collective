# Follow-up — Issue #138

Items intentionally left for later, with enough context to pick up cold.

## 1. Resolve #123 (member-transport bucket authority) — superseded

Issue #123 asks whether `member_transport_announcement.bucket` should be
authoritative for S3 berth routing. After #138, **berth routing authority lives
entirely in `member_berth_storage_announcement`** (scoped to `(member_id, berth_id)`),
which is the only source the Hub consults for peer storage. `member_transport_announcement`
is now a member-level (non-berth) concept that backs only the Manager web feature
(`announce_transport` endpoint + members listing); it does not drive Hub peer reads.

Recommendation: **close #123 as superseded** by berth-scoped storage announcements,
OR rewrite it into the narrower open question below. (#138 did not edit the GitHub
issue itself; do that or fold it into this file's resolution.)

### Remaining open question: should member-level transport announcements exist at all?

`select_effective_member_transport` / `member_transport_announcement` survive only
to power the Manager members-listing `transport_status` UI. With berth-scoped
announcements now authoritative, the member-level concept may be entirely redundant.
Deciding that is out of scope for #138 (which was about removing the *fallback*, not
retiring the member-transport feature). Track as its own grooming item.

## 2. Per-linked-device berth storage announcements

`finalize_linked_device_bootstrap` no longer writes transport fields to `team_device`
(correct), but it also does **not** publish a `member_berth_storage_announcement`.
This is fine today: a linked device joins an existing member who already announced
their berth storage, so peer discovery of that member still works. It becomes
relevant only if a linked device pushes to a *different* bucket than the member's
announced one. Unlike `create_team`/`accept_invitation`, this path derives a bucket
name via `_bucket_name_for_protocol` rather than calling
`_auto_allocate_berth_cloud_if_available`, so wiring publishing here needs a decision
about the signer identity and whether the derived bucket matches a real allocation.
Belongs with the provisioning redesign (#139) rather than #138.

## 3. Cross-member announcement sync delivery (not new, but now load-bearing)

Each member publishes/commits their own announcement to their own `core.db`/bucket.
For a reader to resolve a *peer's* storage, the peer's announcement must be merged
into the reader's clone — the existing sync layer's responsibility. #138 proves
each role *publishes* correctly (creator end-to-end read; invitee valid signed row),
but the end-to-end "Alice downloads Bob's file" path additionally depends on that
merge, which lives in #139/#140 territory. Worth an explicit integration test once
the peer-merge flow is exercised in CI.
