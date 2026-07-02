# Design record: team-constitution (phase 1)

Tracks GitHub issue #163 (now a tracking issue; sub-issues #164–#169).
Schema reference: `Documentation/team-constitution.md`.

## What landed

The first vertical slice of the Team Constitution: the shared signing envelope helper plus one complete record type, `integration_mode_change`, end to end — table, signed construction, authorization check, projection update, micro tests.
Chosen because it is the simplest single-signer type in the catalog (no quorum, no PII commitment/payload split, no recovery ceremony) and it directly unblocks #162 (mode-aware replication).

## Choices worth revisiting later

### Interim anchor: `constitution_digest`, not `anchor_frontier`

The design doc's target anchor is record-to-record (`anchor_frontier` + `predecessor_record_id`), but that only becomes realizable once every governance-bearing type is on the shared envelope.
This slice instead anchors with `constitution_digest` — a live-query digest over current teammate/device/berth-role state, generalizing `admission_proposal`'s existing `governance_digest` — with the queried snapshot stored alongside as `constitution_snapshot_json`.
**This is a deliberate stepping stone, not the target mechanism.**
Retiring it is #166; until then, new `integration_mode_change` rows carry an anchor style the rest of the catalog will not keep.

### Direct projection mutation, for now

`set_teammate_integration_mode` (`provisioning.py`) appends the signed record and then updates the `berth_role` projection row directly.
The doc's target is "append record, rebuild projection" via `constitution_projection`; the conversion is explicit scope in #167.
Until #167 lands, the projection row and the record history are kept consistent only by this one write path.

### Envelope helper is not retrofitted onto legacy types

`wrasse_trust.constitution` (`canonical_constitution_bytes`, `derive_record_id`, `sign_constitution_record`, `verify_constitution_record`) generalizes the canonical-bytes idiom previously triplicated in `identity.py`, `transport.py`, and `provisioning.py`.
Only the new record type uses it; migrating `key_certificate` and `teammate_berth_storage_announcement` onto it is #165, kept out of this slice so the new mechanism could be evaluated before touching working legacy paths.

### Mode vocabulary mapping

The record stores the new vocabulary directly (`automatic` | `proposal-only`); the `berth_role` projection still uses the legacy values (`read-write` | `read-only`).
The mapping lives in the one write path.
This asymmetry disappears when #167 rebuilds projections from records.

### Authorization rule

A mode change is valid when the caller's own teammate identity currently holds `automatic` (`read-write`) standing on the target berth.
The doc also sketches "or Core, for berths where Core itself gates mode changes" — not implemented in this slice.

### No placeholder PII commitments (decided during review, recorded in #168)

Committee feedback overturned this branch's original follow-up suggestion to allow placeholder commitment constructions.
Because Constitution records are never deleted, a weak commitment (e.g., unsalted hash) over low-entropy PII is permanently brute-forceable — a bad placeholder is un-fixable, not temporary.
The expected construction is a standard salted commitment (`sha256(salt ‖ canonical payload bytes)`, random ≥128-bit salt stored inside the separable payload and discarded with it); settling it is scope in #168, and the fallback is a nullable, unpopulated commitment column — never a stored stand-in.

### Pre-alpha schema stance

`USER_SCHEMA_VERSION` 61 → 62, `CREATE TABLE IF NOT EXISTS`, no in-place migration; pre-existing team databases are expected to be deleted and recreated.

## Issue restructuring done on this branch

#163 was rewritten from an implement-everything issue into the epic/tracking issue.
Sub-issues filed: #164 (admission onto the envelope, unblocks #162), #165 (legacy-type envelope migration), #166 (`anchor_frontier`), #167 (projection rebuild), #168 (`exclusion` + commitment construction), #169 (`staleness_observation` + retention exemption).
#57 was closed into #167, which inherited its two loose ends (vestigial `member.device_public_key` removal; device-metadata placement).
Prepared recovery and the PII claim types were deliberately left unfiled pending their open design items.
