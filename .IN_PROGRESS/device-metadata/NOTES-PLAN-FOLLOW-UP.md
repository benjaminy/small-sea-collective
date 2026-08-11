# Notes

Branch scope: issue #175, carried out of #167 (closed by the Constitution reset, #163).
Two independent items: remove vestigial `member.device_public_key`, and decide where device metadata belongs.

## Item 1 is already done — nothing to remove

Issue #175 says `member.device_public_key` still exists and is still written by `create_team` and `complete_invitation_acceptance`.
That is stale.
The column was removed on branch `issue-59-peer-device-model` (PR #77, commit `f330fee`, 2026-04-13), in the same change that replaced `peer` with `team_device`.
`member` was later renamed `teammate` (`8c53246`).

Current state, verified:
- `teammate` in `sql/core_other_team.sql` is `(id, display_name, identity_public_key)`.
   No device column.
- `_upsert_teammate_row` (provisioning.py:1516) writes only those three columns.
- Exact searches for `member.device_public_key`, `teammate.device_public_key`, and a device-key column on either table find no current implementation.
   Broader substring searches still find unrelated operational names such as `team_device_public_key`,
   `invitee_device_public_key`, `peer_team_device_public_key`, and `device_public_keys_by_key_id`,
   plus tests, comments, Cod Sync parameters, and archived design records.

Consequences: no `USER_SCHEMA_VERSION` bump is owed for item 1, and the issue text needs correcting rather than acting on.

## Item 2: where device metadata belongs

### Storage tiers that exist today

Any placement decision has to name one of these.
The distinctions that matter are *who converges on it* and *whether it is durable in Git history*.

| Tier | Path | Replication | Durability |
|---|---|---|---|
| Team Core DB | `{Team}/Sync/core.db` | all teammates | durable in team Git history; teammates keep independent clones |
| NoteToSelf Sync DB | `NoteToSelf/Sync/core.db` | this participant's own devices | durable in NoteToSelf Git history |
| Device-local DB | `NoteToSelf/Local/device_local.db` | none | rebuildable, disposable |
| Hub-local DB | Hub's own SQLite | none | disposable, session-lifetime-ish |

Two properties of the top tier drive most of the decision:
a durable synced row is effectively unerasable (every teammate's clone plus Git history),
and a synced row that changes frequently costs a commit per change in the team's Git history.

### The three fields from #57, classified

**Device label / nickname, self-assigned, for your own devices** ("Ben's laptop").
Purpose is helping *you* tell your own devices apart in your own Manager UI.
Nothing in the trust model consults it.
→ **NoteToSelf Sync DB**, as a column on `user_device`.
Durable, converges across your own devices, never reaches teammates.

There is a concrete existing defect here.
`authorizing_device_label` in the device-link welcome bundle (provisioning.py:2274) is populated from
`get_nickname(...)` — the *participant* nickname, not a device label.
The new device therefore shows the person's name where it claims to show which device authorized the link,
which is exactly the field a user needs to check during a link ceremony.
A real device label is the fix.

**Teammate-visible device label** ("Alice's phone" as seen by Bob).
→ **Nowhere. Explicit "not yet."** Three reasons:
- Nothing needs it.
   Trust flows through `key_certificate` history traversal; teammates address devices by `device_key_id`.
   Multi-device UI can say "2 devices" without naming them.
- It is the worst PII/durability combination available.
   Device labels leak hardware, OS, employer, device count and travel patterns,
   and a durable synced row cannot be retracted.
   This is the case that open-architecture-questions §"Identity payload privacy" is about.
- If it is ever wanted, the shape is already established and should be reused rather than reinvented:
   `admission_proposal.invitee_label_commitment` (signed) plus `invitee_label_payload`
   (separable, unsigned, droppable).
   A plain `TEXT` column on `team_device` would bypass that pattern.

**Last-seen / liveness.**
→ **Device-local or Hub-local only.**
This one is not a fact about the team at all — it is an observation by one device.
Each device sees a different last-seen for the same peer, so there is nothing to converge on;
a synced row would just be last-writer-wins noise.
It also fails the churn test: a row rewritten on every sync means a commit on every sync,
and the accumulated history is a durable per-device activity timeline.
The existing precedent to copy is `unknown_app_sighting(first_seen_at, last_seen_at, seen_count)` in the Hub-local schema.
The observation's natural source is where sync actually happens, so the Hub is the more likely owner of the two.

### The welcome-bundle payload version is duplicated rather than unavailable

Found while checking what a `WelcomeBundle` version bump would cost.

`welcome_bundle_aad` (note_to_self/bootstrap.py:66) binds `WelcomeBundle.version` into the AEAD associated data,
while `prepare_identity_bootstrap` supplies a separate hardcoded `version=1` (provisioning.py:2318).
The receiver does not need to discover that value inside the ciphertext: in the current single-version protocol,
it independently knows the one payload version it supports.
The defect is that the expected version is duplicated and never validated after decryption.

The general rule is that every associated-data input must be constructible before decryption from protocol
constants, receiver-held state, or cleartext envelope fields bound by the authenticated construction.
`_start_linked_team_bootstrap` binds `{bootstrap_id, team_id}` (provisioning.py:2702), both known out of band.
`joining_device_id_hex` qualifies too — the joiner reads it from its own pending artifact.
The expected payload-version constant qualifies for the same reason.

The three version mechanisms are separate:
`_WELCOME_BUNDLE_INFO` is the HKDF domain-separation label;
the cleartext envelope version selects the outer encryption-envelope format;
and `WelcomeBundle.version` selects the signed payload contract.
The envelope version does not replace or authenticate the payload version.

The AAD does not provide sender authentication here.
The seal is anonymous public-key encryption and the joining device's public key is published in the join request,
so anyone can produce a well-formed sealed bundle.
The Ed25519 signature is not verified until `finalize_identity_bootstrap`, because the signer's public key
lives in the NoteToSelf DB fetched between the two steps.
That proves the bundle was signed by a key in the fetched database, but the remote descriptor selecting that
database came from the same bundle.
The comparison of the second confirmation string with the existing device's value is what binds the flow to
the intended participant and authorizing device when the bundle transport does not already provide equivalent
authentication.
That comparison produces local ceremony evidence; it does not grant team standing or establish a global trust fact.

Ruling: keep the expected payload version in AAD, replace the duplicated literals with one shared constant,
and reject unsupported artifact versions explicitly.
If simultaneous payload versions are supported later, carrying a cleartext payload-version selector in the
outer envelope and authenticating it is a separate compatibility design.

### Three definitions of `user_device`, two of them dead

- `packages/small-sea-note-to-self/small_sea_note_to_self/sql/shared_schema.sql` — live, the one this branch changes.
- `packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql` — dead and already drifted to `user_device(id, key)`.
   Its only initializer, `_initialize_core_note_to_self_schema` (provisioning.py:2512), raises `NotImplementedError`.
- `class UserDevice(Base)` (provisioning.py:1975) — unreferenced repo-wide.

The whole Manager SQLAlchemy model block is dead; the Hub defines its own duplicate models (backend.py:156)
rather than importing the Manager's.
Both mirrors are pre-existing dead code and cannot affect the live schema change.
Removing them belongs with the broader dead-code cleanup rather than this branch.

### Note, not scope

`team_device.created_at` is a wall-clock string in the synced team DB.
It is a mild leak and, per team-constitution.md, carries no causal authority.
Flagging it; not touching it on this branch.

The Hub reads the participant's NoteToSelf Core DB directly through its own ORM models (backend.py:410-418),
which conflicts with the Manager-database-exclusivity mandate in AGENTS.md.
Flagging it; not touching it on this branch.

### Remaining decisions

The security property is settled: signature verification proves internal consistency with the fetched NoteToSelf
database, while the second comparison or an equivalently authenticated delivery binds that identity to the
authorizing device the user intended.
That result should be exposed as local ceremony evidence rather than collapsed into a universal trusted/untrusted bit.
The default Manager policy is conservative: it may stage isolated local state, fetch, verify, and present the evidence,
but ordinary identity use, remote mutations, team joins, and key distribution require a match or equivalently
authenticated delivery.
A simple, conspicuous, device-local dangerous override permits ordinary actions in an unconfirmed or mismatched
state for research and testing.
It must preserve the actual evidence state and may not bypass parsing, AEAD, signature, or artifact-version checks.
What remains is implementation scope: the persisted status shape, confirmation input, mismatch cleanup, and whether
that work belongs on this branch or in a follow-up.

Two product designs are explicitly deferred rather than blocking this branch:

- **Future multi-version negotiation.** The current branch can use one expected payload-version constant.
  Supporting old and new payload versions simultaneously would require a clear pre-decrypt selector and downgrade policy.
- **Deferred metadata products.** Teammate-visible labels remain deferred until a concrete UI requires them,
  and peer liveness still needs an owner, observation source, retention policy, and UI before implementation.

# Plan

Proposed scope: correct item 1, write down the item-2 decision durably,
and implement only the self-assigned, participant-owned device label.
That is the one piece with real user value, no teammate exposure, and an existing bug attached.
The two riskier fields land as written "not yet" decisions.

1. **Retire item 1 as already-done.** No code change.
   Correct issue #175 so it no longer claims the column or its two writers exist.
   → verify: the current `teammate` schema and `_upsert_teammate_row` have no device-key column;
      exact searches find no `member.device_public_key` or `teammate.device_public_key`;
      Git history shows the removal in `f330fee`.
   (Already done, recorded above.)

2. **Record the placement decision durably.**
   Add the field-specific ruling — own-device labels are participant-tier, cryptographic standing is team-tier,
   and observed liveness is local — to `architecture.md`
   near §Database Access, which already owns the tier boundaries.
   Keep it to the ruling: §Database Access is currently three sentences and the tier table would swamp it.
   Put the tier table, the `user_device.label` schema, and the creation/link behavior in
   `packages/small-sea-manager/spec.md`, which owns Manager policy.
   Fold the teammate-visible-label deferral into open-architecture-questions §5 as a named open item
   pointing at the commitment-and-payload pattern.
   → verify: a reader who has not seen this branch can answer "where does a device nickname go, and why not the team DB?"
      from the committed docs alone, and the Manager spec agrees with the implemented schema and bootstrap contract.

3. **Centralize and enforce bootstrap artifact versions.**
   Define one supported-version constant for each versioned artifact and use the expected welcome-bundle
   version on both sides of `welcome_bundle_aad`.
   Validate `JoinRequestArtifact.version` before admission side effects and validate decrypted
   `WelcomeBundle.version` and `SignedWelcomeBundle.version` before using their fields.
   Keep the outer encryption-envelope version separate.
   → verify: the current end-to-end device-link micro test passes; unsupported join-request, welcome-bundle,
      and signed-wrapper versions are rejected; and the outer-envelope version test remains independent.
   (Done.
   `JOIN_REQUEST_ARTIFACT_VERSION`, `WELCOME_BUNDLE_VERSION`, and `SIGNED_WELCOME_BUNDLE_VERSION` live in
   `small_sea_note_to_self/bootstrap.py`, and enforcement sits in the three deserializers so no caller can skip it.
   Rejection happens before the artifact's other fields are read, because a future version may change which fields exist.
   Nine of the new micro tests fail when the check is disabled, including the three end-to-end ones:
   an unsupported join request no longer admits a device or produces a commit, and unsupported payload and wrapper
   versions no longer complete a bootstrap.
   `test_welcome_bundle_aad_binds_the_expected_payload_version` is the exception — it guards the existing AAD binding
   rather than proving a defect fixed, and passes either way.)

4. **Add `user_device.label` to the shared NoteToSelf schema.**
   Add a nullable `TEXT` column to `small_sea_note_to_self/sql/shared_schema.sql`,
   bump `SHARED_SCHEMA_VERSION`, and do not add a migration.
   `USER_SCHEMA_VERSION` governs team DBs and must not change for this NoteToSelf-only schema edit.
   → verify: a `small-sea-note-to-self` micro test asserts that a fresh shared DB has the column and that
      `PRAGMA user_version` matches the bumped `SHARED_SCHEMA_VERSION`.
   (Done.
   `SHARED_SCHEMA_VERSION` 57 → 58; `USER_SCHEMA_VERSION` stays 64.
   Every existing `user_device` query names its columns, so nothing had to change to tolerate the new one.
   A second micro test covers what the bump actually buys: a stale shared DB at the previous version is refused
   with the "no migrations" error rather than silently adopted.
   The Manager's dead `core_note_to_self_schema.sql` mirror was left to drift further, per the removal follow-up below.)

5. **Populate the initial participant device label.**
   Replace the currently unused `device` argument to `create_new_participant` / `_initialize_user_db`
   with an explicit optional `device_label`, and write it at the initial `user_device` insert.
   Keep omission as `NULL`; do not derive a label from the participant nickname or host environment.
   Have the two dev-tooling callers pass a real label — `devtools/sandbox/sandbox/workspace.py:180`
   and `scripts/setup_dropbox_workspace.py:62` — so the field is exercised by something other than tests.
   No new label-editing UI is in scope.
   → verify: Manager micro tests show that an initial label round-trips and omission leaves `NULL`,
      and a sandbox workspace built by the dev tooling has a non-null label on its initial device.
   (Done, in `packages/small-sea-manager/tests/test_device_label.py`.
   The vestigial `device="42"` default is gone; every caller in the repo passed only `(root_dir, nickname)`,
   so no call site had to change except the two dev-tooling ones.
   A fourth test asserts `team_device` has no `label` column, guarding the alternative the design record rejects.
   It is scoped to that one table on purpose: the team schema already carries `invitee_label_commitment`
   and `invitee_label_payload`, so a repo-wide "no column named label" assertion would be both wrong and brittle.
   The sandbox assertion imports `sandbox.workspace` from `devtools`, which the uv workspace already makes importable.)

6. **Carry the joining device's self-assigned label through the link request.**
   Add optional `device_label` input to `create_identity_join_request` and its Manager wrapper.
   Add that value to `JoinRequestArtifact` and advance the artifact from version 1 to version 2.
   At authorization, write the artifact's label at the second `user_device` insert.
   A retry for an existing device ID must accept the same keys and label and reject different material;
   relabeling an existing device is not part of this bootstrap operation.
   → verify: Manager micro tests show that a joining-device label appears in both authorizer and joiner
      NoteToSelf clones, an omitted label remains `NULL`, and an idempotent retry does not create
      another row or commit.
      The load-bearing assertion is that the authorizer writes *the artifact's* label rather than one
      chosen locally.
      Binding into the authentication string is structural — `canonical_join_request_artifact_bytes`
      hashes `asdict`, so any new field is covered — so assert it, but do not count it as evidence.
   (Done. `JOIN_REQUEST_ARTIFACT_VERSION` is now 2, the first bump the step 3 enforcement makes meaningful.
   The joiner's clone gets the label through the ordinary NoteToSelf fetch, so only the authorizer writes it.
   Sabotaging the authorizer to write the participant nickname instead of the artifact's label — the same
   shape as the step 7 defect — fails all five new Manager tests.
   A differing label on a retry is refused rather than silently applied, so relabelling stays a separate
   operation that this branch does not implement.)

7. **Fix `authorizing_device_label`** to read the authorizing device's own label,
   falling back to `null` rather than to the participant nickname.
   The field stays present in the signed bytes with a `null` value; omitting the key would change
   canonicalization and the signature shape.
   Reading the label means adding `ud.label` to `_current_device_row` (provisioning.py:256).
   Append it last because five call sites index that row positionally (2251, 2532, 2872, 4471, 5148),
   including two that read position 4 as `signing_private_key_ref`.
   Make `WelcomeBundle.authorizing_device_label` optional and advance `WelcomeBundle` from version 1 to
   version 2 so the signed payload contract records the changed field semantics.
   Keep `SignedWelcomeBundle.version` unchanged because its wrapper contract does not change.
   → verify: micro test over the device-link bundle showing that with a labeled authorizing device the bundle
      carries that label, and with an unlabeled one it does *not* silently carry the participant nickname.
      This is the test that proves the specific defect is gone, so it should fail against current `main`.
   (Done. `WELCOME_BUNDLE_VERSION` is 2; `SIGNED_WELCOME_BUNDLE_VERSION` stays 1.
   `ud.label` was appended last to `_current_device_row`; all five call sites read only positions 0, 1, and 4,
   so none of them moved.
   Restoring the old `get_nickname` expression fails all three new tests, each showing `'Alice'` where a device
   label belongs — the defect is now pinned rather than merely fixed.
   `authorizing_device_label` is validated as optional-string at the wire boundary, matching the pattern the
   join-request label already uses.
   The nickname still travels in `identity_label`, which is the field that is genuinely about the participant.)

8. **Confirm nothing regressed.**
   Run the full micro test suites for `small-sea-manager` and `small-sea-note-to-self`.
   The label must not be added to Hub `/session/info` or another app-facing identity surface;
   run a targeted Hub micro test only if an implementation change touches that path.
   → verify: green, and every changed production line belongs to the NoteToSelf schema/version,
      the two device-creation paths, enforced bootstrap versions, or their documentation.
   (Done. 389 passed, 3 skipped across `small-sea-note-to-self`, `small-sea-manager`, `cuttlefish`,
   `small-sea-hub`, `ssc-files`, and `cod-sync`.
   `small-sea-hub`'s `test_notifications.py` is excluded because its fixture needs a Docker `ntfy` container;
   it errors at setup on a machine without the daemon and is untouched by this branch.
   No targeted Hub test was needed: the Hub's own modules never query `user_device`, so the label reaches no
   app-facing identity surface. The `label` matches in `backend.py` are `invitee_label` from the team DB.
   Production changes are confined to four files: the NoteToSelf schema, version constant, and bootstrap
   artifacts; `provisioning.py`; the one-line `manager.py` wrapper; and the two dev-tooling callers.)

Integrity argument for a skeptic: the branch adds one nullable column, corrects one already-wrong value,
and makes existing artifact-version markers effective.
There is no new table, team-synced metadata, app-facing identity field, or trust-path change — verification still goes through
`key_certificate` traversal and never reads a label.
The reviewable claim is narrow: does the repo now contain a written answer to #57's device-metadata question,
and is the one implemented field confined to the participant's own sync tier?

The existing artifact-version markers are not consistently checked today.
Step 3 turns them into enforced protocol boundaries rather than inert documentation.

Documentation ownership is split deliberately.
`architecture.md` owns the tier boundary and privacy/durability ruling;
`packages/small-sea-manager/spec.md` owns the tier table, the concrete field, and bootstrap behavior;
`packages/cuttlefish/README.md` owns the project-wide AEAD associated-data construction rule;
`Documentation/open-architecture-questions.md` owns only the deferred teammate-visible-label question.

# Follow-up

- **Correct issue #175.** Item 1 is stale: strike the claim that the column and its two writers still exist,
   and note the removal commit. Item 2 is what the issue is actually about.
- **New issue: teammate-visible device labels, deferred.** Record the "not yet" ruling and the condition for
   revisiting: a concrete UI need that `device_key_id` plus a device count cannot serve.
   Should reference the `invitee_label_commitment`/`invitee_label_payload` pattern as the required shape.
- **New issue: peer device liveness observations.** Decide the owner, evidence source, retention, aggregation,
   and UI for observer-local liveness.
   The strongest existing precedent is Hub-local `first_seen/last_seen/seen_count` per observed `device_key_id`,
   sourced from Cod Sync bundle arrivals, but that is still a product design rather than a team fact.
- **Consider on #57's remaining thread**: whether `team_device.created_at` should stay a synced wall-clock string.
- **New issue: the Manager's dead NoteToSelf schema mirrors.** Remove
   `packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql`,
   `_initialize_core_note_to_self_schema`, and the unreferenced SQLAlchemy model block (`UserDevice`,
   `Nickname`, `Team`, `App`, `TeamAppBerth`, `CloudStorage`, `BerthCloudAllocation`,
   `NotificationService`, `Invitation`, `TeamDevice`, `TeamDeviceKey`).
   The Hub does not import them; it defines its own duplicates.
- **New issue: the linked-team bootstrap bundle carries no version field.**
   Found while enforcing versions on the identity-bootstrap artifacts.
   The `response_body` assembled in `_start_linked_team_bootstrap` has no version marker at all,
   so it cannot be version-gated the way the join request and welcome bundle now are.
   Its associated data binds `{bootstrap_id, team_id}` and does not name a payload contract.
- **New issue: the Hub reads the Core DB directly.** `backend.py:410-418` opens the participant's
   NoteToSelf `core.db` and queries it through Hub-local ORM models mirroring the Manager's schema.
   AGENTS.md says only `small-sea-manager` may do that.
   Either the mandate or the implementation needs to move.
