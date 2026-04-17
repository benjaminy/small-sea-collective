# Branch Plan: Spec/Doc Sweep (B1)

**Branch:** `issue-100-spec-doc-sweep`
**Base:** `main`
**Primary issue:** #100 "spec/doc sweep"
**Kind:** Documentation-only. No code changes.
**Related issues:** #97 (trust-domain reframe decision, accepted)
**Related prior plan:** `Archive/branch-plan-issue-97-trust-domain-reframe.md` — the decision this sweep executes against

## Purpose

Rewrite architecture and spec language so it accurately reflects the accepted trust-domain model. This is B1 from the issue-97 meta-plan. It must land before downstream branches (B2–B5) because those branches cite the updated docs.

## Files To Touch

- `architecture.md` (root)
- `packages/small-sea-manager/spec.md`
- `Documentation/open-architecture-questions.md`
- `packages/cuttlefish/README.md`

## Update Themes (Per Issue #100)

1. **Read access is effectively endpoint-trust-scoped.** Remove any language implying the protocol enforces a cryptographic read-access boundary between admitted and non-admitted parties. The real boundary is what endpoints choose to do.
2. **Linked-device admission is a unilateral identity-owner act (sibling handoff).** The existing sibling bootstraps the new device. No per-sender redistribution ceremony. The `device_link` cert chain satisfies the same crypto rules automatically.
3. **Teammate admission is inviter-orchestrated admin-quorum proposal/approval/publish.** Describe the full flow: proposal shell published at initiation (before the invitee is contacted), invitee-signed acceptance transcript, admin approvals scoped to members but executed by anchor-trusted device signatures, inviter observes quorum and publishes finalization.
4. **Approvals are transcript-bound and anchor-verified.** Governance-snapshot anchor (team-history commit hash / Cod Sync link ID) freezes admin roster, membership roster, and member→device mapping. Every signer verifies the frozen state at the anchor independently.
5. **Approvals are member-scoped votes exercised by anchor-trusted device signatures.** One vote per `admin_member_id`; devices linked after the anchor cannot vote on that proposal.
6. **Transport metadata is NOT frozen into the immutable admission transcript.** Admission binds device keys and the inviter-allocated `member_id` only. Post-admission transport setup is a separate flow (B7).
7. **Rotation means exclusion or hygiene, never admission.** Collapse all rotation language to these two purposes.
8. **Proposals are non-durable; invalidate on any governance-state change.** A proposal anchored to a team-history snapshot is dead the moment the admin roster, membership roster, or member→device mapping changes relative to that anchor (or the proposal expires). It cannot be a durable bearer capability that survives security-relevant team-state changes.

## Specific Sections To Rewrite (From Issue-97 Plan)

### `architecture.md`
- §"Fully Decentralized Team Management": rewrite rotation paragraph (exclusion + hygiene). Add paragraphs on admin-quorum, governance-snapshot anchoring (including member→device mapping), member/device approval bridge (expressed as a verifiable derivation, not just a policy rule — see spec.md note below), inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell visibility, transport out of transcript, non-durable proposals and their invalidation trigger.

### `packages/small-sea-manager/spec.md`
- §"Linked-device team bootstrap": rewrite historical-boundary and slice-scope subsections to reflect join-time-forward access and sibling-handoff model.
- §"Invitations" and §"Invitation Protocol (detailed)": describe inviter-orchestrated, transcript-bound proposal/approval/publish model. Spell out: inviter writes finalization (not invitee), `member_id` is inviter-allocated, approvals are member-scoped via anchor-trusted device signatures, transport published post-admission via announce-endpoint flow (not frozen in transcript). The member/device approval bridge must be expressed as a step-by-step derivation a verifier can replay (device_link certs at the anchor map device public keys to member_ids → approval is valid iff signing key appears in such a cert that maps to a current-admin member_id), not just stated as a policy rule.
- **SQL schema fragments:** spec.md contains SQL table definitions for the invitation/admission flow that follow the old model (e.g., the existing `invitation` table). B1 must not leave those intact — they will mislead implementers who read schema before prose. Replace each stale schema block with a placeholder of the form:

  ```
  -- [SCHEMA TBD — to be defined in B5]
  -- Target fields (from accepted model): proposal_id, nonce, team_history_anchor,
  --   frozen_governance_digest, inviter_member_id (= finalizer_member_id),
  --   pre_allocated_invitee_member_id, state, created_at, expires_at;
  --   plus acceptance_transcript, admin_approval_signatures (separate rows).
  -- Transport metadata (cloud endpoint etc.) is NOT part of this schema.
  ```

  The placeholder names the target fields so the intent is clear, but explicitly defers the authoritative definition to B5.

### `Documentation/open-architecture-questions.md`
- §5 "Identity Model": add settled-decisions subsection citing the reframe. Cover: endpoint-trust framing, admin-quorum, transcript binding (transport explicitly excluded), governance anchor (including member→device mapping), member/device approval bridge, inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell publication, non-durable proposals.

### `packages/cuttlefish/README.md`
- Sender-keys and trust sections: reconcile language to match the endpoint-trust frame and new admission model.

## Approach

Read each file in full, identify all passages that conflict with the accepted model, and rewrite them. Add new sections where the accepted model introduces concepts not yet present. Keep doc voice consistent with existing style.

After editing each file, do a pass checking that no old-model language (e.g., "only admitted readers can decrypt," "must redistribute to new device before it can read," "invitee publishes own admission") survived in that file.

Before declaring done, do a repo-wide grep for the most likely stale-model phrases (e.g., "can decrypt," "redistribution," "invitee.*publish," "admits.*read") and confirm any hits are either in the four target files and already updated, or are in code/tests/archive where old language is expected and harmless.

## Validation

Done when a skeptical reviewer confirms all three groups:

### Goal: four target files match the accepted model

For **each** of the four files independently:

1. None of the following old-model claims appear: (a) a cryptographic read-access boundary the protocol enforces, (b) per-sender redistribution required before a new linked device can read, (c) invitee publishes their own admission to team DB, (d) invitee selects or generates their own `member_id`.
2. Rotation is described only as exclusion or hygiene — never as an admission mechanism.
3. Transport metadata is explicitly noted as excluded from the admission transcript; post-admission transport setup is described as a separate flow.

### Goal: cross-document consistency on the subtle points

A reviewer who reads any one of the four files and then any other gets the same answer to each of these:

4. **Who allocates `member_id`?** All four files agree: the inviter, at proposal creation.
5. **Who publishes finalization?** All four files agree: the inviter (never the invitee).
6. **What does the governance anchor freeze?** All four files agree: admin roster, membership roster, AND member→device mapping.
7. **When does the proposal shell become visible to other admins?** All four files agree: at initiation, before the invitee is contacted.
8. **What is bound in the admission transcript?** All four files agree: device keys and `member_id`; transport metadata explicitly excluded.

### Goal: repo integrity

9. A repo-wide search finds no old-model language in non-archived, non-code files outside the four target files. Any hits are documented and either false positives or intentionally deferred.
10. No code files were modified. No GitHub issue state was changed (that is B6).
11. The four target files remain internally consistent: no section contradicts another within the same file.

## Out Of Scope

- Code changes of any kind.
- GitHub issue edits (that is B6).
- DB schema definitions (B5).
- Implementation of the admission or transport-configuration flows.
- Formal-model writeup.
