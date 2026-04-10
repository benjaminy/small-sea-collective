import pathlib
import sqlite3
import time

from small_sea_manager.manager import TeamManager, bootstrap_existing_identity, create_identity_join_request
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path


def _count_rows(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_localfolder_identity_bootstrap_roundtrip(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    assert welcome["auth_string"] == join_request["auth_string"]

    bootstrap = bootstrap_existing_identity(root2, welcome["welcome_bundle"])
    assert bootstrap["participant_hex"] == alice_hex

    shared1 = note_to_self_sync_db_path(root1, alice_hex)
    shared2 = note_to_self_sync_db_path(root2, alice_hex)
    local2 = device_local_db_path(root2, alice_hex)
    assert shared1.exists()
    assert shared2.exists()
    assert local2.exists()

    assert _count_rows(shared1, "SELECT COUNT(*) FROM user_device") == 2
    assert _count_rows(shared2, "SELECT COUNT(*) FROM user_device") == 2
    assert _count_rows(local2, "SELECT COUNT(*) FROM cloud_storage_credential") == 0
    assert _count_rows(local2, "SELECT COUNT(*) FROM note_to_self_device_key_secret") == 1

    joined_device_id = bytes.fromhex(bootstrap["joining_device_id_hex"])
    with sqlite3.connect(local2) as conn:
        row = conn.execute(
            "SELECT private_key_ref FROM note_to_self_device_key_secret WHERE device_id = ?",
            (joined_device_id,),
        ).fetchone()
    assert row is not None
    assert pathlib.Path(row[0]).exists()

    manager2 = TeamManager(root2, alice_hex)
    create_team_result = manager2.create_team("JoinedDeviceTeam")
    team_id = bytes.fromhex(create_team_result["team_id_hex"])
    with sqlite3.connect(shared2) as conn:
        team_device_row = conn.execute(
            "SELECT device_id FROM team_device_key WHERE team_id = ?",
            (team_id,),
        ).fetchone()
    assert team_device_row is not None
    assert team_device_row[0] == joined_device_id


def test_identity_bootstrap_bundle_expiry_and_reissue(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    expired = alice_manager.authorize_identity_join(
        join_request["join_request_artifact"],
        expires_in_seconds=1,
    )
    time.sleep(1.2)

    try:
        bootstrap_existing_identity(root2, expired["welcome_bundle"])
        assert False, "Expected expired welcome bundle to fail"
    except ValueError as exn:
        assert "expired" in str(exn).lower()

    fresh = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap = bootstrap_existing_identity(root2, fresh["welcome_bundle"])
    assert bootstrap["participant_hex"] == alice_hex
