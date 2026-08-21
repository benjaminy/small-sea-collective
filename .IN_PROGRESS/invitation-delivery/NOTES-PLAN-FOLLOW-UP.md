# Notes

Branch for issue #183: deliver the invitee's first berth storage announcement to the inviter.

## The gap, restated from the code

After #137 and #138, a signed `teammate_berth_storage_announcement` is the only source of peer storage routing.
`Hub._download_peer_file` resolves through `select_effective_teammate_berth_storage` and raises `SmallSeaNotFoundExn` when no valid announcement exists (`packages/small-sea-hub/small_sea_hub/backend.py:1596`).

`accept_invitation` already publishes the acceptor's own announcement into the acceptor's clone and commits it (`packages/small-sea-manager/small_sea_manager/provisioning.py:5137`).
The `admission_acceptance` blob carried back to the inviter does not include it: the signed field set is `subject_record_id`, `nonce`, `invitee_device_public_key`, `invitee_bootstrap_key` (`provisioning.py:959`).
So after `complete_invitation_acceptance` the inviter holds the acceptor's teammate row, `team_device` row, and membership cert, but no route to the acceptor's storage.

The ordering is circular: fetching the acceptor's Core chain to obtain the announcement requires already knowing the acceptor's storage.
First contact has no bootstrap.

## Why the acceptance courier is the right carrier

`select_effective_teammate_berth_storage` accepts an announcement only when its `signer_key_id` maps to a device public key that `trusted_device_keys_for_teammate` derives from the team's cert history (`packages/wrasse-trust/wrasse_trust/transport.py:189`).
`complete_invitation_acceptance` issues exactly that membership cert for `invitee_device_public_key`, and the acceptance's self-certifying verify already proves the acceptor holds that key (`provisioning.py:5234`).
Carrying the announcement alongside the acceptance therefore adds no trust that the acceptance does not already establish.
The inviter verifies the announcement itself and never trusts the courier.

Once inserted, the announcement is an ordinary row in the shared team `core.db`.
The inviter's next push republishes it, so the rest of the team gets the acceptor's route too, without any further first-contact problem.

## Positions on the issue's four open questions

These are proposals, not settled decisions.
Confirm before implementation starts.

**Q1 — sidecar, not a signed field.**
The announcement is already independently signed with the same key the acceptance proves, so binding it into the acceptance signature adds nothing cryptographically.
The decisive argument is durability: `admission_acceptance` is an immutable membership fact, storage location is mutable, and pinning one routing snapshot inside the membership record mixes the two.
This also keeps the canonical bytes and `record_id` derivation for `admission_acceptance` untouched, which #164 recently settled, and keeps the announcement free to be re-canonicalized by #165 without disturbing admission records.

**Q2 — require an allocation before acceptance succeeds.**
Today `accept_invitation` publishes an announcement only when `_auto_allocate_berth_cloud_if_available` finds storage (`provisioning.py:5133`).
An acceptor who joins without storage is admitted and then unreachable, with no later channel that repairs it — the pull that would fix it has the same first-contact dependency.
Rather than admitting a provably unreachable member, `accept_invitation` should fail early with a typed "register cloud storage first" error.
This mirrors the Hub's existing own-storage gate on `/cloud_file` (`backend.py:_require_own_storage_announcement`) and removes the unreachable-member state instead of deferring it.
Alternative if that is judged too strict for research use: accept without a sidecar, and file the reachability repair as a follow-up issue.

**Q3 — a bad announcement rejects the whole acceptance.**
The acceptance is one courier-transported blob; a part that fails verification means the blob is not trustworthy input.
Admitting-and-discarding produces exactly the state this issue exists to eliminate — a member the inviter cannot reach — and hides a tampering signal behind a successful admission.
Fail loud.

**Q4 — Core only.**
`accept_invitation` materializes only the `SmallSeaCollectiveCore` berth (`provisioning.py:5120`); other berths are expanded inviter-side at finalization by `_expand_mode_plan_at_finalization`.
Core routing is what unblocks everything else, and once it exists other berths announce through normal sync.

## Adjacent issues

- #185 explicitly lists "invitation first-contact delivery, tracked in #183" as out of scope and uses a routing fixture, so the two are independent and #185 is not a prerequisite.
- #150 wants a cross-member delivery witness through the *sync* path.
  This branch's integration test proves delivery through the *acceptance* path.
  They are different mechanisms; #150 should stay open, but its framing may want a note that first contact is now covered separately.
- #165 will migrate `teammate_berth_storage_announcement` onto the core event envelope.
  The sidecar choice in Q1 keeps that migration from touching admission records.
- #123 (deferred) concerns the older member-level `teammate_transport_announcement` and is unaffected.

## Open uncertainties

- If quorum is greater than one, `complete_invitation_acceptance` records the acceptance without finalizing, so no membership cert exists yet and the acceptor is not a teammate.
  Does the announcement get inserted immediately (harmless — selection rejects it until a cert exists) or only on the finalization branch?
  Leaning immediate, since the row is inert without a cert and re-deriving it later needs the sidecar to be persisted somewhere anyway.
- Idempotency: once peer Core pull lands (#185), the acceptor's own chain carries the same `announcement_id`.
  The inviter-side insert must not later collide on the primary key.

# Plan

Confirm the Q1–Q4 positions above before step 1.

1. Extend the acceptance blob with an optional `berth_storage_announcement` sidecar in `accept_invitation`.
   Emit the full signed row — `announcement_id`, `teammate_id`, `berth_id`, `protocol`, `url`, `location`, `announced_at`, `signer_key_id`, `signature` — read back from the row just published, not reconstructed.
   → verify: micro test asserts the sidecar's fields are byte-identical to the acceptor's own `teammate_berth_storage_announcement` row, and that `admission_acceptance` `record_id` and canonical bytes are unchanged from before the branch.

2. Decide and implement the no-allocation case per Q2.
   → verify: micro test that acceptance without a registered berth cloud raises the typed error (or, under the alternative, produces a sidecar-free acceptance that still verifies and completes).

3. Add inviter-side verification in `complete_invitation_acceptance`, before any insert.
   Checks: signature verifies against the embedded `invitee_device_public_key` via `verify_teammate_berth_storage_announcement_signature`; `signer_key_id == key_id_from_public(invitee_device_public_key)`; `teammate_id == author_teammate_id`; `berth_id` exists in the inviter's `team_app_berth`.
   Any failure raises before the transaction touches the team DB.
   → verify: one micro test per check, each asserting both the raised error and that the inviter's team DB is unchanged.

4. Insert the verified row inside the existing acceptance transaction, idempotently on `announcement_id`.
   Insert the received bytes verbatim rather than re-signing or re-deriving.
   → verify: micro test asserts the inviter's row equals the acceptor's row field-for-field, including `announcement_id` and `signature`.

5. Integration witness in `packages/small-sea-manager/tests/test_invitation.py`, extending the existing two-MinIO Alice/Bob fixture.
   After `complete_invitation_acceptance` and no other delivery step, Bob pushes a file through the Hub and Alice reads it through the peer path.
   → verify: the read succeeds only through `select_effective_teammate_berth_storage`; the test fails if the announcement insert is removed.

6. Update `packages/small-sea-manager/spec.md` (acceptance record shape, first-contact delivery) and `packages/small-sea-hub/spec.md` if the peer-routing narrative claims sync is the only delivery path.
   → verify: grep the specs for statements that first contact has no bootstrap and confirm none survive stale.

## Validation story

For a skeptic asking "is the goal accomplished":
the integration test in step 5 is the load-bearing one.
It runs the real flow with two separate participants, two separate MinIO buckets, and no manual row insertion or fixture-injected routing, and it fails if step 4's insert is deleted.
Negative tests in step 3 show that every rejection path leaves the inviter's team DB untouched, so no unverified insert path is introduced.

For a skeptic asking "is repo integrity maintained":
no new trust path is added — verification reuses `verify_teammate_berth_storage_announcement_signature` from `wrasse-trust` and the membership-cert trust the acceptance already establishes.
No new table, no new endpoint, no change to `admission_acceptance` canonical bytes or `record_id` derivation.
The Hub is untouched; delivery is Manager-side, consistent with Manager database exclusivity.
Step 1's assertion that `admission_acceptance` canonical bytes are unchanged is the concrete guard that #164's settlement was not disturbed.

# Follow-up

- If Q2 takes the alternative (accept without an allocation), file an issue for repairing reachability of a member admitted without storage.
- Consider a note on #150 clarifying that first-contact delivery is covered here and that #150 remains the sync-path witness.
- Confirm with #165 that the sidecar payload is understood to re-canonicalize with the announcement, not with the admission record.
