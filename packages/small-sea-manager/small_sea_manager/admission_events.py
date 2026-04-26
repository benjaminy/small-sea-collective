from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text

from small_sea_manager import provisioning


class AdmissionEventType(str, Enum):
    LINKED_DEVICE = "linked_device"
    INVITATION_PENDING = "invitation_pending"
    INVITATION_FINALIZED = "invitation_finalized"
    # Reserved for B5 once proposal shells become first-class runtime artifacts.
    PROPOSAL_SHELL = "proposal_shell"
    # Reserved for B5 once quorum approvals exist as first-class runtime artifacts.
    AWAITING_QUORUM = "awaiting_quorum"


@dataclass(frozen=True)
class AdmissionEvent:
    event_type: AdmissionEventType
    artifact_id_hex: str
    occurred_at: str | None
    title: str
    summary: str
    badge_label: str
    badge_class: str
    member_id_hex: str | None = None
    invitation_id_hex: str | None = None
    proposal_id_hex: str | None = None
    can_dismiss: bool = True
    can_revoke: bool = False
    can_exclude: bool = False
    can_approve: bool = False
    can_finalize: bool = False


@dataclass(frozen=True)
class LinkedDeviceNotificationCandidate:
    artifact_id_hex: str
    occurred_at: str | None
    title: str
    summary: str
    member_id_hex: str


def _member_label(display_name: str | None, member_id_hex: str | None) -> str:
    if display_name:
        return display_name
    if member_id_hex:
        return f"member {member_id_hex[:8]}"
    return "unknown member"


def _linked_device_events(conn, dismissed, *, self_member_id_hex: str | None):
    rows = conn.execute(
        text(
            "SELECT kc.cert_id, kc.issued_at, kc.claims, td.member_id, m.display_name "
            "FROM key_certificate AS kc "
            "LEFT JOIN team_device AS td ON td.device_key_id = kc.subject_key_id "
            "LEFT JOIN member AS m ON m.id = td.member_id "
            "WHERE kc.cert_type = 'device_link' "
            "ORDER BY kc.issued_at DESC, kc.cert_id DESC"
        )
    ).fetchall()

    events: list[AdmissionEvent] = []
    for cert_id, issued_at, claims_json, row_member_id, display_name in rows:
        artifact_id_hex = cert_id.hex()
        key = (AdmissionEventType.LINKED_DEVICE.value, artifact_id_hex)
        if key in dismissed:
            continue

        member_id_hex = row_member_id.hex() if row_member_id is not None else None
        if member_id_hex is None and claims_json:
            try:
                claims = json.loads(claims_json)
            except json.JSONDecodeError:
                claims = {}
            member_id_hex = claims.get("member_id")

        label = _member_label(display_name, member_id_hex)
        if member_id_hex == self_member_id_hex:
            title = "New linked device for you"
            summary = (
                "A `device_link` cert added another trusted device to your member identity."
            )
        else:
            title = f"New linked device for {label}"
            summary = (
                "A `device_link` cert added another trusted device for this teammate."
            )
        events.append(
            AdmissionEvent(
                event_type=AdmissionEventType.LINKED_DEVICE,
                artifact_id_hex=artifact_id_hex,
                occurred_at=issued_at,
                title=title,
                summary=summary,
                badge_label="linked device",
                badge_class="badge-blue",
                member_id_hex=member_id_hex,
                can_dismiss=True,
                can_revoke=False,
                can_exclude=False,
            )
        )
    return events


def _invitation_events(conn, dismissed, *, self_member_id_hex: str | None, viewer_is_admin: bool):
    proposal_rows = conn.execute(
        text(
            "SELECT proposal_id, state, invitee_label, role, created_at, finalized_at, "
            "invitee_member_id, inviter_member_id, transcript_digest "
            "FROM admission_proposal "
            "WHERE state IN ('awaiting_invitee', 'awaiting_quorum', 'finalized') "
            "ORDER BY COALESCE(finalized_at, created_at) DESC, proposal_id DESC"
        )
    ).fetchall()

    if proposal_rows:
        events: list[AdmissionEvent] = []
        for (
            proposal_id,
            state,
            invitee_label,
            role,
            created_at,
            finalized_at,
            invitee_member_id,
            inviter_member_id,
            transcript_digest,
        ) in proposal_rows:
            artifact_id_hex = proposal_id.hex()
            member_id_hex = invitee_member_id.hex() if invitee_member_id is not None else None
            if state == "awaiting_invitee":
                event_type = AdmissionEventType.PROPOSAL_SHELL
                if (event_type.value, artifact_id_hex) in dismissed:
                    continue
                events.append(
                    AdmissionEvent(
                        event_type=event_type,
                        artifact_id_hex=artifact_id_hex,
                        occurred_at=created_at,
                        title=f"Proposal shell open for {invitee_label or 'unlabelled invitee'}",
                        summary=(
                            f"Transcript-bound admission proposal created for role `{role}`. "
                            "This shell should be visible before the invitation token is delivered."
                        ),
                        badge_label="proposal shell",
                        badge_class="badge-amber",
                        member_id_hex=member_id_hex,
                        invitation_id_hex=artifact_id_hex,
                        proposal_id_hex=artifact_id_hex,
                        can_dismiss=True,
                        can_revoke=viewer_is_admin,
                    )
                )
                continue
            if state == "awaiting_quorum":
                event_type = AdmissionEventType.AWAITING_QUORUM
                if (event_type.value, artifact_id_hex) in dismissed:
                    continue
                quorum_row = conn.execute(
                    text(
                        "SELECT COUNT(DISTINCT admin_member_id) FROM admin_approval "
                        "WHERE proposal_id = :proposal_id AND transcript_digest = :transcript_digest"
                    ),
                    {
                        "proposal_id": proposal_id,
                        "transcript_digest": transcript_digest,
                    },
                ).fetchone()
                quorum_count = int(quorum_row[0]) if quorum_row is not None else 0
                quorum_target = int(
                    provisioning._team_setting(conn, "admission_quorum", "1")
                )
                can_finalize = (
                    viewer_is_admin
                    and self_member_id_hex is not None
                    and inviter_member_id is not None
                    and inviter_member_id.hex() == self_member_id_hex
                    and quorum_count >= quorum_target
                )
                events.append(
                    AdmissionEvent(
                        event_type=event_type,
                        artifact_id_hex=artifact_id_hex,
                        occurred_at=created_at,
                        title=f"Awaiting quorum for {invitee_label or 'unlabelled invitee'}",
                        summary=(
                            f"Transcript recorded for role `{role}`. "
                            f"{quorum_count} of {quorum_target} distinct admin approvals recorded."
                        ),
                        badge_label="awaiting quorum",
                        badge_class="badge-amber",
                        member_id_hex=member_id_hex,
                        invitation_id_hex=artifact_id_hex,
                        proposal_id_hex=artifact_id_hex,
                        can_dismiss=True,
                        can_revoke=viewer_is_admin,
                        can_approve=viewer_is_admin,
                        can_finalize=can_finalize,
                    )
                )
                continue
            if state == "finalized":
                event_type = AdmissionEventType.INVITATION_FINALIZED
                if (event_type.value, artifact_id_hex) in dismissed:
                    continue
                events.append(
                    AdmissionEvent(
                        event_type=event_type,
                        artifact_id_hex=artifact_id_hex,
                        occurred_at=finalized_at or created_at,
                        title=f"Admission finalized for {invitee_label or _member_label(None, member_id_hex)}",
                        summary=(
                            "This transcript-bound admission has been finalized in the current team view. "
                            "Transport setup remains a separate post-admission step."
                        ),
                        badge_label="finalized",
                        badge_class="badge-green",
                        member_id_hex=member_id_hex,
                        invitation_id_hex=artifact_id_hex,
                        proposal_id_hex=artifact_id_hex,
                        can_dismiss=True,
                        can_exclude=viewer_is_admin and member_id_hex not in {None, self_member_id_hex},
                    )
                )
        return events

    rows = conn.execute(
        text(
            "SELECT i.id, i.status, i.invitee_label, i.role, i.created_at, i.accepted_at, "
            "i.accepted_by, m.display_name "
            "FROM invitation AS i "
            "LEFT JOIN member AS m ON m.id = i.accepted_by "
            "WHERE i.status IN ('pending', 'accepted') "
            "ORDER BY COALESCE(i.accepted_at, i.created_at) DESC, i.id DESC"
        )
    ).fetchall()

    events: list[AdmissionEvent] = []
    for (
        invitation_id,
        status,
        invitee_label,
        role,
        created_at,
        accepted_at,
        accepted_by,
        accepted_display_name,
    ) in rows:
        artifact_id_hex = invitation_id.hex()
        if status == "pending":
            event_type = AdmissionEventType.INVITATION_PENDING
            if (event_type.value, artifact_id_hex) in dismissed:
                continue
            label = invitee_label or "unlabelled invitee"
            events.append(
                AdmissionEvent(
                    event_type=event_type,
                    artifact_id_hex=artifact_id_hex,
                    occurred_at=created_at,
                    title=f"Invitation open for {label}",
                    summary=(
                        f"Current-model invitation token created for role `{role}`. "
                        "This is visible now; multi-admin quorum decisions remain B5."
                    ),
                    badge_label="needs attention" if viewer_is_admin else "invitation",
                    badge_class="badge-amber",
                    invitation_id_hex=artifact_id_hex,
                    can_dismiss=True,
                    can_revoke=viewer_is_admin,
                    can_exclude=False,
                )
            )
            continue

        if status == "accepted":
            event_type = AdmissionEventType.INVITATION_FINALIZED
            if (event_type.value, artifact_id_hex) in dismissed:
                continue
            accepted_by_hex = accepted_by.hex() if accepted_by is not None else None
            label = invitee_label or _member_label(accepted_display_name, accepted_by_hex)
            events.append(
                AdmissionEvent(
                    event_type=event_type,
                    artifact_id_hex=artifact_id_hex,
                    occurred_at=accepted_at or created_at,
                    title=f"Admission finalized for {label}",
                    summary=(
                        "This admission has been finalized in the current team view. "
                        "Use exclusion if you need to object after the fact."
                    ),
                    badge_label="finalized",
                    badge_class="badge-green",
                    member_id_hex=accepted_by_hex,
                    invitation_id_hex=artifact_id_hex,
                    can_dismiss=True,
                    can_revoke=False,
                    can_exclude=viewer_is_admin and accepted_by_hex not in {None, self_member_id_hex},
                )
            )
            continue
    return events


def list_admission_events(
    root_dir,
    participant_hex: str,
    team_name: str,
    *,
    self_member_id_hex: str | None,
    viewer_is_admin: bool,
) -> list[AdmissionEvent]:
    team_db_path = pathlib.Path(root_dir) / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    dismissed = provisioning.list_dismissed_admission_events(
        root_dir,
        participant_hex,
        team_name,
    )
    engine = provisioning._sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            events = _linked_device_events(
                conn,
                dismissed,
                self_member_id_hex=self_member_id_hex,
            )
            events.extend(
                _invitation_events(
                    conn,
                    dismissed,
                    self_member_id_hex=self_member_id_hex,
                    viewer_is_admin=viewer_is_admin,
                )
            )
    finally:
        engine.dispose()

    return sorted(
        events,
        key=lambda event: (event.occurred_at or "", event.artifact_id_hex),
        reverse=True,
    )


def list_linked_device_notification_candidates(
    root_dir,
    participant_hex: str,
    team_name: str,
    *,
    self_member_id_hex: str | None,
) -> list[LinkedDeviceNotificationCandidate]:
    team_db_path = pathlib.Path(root_dir) / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    suppressed = provisioning.list_dismissed_admission_events(
        root_dir,
        participant_hex,
        team_name,
    ) | provisioning.list_notified_admission_events(
        root_dir,
        participant_hex,
        team_name,
    )
    engine = provisioning._sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            events = _linked_device_events(
                conn,
                suppressed,
                self_member_id_hex=self_member_id_hex,
            )
    finally:
        engine.dispose()

    candidates: list[LinkedDeviceNotificationCandidate] = []
    for event in events:
        if event.member_id_hex in {None, self_member_id_hex}:
            continue
        candidates.append(
            LinkedDeviceNotificationCandidate(
                artifact_id_hex=event.artifact_id_hex,
                occurred_at=event.occurred_at,
                title=event.title,
                summary=event.summary,
                member_id_hex=event.member_id_hex,
            )
        )
    return candidates
