# GitHub follow-up plan

No GitHub issue was changed during planning, experiments, or handoff preparation.
The user accepted the implementation direction on 2026-09-10; [plan.md](plan.md) records the settled scope.

Prepare the following update to [#261](https://github.com/benjaminy/small-sea-collective/issues/261) alongside implementation, then attach the final evidence for the human handoff:

- The ordinary-team publication claim and expected-writer model, including sibling devices and retained-candidate reads.
- The chosen logical-path and format rules.
- The context field set and why it was chosen: object coordinates are separate from independently established device ownership.
  Both minimal and publisher-bearing contexts can test the ownership boundary; the latter needs a validly signed false publisher claim.
- Which local store answers device ownership for each session kind, and what an absent projection means as an outcome.
- Missing ownership pauses dependent acceptance, including new-invitee own reads; unrelated operations with satisfied prerequisites can continue.
  No provisional plaintext consumer or automatic recovery system is included.
- A route-by-route contract, adding retained-candidate inspection to the issue's inventory.
- Explicit limits for passthrough, proxy, bootstrap, runtime artifacts, and notification hints.
  NoteToSelf retains its existing passthrough contract; adding its missing encrypted-publication lifecycle is outside this branch.
- The #264 sequencing decision to implement on today's Sender Keys construction, the bounded-change evidence behind it, and the association evidence assumed pending #262/#266.

After implementation, prepare validation evidence and remaining limits for the human's issue/PR review before proposing closure of #261.
Name the tested commands, passing controls, state snapshots, and HTTP/client failure behavior.
Clarify that internal AEAD opening may precede the context comparison, but ordinary plaintext release and receiver-state commit may not.
Do not claim that publication-context binding proves provider upload identity, freshness, Git authorship, or membership policy.

If investigation confirms independent gaps, propose focused follow-ups rather than silently expanding this branch.
The local experiments establish that fresh NoteToSelf has no group sender-key provisioning; the accepted decision is to preserve its separate passthrough contract here.
Check existing issues and prepare a focused follow-up for its encrypted-publication lifecycle if no suitable issue exists; do not fold that implementation into #261.
An invitee's fetched proposal snapshot also lacks its own ownership row despite local sender-key provisioning; explain which dependent operations wait for accepted admission evidence rather than treating this normal intermediate state as corrupt storage.
In particular, runtime redistribution's expected-source binding and prekey-consumption ordering need attention if they remain outside the agreed route contract.
The prekey-consumption ordering in `receive_sender_key_distribution` (a one-time prekey is consumed before later decrypted-distribution checks) is a separate defect; prepare a focused issue for the human to publish rather than leaving it only in this file.
Check existing issues before proposing a duplicate; #264 already covers the ratchet/storage mismatch, and #262/#266 already cover broader trust-source and key-scope questions.

## After implementation — 2026-09-10

Implementation is complete; the evidence for the #261 update is in [notes.md](notes.md#implementation--2026-09-10) and the public contract is now in `packages/small-sea-hub/spec.md`.
Add to the update prepared above:

- The chosen wire shapes: context `["small-sea/object-publication", 1, team hex, berth hex, path]` as compact UTF-8 JSON, envelope marker `small-sea/group-publication/1`, and the domain-separated length-prefixed transcript.
  Older unbound envelopes are refused rather than read on the weaker contract.
- The read-outcome table and its status codes, including that the missing-sender-key wire code changed from `peer_sender_key_unavailable` to `sender_key_unavailable` now that own and candidate reads report the same condition.
- That the bounded-change test held: no retention, back-fill, or migration machinery was needed, so #264 sequencing does not reopen.
- The limits named in the notes: substituted provider I/O rather than a provider attack, a synthetic mapping for the ambiguity branch, and no provider integration run.

Two focused issues to prepare for the human to publish, neither folded into #261:

- `SmallSeaBackend._device_public_keys_by_key_id` returns an empty mapping when `team_device` is absent, which quietly weakens storage-announcement verification the same way an empty ownership mapping would have weakened publication.
  This branch left that pre-existing shortcut alone and made the publication path treat an absent projection as its own outcome instead.
- The prekey-consumption ordering in `receive_sender_key_distribution` (a one-time prekey is consumed before later decrypted-distribution checks), together with its missing expected-source binding.
  Both were already named above; they remain outside the agreed route contract.

The NoteToSelf encrypted-publication lifecycle issue described above is still worth checking for and preparing.
The implementation confirmed the diagnosis without changing anything about that path: `_device_ownership_by_key_id` returns an absent projection for NoteToSelf, and an encrypted-mode read of its passthrough bytes is refused rather than downgraded.
