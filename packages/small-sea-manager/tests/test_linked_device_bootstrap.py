import base64
import json
import pathlib
import shutil
import sqlite3

import pytest

from cuttlefish.group import group_decrypt, group_encrypt
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    save_team_sender_key,
)


def _copy_team_baseline(root1, root2, participant_hex: str, team_name: str, team_id: bytes, member_id: bytes):
    src = root1 / "Participants" / participant_hex / team_name
    dst = root2 / "Participants" / participant_hex / team_name
    shutil.copytree(src, dst)
    with sqlite3.connect(note_to_self_sync_db_path(root2, participant_hex)) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, member_id),
        )
        conn.commit()


def _row_count(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_linked_device_bootstrap_round_trip_same_member(playground_dir):
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
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    team_result = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    member_id = bytes.fromhex(team_result["member_id_hex"])
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)

    pre_bootstrap_sender = load_team_sender_key(local_db1, team_id)
    assert pre_bootstrap_sender is not None
    pre_bootstrap_sender, historical_message = group_encrypt(
        team_id,
        pre_bootstrap_sender,
        b"before bootstrap",
    )
    save_team_sender_key(local_db1, team_id, pre_bootstrap_sender)

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")

    assert _row_count(
        local_db1,
        "SELECT COUNT(*) FROM pending_linked_team_bootstrap",
    ) == 0

    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )

    assert _row_count(
        local_db1,
        "SELECT COUNT(*) FROM pending_linked_team_bootstrap",
    ) == 1

    finalized = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    finalized_again = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    assert finalized_again == finalized

    manager1.complete_linked_device_bootstrap(
        "ProjectX",
        finalized["sender_distribution_payload"],
    )

    assert _row_count(
        local_db1,
        "SELECT COUNT(*) FROM pending_linked_team_bootstrap",
    ) == 0

    root2_peer_for_a = load_peer_sender_key(
        local_db2,
        team_id,
        pre_bootstrap_sender.sender_device_key_id,
    )
    assert root2_peer_for_a is not None
    with pytest.raises(ValueError):
        group_decrypt(historical_message, root2_peer_for_a)

    root1_sender = load_team_sender_key(local_db1, team_id)
    root2_sender = load_team_sender_key(local_db2, team_id)
    assert root1_sender is not None
    assert root2_sender is not None
    assert root1_sender.sender_device_key_id != root2_sender.sender_device_key_id

    root1_sender, message_to_b = group_encrypt(root1_sender.group_id, root1_sender, b"hello from A")
    root2_peer_for_a = load_peer_sender_key(local_db2, team_id, root1_sender.sender_device_key_id)
    assert root2_peer_for_a is not None
    root2_peer_for_a, plaintext_to_b = group_decrypt(message_to_b, root2_peer_for_a)
    assert plaintext_to_b == b"hello from A"

    root2_sender, message_to_a = group_encrypt(root2_sender.group_id, root2_sender, b"hello from B")
    root1_peer_for_b = load_peer_sender_key(local_db1, team_id, root2_sender.sender_device_key_id)
    assert root1_peer_for_b is not None
    root1_peer_for_b, plaintext_to_a = group_decrypt(message_to_a, root1_peer_for_b)
    assert plaintext_to_a == b"hello from B"

    with sqlite3.connect(
        root1 / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    ) as conn:
        cert_types = [row[0] for row in conn.execute(
            "SELECT cert_type FROM key_certificate ORDER BY issued_at"
        ).fetchall()]
    assert cert_types == ["membership", "device_link"]

    with sqlite3.connect(note_to_self_sync_db_path(root2, alice_hex)) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT COUNT(*) FROM linked_team_bootstrap_session").fetchone()


def test_linked_device_bootstrap_rejects_invalid_join_signatures(playground_dir):
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
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    team_result = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    member_id = bytes.fromhex(team_result["member_id_hex"])
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    request = json.loads(
        base64.b64decode(prepared["join_request_bundle"].encode("ascii")).decode("utf-8")
    )

    tampered_note_to_self = dict(request)
    tampered_note_to_self["note_to_self_signature"] = ("00" * 64)
    with pytest.raises(ValueError, match="NoteToSelf signature"):
        manager1.create_linked_device_bootstrap(
            "ProjectX",
            base64.b64encode(json.dumps(tampered_note_to_self).encode("utf-8")).decode("ascii"),
        )

    tampered_team = dict(request)
    tampered_team["team_device_signature"] = ("11" * 64)
    with pytest.raises(ValueError, match="Team X signature"):
        manager1.create_linked_device_bootstrap(
            "ProjectX",
            base64.b64encode(json.dumps(tampered_team).encode("utf-8")).decode("ascii"),
        )
