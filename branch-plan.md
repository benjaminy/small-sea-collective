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
3. **Teammate admission is inviter-orchestrated admin-quorum proposal/approval/publish.** Describe the full flow: proposal shell published at initiation (before invitee responds), invitee-signed acceptance transcript, admin approvals scoped to members but executed by anchor-trusted device signatures, inviter observes quorum and publishes finalization.
4. **Approvals are transcript-bound and anchor-verified.** Governance-snapshot anchor (team-history commit hash / Cod Sync link ID) freezes admin roster, membership roster, and member→device mapping. Every signer verifies the frozen state at the anchor independently.
5. **Approvals are member-scoped votes exercised by anchor-trusted device signatures.** One vote per `admin_member_id`; devices linked after the anchor cannot vote on that proposal.
6. **Transport metadata is NOT frozen into the immutable admission transcript.** Admission binds device keys and the inviter-allocated `member_id` only. Post-admission transport setup is a separate flow (B7).
7. **Rotation means exclusion or hygiene, never admission.** Collapse all rotation language to these two purposes.

## Specific Sections To Rewrite (From Issue-97 Plan)

### `architecture.md`
- §"Fully Decentralized Team Management": rewrite rotation paragraph (exclusion + hygiene). Add paragraphs on admin-quorum, governance-snapshot anchoring (including member→device mapping), member/device approval bridge, inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell visibility, transport out of transcript, non-durable proposals.

### `packages/small-sea-manager/spec.md`
- §"Linked-device team bootstrap": rewrite historical-boundary and slice-scope subsections to reflect join-time-forward access and sibling-handoff model.
- §"Invitations" and §"Invitation Protocol (detailed)": describe inviter-orchestrated, transcript-bound proposal/approval/publish model. Spell out: inviter writes finalization (not invitee), `member_id` is inviter-allocated, approvals are member-scoped via anchor-trusted device signatures, transport published post-admission via announce-endpoint flow (not frozen in transcript).

### `Documentation/open-architecture-questions.md`
- §5 "Identity Model": add settled-decisions subsection citing the reframe. Cover: endpoint-trust framing, admin-quorum, transcript binding (transport explicitly excluded), governance anchor (including member→device mapping), member/device approval bridge, inviter-as-finalizer, inviter-allocated `member_id`, early proposal-shell publication, non-durable proposals.

### `packages/cuttlefish/README.md`
- Sender-keys and trust sections: reconcile language to match the endpoint-trust frame and new admission model.

## Approach

Read each file in full, identify all passages that conflict with the accepted model, and rewrite them. Add new sections where the accepted model introduces concepts not yet present. Keep doc voice consistent with existing style.

After editing each file, do a pass checking that no old-model language (e.g., "only admitted readers can decrypt," "must redistribute to new device before it can read," "invitee publishes own admission") survived.

## Validation

Done when a skeptical reviewer confirms:

1. No file retains language claiming a cryptographic read-access boundary the protocol cannot enforce.
2. Linked-device admission is described as sibling handoff + `device_link` cert; no per-sender redistribution step.
3. Teammate admission describes the full inviter-orchestrated flow with proposal shell, signed acceptance transcript, anchor-verified approvals, and inviter-published finalization.
4. Rotation is described only as exclusion or hygiene in all four files.
5. Transport metadata is explicitly noted as excluded from the admission transcript and handled post-admission.
6. No code files were modified.

## Out Of Scope

- Code changes of any kind.
- GitHub issue edits (that is B6).
- DB schema definitions (B5).
- Implementation of the admission or transport-configuration flows.
- Formal-model writeup.
