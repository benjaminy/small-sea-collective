"""Micro tests for the Manager team view's Core route column (issue #210).

The column reports the newest valid `teammate_berth_storage_announcement` for
the team's `SmallSeaCollectiveCore` berth -- the same row Hub peer routing
selects. It records the teammate's selected locator; it does not prove the
provider object is reachable, and rendering it performs no network I/O.
"""

import pathlib
import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import small_sea_manager.provisioning as Provisioning
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app
from small_sea_note_to_self.ids import uuid7
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    canonical_teammate_berth_storage_announcement_bytes,
)


def _alice_with_team(root, team_name="ProjectX"):
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    return alice_hex, Provisioning.create_team(root, alice_hex, team_name)


def _team_db(root, participant_hex, team_name="ProjectX") -> pathlib.Path:
    return root / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _self_row(manager, team_name="ProjectX"):
    team = manager.get_team(team_name)
    return next(t for t in team["teammates"] if t["id"] == team["self_in_team"])


def _signed_storage_announcement(
    *,
    teammate_id: bytes,
    berth_id: bytes,
    location: str,
    signer_private_key: bytes,
    signer_key_id: bytes,
) -> TeammateBerthStorageAnnouncement:
    unsigned = TeammateBerthStorageAnnouncement(
        announcement_id=uuid7(),
        teammate_id=teammate_id,
        berth_id=berth_id,
        protocol="localfolder",
        url="file:///route-announced",
        location=location,
        announced_at="2026-04-17T00:00:00+00:00",
        signer_key_id=signer_key_id,
        signature=b"",
    )
    signature = Ed25519PrivateKey.from_private_bytes(signer_private_key).sign(
        canonical_teammate_berth_storage_announcement_bytes(unsigned)
    )
    return replace(unsigned, signature=signature)


def _insert(db_path, announcement: TeammateBerthStorageAnnouncement):
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO teammate_berth_storage_announcement
            (announcement_id, teammate_id, berth_id, protocol, url, location,
             announced_at, signer_key_id, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                announcement.announcement_id,
                announcement.teammate_id,
                announcement.berth_id,
                announcement.protocol,
                announcement.url,
                announcement.location,
                announcement.announced_at,
                announcement.signer_key_id,
                announcement.signature,
            ),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# Status projection
# --------------------------------------------------------------------------- #


def test_a_valid_core_announcement_reports_announced(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)

    # create_team publishes the creator's own Core route when an allocation is
    # available, so the column reports it without any further Manager action.
    alice = _self_row(TeamManager(root, alice_hex))
    assert alice["core_route_status"] == "announced"
    assert alice["effective_core_route"]["protocol"] == "localfolder"
    assert alice["effective_core_route"]["location"]
    assert "bucket" not in alice["effective_core_route"]


def test_no_core_announcement_reports_missing(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)
    with sqlite3.connect(str(_team_db(root, alice_hex))) as conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
        conn.commit()

    alice = _self_row(TeamManager(root, alice_hex))
    assert alice["core_route_status"] == "missing"
    assert alice["effective_core_route"] is None


def test_an_invalid_signature_reports_missing(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)
    with sqlite3.connect(str(_team_db(root, alice_hex))) as conn:
        conn.execute(
            "UPDATE teammate_berth_storage_announcement SET signature = ?",
            (b"\x00" * 64,),
        )
        conn.commit()

    alice = _self_row(TeamManager(root, alice_hex))
    assert alice["core_route_status"] == "missing"
    assert alice["effective_core_route"] is None


def test_a_non_core_announcement_does_not_satisfy_the_status(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)
    db_path = _team_db(root, alice_hex)
    teammate_id = bytes.fromhex(team["teammate_id_hex"])
    core_berth_id = bytes.fromhex(team["berth_id_hex"])

    private_key, public_key = Provisioning.get_current_team_device_key(
        root, alice_hex, "ProjectX"
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
        conn.commit()
    other_berth_id = core_berth_id[:-1] + bytes([core_berth_id[-1] ^ 1])
    _insert(
        db_path,
        _signed_storage_announcement(
            teammate_id=teammate_id,
            berth_id=other_berth_id,
            location="other-berth-location",
            signer_private_key=private_key,
            signer_key_id=key_id_from_public(public_key),
        ),
    )

    alice = _self_row(TeamManager(root, alice_hex))
    assert alice["core_route_status"] == "missing"
    assert alice["effective_core_route"] is None


def test_a_core_route_becomes_inert_after_device_link_removal(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)
    db_path = _team_db(root, alice_hex)
    teammate_id = bytes.fromhex(team["teammate_id_hex"])
    core_berth_id = bytes.fromhex(team["berth_id_hex"])

    linked_key, linked_private_key = generate_key_pair(ProtectionLevel.DAILY)
    linked_cert = Provisioning.issue_device_link_for_teammate(
        root, alice_hex, "ProjectX", linked_key.public_key
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
        conn.commit()
    _insert(
        db_path,
        _signed_storage_announcement(
            teammate_id=teammate_id,
            berth_id=core_berth_id,
            location="linked-device-location",
            signer_private_key=linked_private_key,
            signer_key_id=key_id_from_public(linked_key.public_key),
        ),
    )

    manager = TeamManager(root, alice_hex)
    before = _self_row(manager)
    assert before["core_route_status"] == "announced"
    assert before["effective_core_route"]["location"] == "linked-device-location"

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM key_certificate WHERE cert_id = ?", (linked_cert.cert_id,))
        conn.commit()

    after = _self_row(manager)
    assert after["core_route_status"] == "missing"
    assert after["effective_core_route"] is None


def test_a_newer_invalid_row_does_not_hide_an_older_valid_row(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, team = _alice_with_team(root)
    db_path = _team_db(root, alice_hex)
    teammate_id = bytes.fromhex(team["teammate_id_hex"])
    core_berth_id = bytes.fromhex(team["berth_id_hex"])

    private_key, public_key = Provisioning.get_current_team_device_key(
        root, alice_hex, "ProjectX"
    )
    valid = _signed_storage_announcement(
        teammate_id=teammate_id,
        berth_id=core_berth_id,
        location="older-valid-location",
        signer_private_key=private_key,
        signer_key_id=key_id_from_public(public_key),
    )
    newer_broken = replace(
        _signed_storage_announcement(
            teammate_id=teammate_id,
            berth_id=core_berth_id,
            location="newer-broken-location",
            signer_private_key=private_key,
            signer_key_id=key_id_from_public(public_key),
        ),
        signature=b"\x00" * 64,
    )
    assert newer_broken.announcement_id > valid.announcement_id
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
        conn.commit()
    _insert(db_path, valid)
    _insert(db_path, newer_broken)

    alice = _self_row(TeamManager(root, alice_hex))
    assert alice["core_route_status"] == "announced"
    assert alice["effective_core_route"]["location"] == "older-valid-location"


# --------------------------------------------------------------------------- #
# Core berth coordinate failures reach the caller
# --------------------------------------------------------------------------- #


def test_list_teammates_raises_when_the_core_berth_is_missing(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _team = _alice_with_team(root)
    with sqlite3.connect(str(_team_db(root, alice_hex))) as conn:
        conn.execute(
            "DELETE FROM team_app_berth WHERE app_id IN "
            "(SELECT id FROM app WHERE name = 'SmallSeaCollectiveCore')"
        )
        conn.commit()

    with pytest.raises(Provisioning.MissingCoreBerthError):
        Provisioning.list_teammates(root, alice_hex, "ProjectX")


def test_list_teammates_raises_when_the_core_berth_is_ambiguous(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _team = _alice_with_team(root)
    with sqlite3.connect(str(_team_db(root, alice_hex))) as conn:
        berth_id, app_id = conn.execute(
            "SELECT tab.id, tab.app_id FROM team_app_berth tab "
            "JOIN app a ON a.id = tab.app_id WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (berth_id[:-1] + bytes([berth_id[-1] ^ 1]), app_id),
        )
        conn.commit()

    with pytest.raises(Provisioning.AmbiguousCoreBerthError):
        Provisioning.list_teammates(root, alice_hex, "ProjectX")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_team_detail_renders_the_core_route_column(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _team = _alice_with_team(root)

    client = TestClient(create_app(root, alice_hex))
    response = client.get("/teams/ProjectX")

    assert response.status_code == 200
    assert "Core route" in response.text
    assert "announced" in response.text


def test_teammate_berth_storage_publish_is_deduped_by_current_location(playground_dir):
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="localfolder",
        url=str(cloud_dir),
    )
    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        root,
        alice_hex,
        team_result["berth_id_hex"],
    )
    assert allocation is not None

    first = Provisioning.publish_teammate_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        team_result["teammate_id_hex"],
        team_result["berth_id_hex"],
        allocation,
    )
    second = Provisioning.publish_teammate_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        team_result["teammate_id_hex"],
        team_result["berth_id_hex"],
        allocation,
    )

    changed_allocation = dict(allocation)
    changed_allocation["location"] = "provider-final-location"
    third = Provisioning.publish_teammate_berth_storage_announcement(
        root,
        alice_hex,
        "ProjectX",
        team_result["teammate_id_hex"],
        team_result["berth_id_hex"],
        changed_allocation,
    )

    team_db = _team_db(root, alice_hex)
    with sqlite3.connect(str(team_db)) as conn:
        rows = conn.execute(
            """
            SELECT location
            FROM teammate_berth_storage_announcement
            ORDER BY announcement_id ASC
            """
        ).fetchall()

    # create_team already published the initial announcement for this berth's
    # allocation, so re-publishing the same allocation is deduped against it.
    assert first["wrote"] is False
    assert second["wrote"] is False
    assert second["announcement_id_hex"] == first["announcement_id_hex"]
    assert third["wrote"] is True
    assert [row[0] for row in rows] == [
        allocation["location"],
        "provider-final-location",
    ]
