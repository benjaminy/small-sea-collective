"""Shared test-support helpers.

Lives at the repo root so it ships in zero runtime distributions and is
importable from any package's tests via the root `pyproject.toml`'s
`pythonpath = ["."]`.
"""

import small_sea_manager.provisioning as provisioning


def publish_storage_announcement_for_session(backend, session_hex) -> dict | None:
    """Publish this session's own-storage announcement.

    For NoteToSelf sessions this is a no-op (returns None) — that team
    has no shared storage to announce.

    `backend` must expose `.root_dir` and `._lookup_session(session_hex)`
    returning a `SmallSeaSession`.  Duck-typed so this module can stay
    free of hub/files imports.
    """
    ss_session = backend._lookup_session(session_hex)
    if ss_session.team_name == "NoteToSelf":
        return None
    allocation = provisioning.get_berth_cloud_allocation_for_berth(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
    )
    assert allocation is not None
    team_id, self_teammate_id = provisioning._team_row(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
    )
    assert team_id == ss_session.team_id
    return provisioning.publish_teammate_berth_storage_announcement(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
        self_teammate_id,
        ss_session.berth_id,
        allocation,
    )


def acceptance_record_from_courier(courier_b64: str) -> dict:
    """Decode the `admission_acceptance` record out of a courier token."""
    import base64
    import json

    payload = json.loads(base64.b64decode(courier_b64).decode())
    if payload.get("envelope") != provisioning.ACCEPTANCE_COURIER_ENVELOPE:
        return payload
    return json.loads(base64.b64decode(payload["admission_acceptance"]).decode())


def route_sidecar_from_courier(courier_b64: str) -> dict | None:
    """Decode the route attached beside the acceptance, if any."""
    import base64
    import json

    payload = json.loads(base64.b64decode(courier_b64).decode())
    return payload.get("route")


def accept_and_export(manager, token_b64: str) -> str:
    """Accept an invitation and export the courier token in one step.

    Asserts the route landed, so a test that means to exercise the happy path
    fails at the point the route went pending rather than later.
    """
    import base64
    import json

    team_name = json.loads(base64.b64decode(token_b64).decode())["team_name"]
    report = manager.accept_invitation(token_b64)
    assert report["route"] == "ready", report
    assert report["acceptance"] == "exportable", report
    exported = manager.export_admission_acceptance(team_name)
    assert exported["acceptance_token"] is not None, exported
    return exported["acceptance_token"]
