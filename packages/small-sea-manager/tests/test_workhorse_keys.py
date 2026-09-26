"""Micro tests for device-local, per-berth workhorse signing keys."""

import base64
import pathlib
import sqlite3

import small_sea_manager.provisioning as provisioning
from small_sea_manager.manager import TeamManager


def _ssh_key_bytes(key: str) -> bytes:
    return base64.b64decode(key.split()[1])[-32:]


def _provision_device(root: pathlib.Path, label: str):
    root.mkdir(parents=True, exist_ok=True)
    participant = provisioning.create_new_participant(root, label)
    manager = TeamManager(root, participant)
    teams = {}
    for team_name in ("ProjectX", "ProjectY"):
        provisioning.create_team(root, participant, team_name)
        provisioning.register_app_for_participant(
            root, participant, "SmallSeaCollectiveFiles"
        )
        provisioning.activate_app_for_team(
            root, participant, team_name, "SmallSeaCollectiveFiles"
        )
        core = provisioning.derive_team_join_state(root, participant, team_name)
        files = provisioning.derive_team_join_state(
            root, participant, team_name, "SmallSeaCollectiveFiles"
        )
        teams[team_name] = (core["berth_id"], files["berth_id"])
    return participant, manager, teams


def test_workhorse_keys_partition_device_and_berth(playground_dir):
    root = pathlib.Path(playground_dir)
    devices = [
        _provision_device(root / "device-a", "Alice"),
        _provision_device(root / "device-b", "Alice"),
    ]
    keys = []
    existing_public_keys = []
    for device_root, (participant, _manager, teams) in zip(
        (root / "device-a", root / "device-b"), devices
    ):
        with sqlite3.connect(
            device_root / "Participants" / participant / "NoteToSelf" / "Sync" / "core.db"
        ) as conn:
            existing_public_keys.append(conn.execute(
                "SELECT signing_key FROM user_device LIMIT 1"
            ).fetchone()[0])
        for team_name, berths in teams.items():
            _private_key, team_public_key = provisioning.get_current_team_device_key(
                device_root, participant, team_name
            )
            existing_public_keys.append(team_public_key)
            keys.extend(
                provisioning.get_workhorse_signing_public_key(
                    device_root, participant, berth_id
                )
                for berth_id in berths
            )

    public_bytes = [_ssh_key_bytes(key) for key in keys]
    assert len(set(public_bytes)) == 8
    assert not set(public_bytes).intersection(existing_public_keys)


def test_workhorse_secret_stays_local(playground_dir):
    root = pathlib.Path(playground_dir)
    participant, _manager, teams = _provision_device(root, "Alice")
    berth_id = teams["ProjectX"][0]
    secret = provisioning.get_workhorse_signing_key(root, participant, berth_id)

    # A fresh Manager reads the same persisted key after restart.
    TeamManager(root, participant)
    assert provisioning.get_workhorse_signing_key(root, participant, berth_id) == secret

    shared_dirs = [
        root / "Participants" / participant / "NoteToSelf" / "Sync",
        root / "Participants" / participant / "ProjectX" / "Sync",
    ]
    for sync_dir in shared_dirs:
        for path in sync_dir.rglob("*"):
            if path.is_file():
                assert secret not in path.read_bytes(), path
