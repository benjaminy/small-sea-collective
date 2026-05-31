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
    free of hub/vault imports.
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
    team_id, self_member_id = provisioning._team_row(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
    )
    assert team_id == ss_session.team_id
    return provisioning.publish_member_berth_storage_announcement(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
        self_member_id,
        ss_session.berth_id,
        allocation,
    )
