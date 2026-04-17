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
    event_type: str
    artifact_id_hex: str
    occurred_at: str | None
    title: str
    summary: str
    badge_label: str
    badge_class: str
    member_id_hex: str | None = None
    invitation_id_hex: str | None = None
    can_dismiss: bool = True
    can_revoke: bool = False
    can_exclude: bool = False


def _team_db_path(root_dir, participant_hex: str, team_name: str) -> pathlib.Path:
    return pathlib.Path(root_dir) / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _member_label(display_name: str | None, member_id_hex: str | None) -> str:
    if display_name:
        return display_name
    if member_id_hex:
        return f"member {member_id_hex[:8]}"
    return "unknown member"


def _load_dismissed_keys(conn) -> set[tuple[str, str]]:
    if not provisioning._table_exists(conn, "admission_event_disposition"):
        return set()
    rows = conn.execute(
        text(
            "SELECT event_type, artifact_id "
            "FROM admission_event_disposition "
            "WHERE disposition = 'dismissed'"
        )
    ).fetchall()
    return {(row[0], row[1].hex()) for row in rows}


def _linked_device_events(conn, *, self_member_id_hex: str | None, viewer_is_admin: bool):
    dismissed = _load_dismissed_keys(conn)
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
                event_type=AdmissionEventType.LINKED_DEVICE.value,
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


def _invitation_events(conn, *, self_member_id_hex: str | None, viewer_is_admin: bool):
    dismissed = _load_dismissed_keys(conn)
    rows = conn.execute(
        text(
            "SELECT i.id, i.status, i.invitee_label, i.role, i.created_at, i.accepted_at, "
            "i.accepted_by, m.display_name "
            "FROM invitation AS i "
            "LEFT JOIN member AS m ON m.id = i.accepted_by "
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
            event_type = AdmissionEventType.INVITATION_PENDING.value
            if (event_type, artifact_id_hex) in dismissed:
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
            event_type = AdmissionEventType.INVITATION_FINALIZED.value
            if (event_type, artifact_id_hex) in dismissed:
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
    return events


def list_admission_events(
    root_dir,
    participant_hex: str,
    team_name: str,
    *,
    self_member_id_hex: str | None,
    viewer_is_admin: bool,
) -> list[AdmissionEvent]:
    team_db_path = _team_db_path(root_dir, participant_hex, team_name)
    engine = provisioning._sqlite_engine(team_db_path)
    try:
        with engine.begin() as conn:
            events = _linked_device_events(
                conn,
                self_member_id_hex=self_member_id_hex,
                viewer_is_admin=viewer_is_admin,
            )
            events.extend(
                _invitation_events(
                    conn,
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
