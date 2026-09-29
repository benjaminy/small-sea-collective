# What each bootstrap transcript proves today (#294)

Citations are to `packages/small-sea-manager/small_sea_manager/` on main (480c4fe).
A local model gathered the first draft; every row below was rechecked against the code.

Summary: on all three paths the receiver fetches or imports history before it holds any key that did not come from that history or from the channel that delivered the artifact.
No path verifies commit signatures on ancestors of the head.
`CoreHistoryVerifier` (`git_verification.py:15`) is wired only into post-bootstrap Core fetch and publish (`manager.py:1387`, `1644`, `1697`).

## 1. Identity bootstrap (new device joins an existing identity)

Entry points: `manager.bootstrap_existing_identity` (`manager.py:209`, adds a Hub bootstrap-session store for non-local remotes) and `provisioning.bootstrap_existing_identity` (`provisioning.py:2500`, local-folder remotes).
Both call `prepare_identity_bootstrap` (`provisioning.py:2347`), fetch, adopt, then `finalize_identity_bootstrap` (`provisioning.py:2437`).

| Step | What happens | Citation |
| --- | --- | --- |
| Artifact | Sealed `SignedWelcomeBundle`: identity label, remote descriptor, authorizing device label, expiry. Sealed to the joiner's encryption key. | `provisioning.py:2311-2336` |
| Signature | Authorizing device signs the bundle plaintext with its `user_device` signing key. The bundle names the signer by device id, not by public key. | `provisioning.py:2288`, `2319`, `2324` |
| How the receiver learns the signer key | It reads `user_device.signing_key` from the fetched NoteToSelf database. | `provisioning.py:2448` |
| Human evidence | Two comparison strings: a hash of the join request, and a hash of request, bundle and signature. Neither covers the signer's public key. | `provisioning.py:2338`; `small_sea_note_to_self/bootstrap.py:150-160` |
| Checks before fetch | Open the seal; device id and encryption key match the pending request; expiry. No signature check. | `provisioning.py:2362-2377` |
| Fetch | `CodSync.fetch()` from the descriptor inside the bundle. No verifier. | `provisioning.py:2507`; `manager.py:233` |
| Import | `adopt_fetched_source` checks out the fetched head into the unborn repo. It checks tree shape, not signatures. | `provisioning.py:2508`; `note_to_self_sync.py:392`, `450-472` |
| Checks after import | Bundle signature against the fetched signer row; fetched joining-device row equals the pending request's keys. On failure the participant is marked untrusted, but the imported history stays on disk. | `provisioning.py:2448-2478`, `1802` |
| History checked | None beyond the head's database contents. | |

## 2. Invitation (new teammate accepts)

Entry point: `provisioning.accept_invitation` (`provisioning.py:5925`).

| Step | What happens | Citation |
| --- | --- | --- |
| Artifact | Unauthenticated base64 token: inviter cloud and bucket, inviter sender key, proposal id, nonce, `authority_anchor`, `authority_anchor_signature`. | `5948-5961` |
| Signature | The inviter signs a subset of fields (proposal id, nonce, ids, sender key id, anchor) with the inviter's team device key. | `6094-6104` |
| How the receiver learns the signer key | From the cloned Core: certificates in `core.db`, walked from each self-issued genesis membership certificate. The token's anchor must equal the one matching genesis subject key. | `6046-6075`, `6107` |
| Fetch | `CodSync.fetch()` into a fresh repo from the token's cloud, no verifier. | `5983-5985` |
| Import | Checkout of the fetched head. The signed proposal's `anchor_commit` (selected at `6034`) is never compared with the head. | `5993` |
| Side effects before verification | NoteToSelf `team` row inserted; local team device key generated. Only the clone step is inside the cleanup `try`, so a later verification failure leaves both, plus the clone directory. | `6009`, `6018`, `5990-5996` |
| Checks after import | Proposal signature and match to token; unique genesis; token signature; anchor equality. Anchor recorded only afterward. | `6041-6107`, `6112` |
| History checked | None. The chain check reads certificate rows at the head, not commit signatures. | |

The token is the only pre-fetch evidence, and it is bound to the history only through the check at `6107`.
A hostile token deliverer supplies token, cloud and Core together.

## 3. Linked device (same teammate, new device, existing team)

Entry point: `provisioning.finalize_linked_device_bootstrap` (`provisioning.py:2867`).

| Step | What happens | Citation |
| --- | --- | --- |
| Artifact | Signed response with team id, signer key, Core berth id, `core_head`, X3DH and ratchet messages, device-link certificate, authority anchor. | `2899-2903`, `2905` |
| Signature | The sibling's team device key signs the canonical response. The Core git bundle is not in the signed body; it arrives inside the ratchet-encrypted payload and is tied to the signature only through `core_head`. | `2905`, `2930-2932`, `3015` |
| How the receiver learns the signer key | With no local Core: from NoteToSelf `team_device_key` rows (`_nts_team_device_public_keys`, `2663-2671`), which come from unverified NoteToSelf (path 1). With a local Core: from that Core's certificates. | `2919-2926` |
| Checks before import | Version, team id, signer in trusted set, signature, session row, device-link certificate. Then the anchor is recorded. | `2891-2947` |
| Import | Bundle imported and checked out only on cold start; the head must equal the signed `core_head`. | `3011-3016`, `2687-2693` |
| Checks after import | Berth id equals signed berth id; signer trusted by the imported Core's certificates. Failure deletes the clone. | `3020-3028` |
| History checked | None. The docstring says so. | `2883` |

Here the receiver already holds the anchor from an authenticated response before the bundle is imported, so a `CoreHistoryVerifier` can be built at that point.

## 4. NoteToSelf, all paths

- No anchor or certificate chain exists for `user_device.signing_key` rows.
- `authorize_identity_join` inserts the joiner's row locally and commits it (`provisioning.py:2273-2296`).
- Later NoteToSelf integration merges rows after structural checks in `adopt_source` (`note_to_self_sync.py:371`); no signature check.
