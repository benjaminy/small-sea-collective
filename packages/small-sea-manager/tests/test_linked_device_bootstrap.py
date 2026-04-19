import base64
import json
import pathlib
import shutil
import sqlite3
import subprocess

import pytest
from cryptography.exceptions import InvalidSignature
from sqlalchemy import create_engine, text

from cuttlefish.group import group_decrypt, group_encrypt
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager import provisioning
from small_sea_manager.provisioning import (
    _publish_local_device_prekey_bundle,
    add_cloud_storage,
    create_new_participant,
)
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
    save_team_sender_key,
)
from wrasse_trust.identity import issue_membership_cert
from wrasse_trust.keys import key_id_from_public


def _copy_team_baseline(
    src_root,
    dst_root,
    src_participant_hex: str,
    dst_participant_hex: str,
    team_name: str,
    team_id: bytes,
    member_id: bytes,
):
    src = provisioning._team_sync_dir(src_root, src_participant_hex, team_name).parent
    dst = provisioning._team_sync_dir(dst_root, dst_participant_hex, team_name).parent
    shutil.copytree(src, dst)
    with sqlite3.connect(note_to_self_sync_db_path(dst_root, dst_participant_hex)) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, member_id),
        )
        conn.commit()


def _row_count(db_path, sql, params=()):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def _git_commit_count(repo_dir: pathlib.Path) -> int:
    return int(
        subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-list", "--count", "HEAD"],
            text=True,
        ).strip()
    )


def _team_db(root: pathlib.Path, participant_hex: str, team_name: str) -> pathlib.Path:
    return provisioning._team_db_path(root, participant_hex, team_name)


def _copy_device_prekey_bundle_row(src_team_db: pathlib.Path, dst_team_db: pathlib.Path, device_key_id: bytes):
    with sqlite3.connect(src_team_db) as src_conn:
        row = src_conn.execute(
            "SELECT device_key_id, prekey_bundle_json, published_at "
            "FROM device_prekey_bundle WHERE device_key_id = ?",
            (device_key_id,),
        ).fetchone()
    assert row is not None
    with sqlite3.connect(dst_team_db) as dst_conn:
        dst_conn.execute(
            """
            INSERT OR REPLACE INTO device_prekey_bundle
            (device_key_id, prekey_bundle_json, published_at)
            VALUES (?, ?, ?)
            """,
            row,
        )
        dst_conn.commit()


def _bootstrap_remote_member_installation(
    alice_root: pathlib.Path,
    alice_hex: str,
    bob_root: pathlib.Path,
    bob_hex: str,
    team_name: str,
    team_id: bytes,
    alice_member_id: bytes,
):
    bob_member_id = provisioning.uuid7()
    _copy_team_baseline(
        alice_root,
        bob_root,
        alice_hex,
        bob_hex,
        team_name,
        team_id,
        bob_member_id,
    )

    bob_team_keys = provisioning._generate_initial_team_device_key(bob_root, bob_hex, team_id)
    alice_private_key, alice_public_key = provisioning.get_current_team_device_key(
        alice_root,
        alice_hex,
        team_name,
    )
    membership_cert = issue_membership_cert(
        subject_key=provisioning._participant_key_from_public(bob_team_keys["device_key"].public_key),
        issuer_key=provisioning._participant_key_from_public(alice_public_key),
        issuer_private_key=alice_private_key,
        team_id=team_id,
        issuer_member_id=alice_member_id,
        admitted_member_id=bob_member_id,
    )

    bob_device_key_id = key_id_from_public(bob_team_keys["device_key"].public_key)
    for team_db, root in (
        (_team_db(alice_root, alice_hex, team_name), alice_root),
        (_team_db(bob_root, bob_hex, team_name), bob_root),
    ):
        engine = create_engine(f"sqlite:///{team_db}")
        try:
            with engine.begin() as conn:
                existing_member = conn.execute(
                    text("SELECT 1 FROM member WHERE id = :id"),
                    {"id": bob_member_id},
                ).fetchone()
                if existing_member is None:
                    conn.execute(
                        text("INSERT INTO member (id, display_name) VALUES (:id, :display_name)"),
                        {"id": bob_member_id, "display_name": "Bob"},
                    )
                berth_rows = conn.execute(text("SELECT id FROM team_app_berth")).fetchall()
                for berth_row in berth_rows:
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO berth_role (id, member_id, berth_id, role) "
                            "VALUES (:id, :member_id, :berth_id, :role)"
                        ),
                        {
                            "id": provisioning.uuid7(),
                            "member_id": bob_member_id,
                            "berth_id": berth_row[0],
                            "role": "read-write",
                        },
                    )
                provisioning._store_team_certificate(
                    conn,
                    membership_cert,
                    issuer_member_id=alice_member_id,
                )
                provisioning._upsert_team_device_row(
                    conn,
                    bob_member_id,
                    bob_team_keys["device_key"].public_key,
                    protocol="localfolder",
                    url=str(root / "bob-cloud"),
                    bucket="bob-bucket",
                )
        finally:
            engine.dispose()

    bob_local_db = device_local_db_path(bob_root, bob_hex)
    bob_sender_record, bob_distribution = provisioning.create_sender_key(
        team_id,
        bob_device_key_id,
    )
    save_team_sender_key(bob_local_db, team_id, bob_sender_record)
    save_peer_sender_key(
        device_local_db_path(alice_root, alice_hex),
        team_id,
        receiver_record_from_distribution(bob_distribution),
    )
    _publish_local_device_prekey_bundle(
        bob_root,
        bob_hex,
        team_name,
        commit_message=None,
    )
    _copy_device_prekey_bundle_row(
        _team_db(bob_root, bob_hex, team_name),
        _team_db(alice_root, alice_hex, team_name),
        bob_device_key_id,
    )

    return {
        "member_id": bob_member_id,
        "device_key_id": bob_device_key_id,
        "local_db": bob_local_db,
    }


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
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)

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
    assert set(finalized) == {"bootstrap_id_hex"}
    assert finalized_again == finalized

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

    assert load_peer_sender_key(local_db1, team_id, root2_sender.sender_device_key_id) is None
    redistribution = provisioning.redistribute_sender_key(root2, alice_hex, "ProjectX")
    assert redistribution["skipped_device_key_ids_hex"] == []
    assert len(redistribution["artifacts"]) == 1
    provisioning.receive_sender_key_distribution(
        root1,
        alice_hex,
        "ProjectX",
        redistribution["artifacts"][0]["distribution_payload"],
    )

    root1_peer_for_b = load_peer_sender_key(local_db1, team_id, root2_sender.sender_device_key_id)
    assert root1_peer_for_b is not None
    root2_sender, message_to_a = group_encrypt(root2_sender.group_id, root2_sender, b"hello from B")
    root1_peer_for_b, plaintext_to_a = group_decrypt(message_to_a, root1_peer_for_b)
    assert plaintext_to_a == b"hello from B"

    with sqlite3.connect(_team_db(root1, alice_hex, "ProjectX")) as conn:
        cert_types = [row[0] for row in conn.execute(
            "SELECT cert_type FROM key_certificate ORDER BY issued_at"
        ).fetchall()]
    assert cert_types == ["membership", "device_link"]

    with sqlite3.connect(_team_db(root2, alice_hex, "ProjectX")) as conn:
        cert_types = [row[0] for row in conn.execute(
            "SELECT cert_type FROM key_certificate ORDER BY issued_at"
        ).fetchall()]
    assert cert_types == ["membership", "device_link"]

    assert _row_count(
        local_db2,
        "SELECT COUNT(*) FROM linked_team_bootstrap_session",
    ) == 1
    with sqlite3.connect(note_to_self_sync_db_path(root2, alice_hex)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("linked_team_bootstrap_session",),
        ).fetchone()[0] == 0


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
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

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


def test_linked_device_bootstrap_create_replay_returns_stored_bundle_without_extra_commit(
    playground_dir,
):
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
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")

    team_repo_dir = _team_db(root1, alice_hex, "ProjectX").parent
    before_commits = _git_commit_count(team_repo_dir)

    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )
    after_first_create_commits = _git_commit_count(team_repo_dir)
    replayed = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )
    after_replay_commits = _git_commit_count(team_repo_dir)

    assert replayed == created
    assert after_first_create_commits == before_commits + 1
    assert after_replay_commits == after_first_create_commits
    assert _row_count(
        device_local_db_path(root1, alice_hex),
        "SELECT COUNT(*) FROM pending_linked_team_bootstrap",
    ) == 1

    with sqlite3.connect(_team_db(root1, alice_hex, "ProjectX")) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM key_certificate WHERE cert_type = 'device_link'"
        ).fetchone()[0] == 1


def test_linked_device_bootstrap_retry_after_interrupted_finalize_is_idempotent(
    playground_dir, monkeypatch
):
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
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )

    original_update = provisioning._update_linked_team_bootstrap_session

    def fail_after_cert_store(*args, **kwargs):
        raise RuntimeError("simulated crash after cert store")

    monkeypatch.setattr(
        provisioning,
        "_update_linked_team_bootstrap_session",
        fail_after_cert_store,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        manager2.finalize_linked_device_bootstrap(
            "ProjectX",
            created["bootstrap_bundle"],
        )

    monkeypatch.setattr(
        provisioning,
        "_update_linked_team_bootstrap_session",
        original_update,
    )
    retried = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    retried_again = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    assert retried_again == retried


def test_linked_device_bootstrap_peer_sender_keys_transferred(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    bob_root = workspace / "install-bob"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    bob_root.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    bob_hex = create_new_participant(bob_root, "Bob")

    join_request = create_identity_join_request(root2)
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    team_result = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    member_id = bytes.fromhex(team_result["member_id_hex"])
    bob = _bootstrap_remote_member_installation(
        root1,
        alice_hex,
        bob_root,
        bob_hex,
        "ProjectX",
        team_id,
        member_id,
    )
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)
    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None

    alice_peer_for_bob = load_peer_sender_key(local_db1, team_id, bob["device_key_id"])
    assert alice_peer_for_bob is not None
    bob_sender, historical_bob_message = group_encrypt(team_id, bob_sender, b"before bootstrap")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)
    alice_peer_for_bob, plaintext = group_decrypt(historical_bob_message, alice_peer_for_bob)
    save_peer_sender_key(local_db1, team_id, alice_peer_for_bob)
    assert plaintext == b"before bootstrap"

    with sqlite3.connect(_team_db(root2, alice_hex, "ProjectX")) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM team_device WHERE device_key_id = ?",
            (bob["device_key_id"],),
        ).fetchone()[0] == 1

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )
    finalized = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    assert "bootstrap_id_hex" in finalized

    root2_peer_for_bob = load_peer_sender_key(local_db2, team_id, bob["device_key_id"])
    assert root2_peer_for_bob is not None
    with pytest.raises(ValueError):
        group_decrypt(historical_bob_message, root2_peer_for_bob)

    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None
    bob_sender, bob_message = group_encrypt(team_id, bob_sender, b"hello from Bob")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)
    root2_peer_for_bob, plaintext = group_decrypt(bob_message, root2_peer_for_bob)
    assert plaintext == b"hello from Bob"


def test_linked_device_bootstrap_transfers_skipped_peer_sender_keys(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    bob_root = workspace / "install-bob"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    bob_root.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    bob_hex = create_new_participant(bob_root, "Bob")

    join_request = create_identity_join_request(root2)
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    team_result = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    member_id = bytes.fromhex(team_result["member_id_hex"])
    bob = _bootstrap_remote_member_installation(
        root1,
        alice_hex,
        bob_root,
        bob_hex,
        "ProjectX",
        team_id,
        member_id,
    )
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)
    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None

    alice_peer_for_bob = load_peer_sender_key(local_db1, team_id, bob["device_key_id"])
    assert alice_peer_for_bob is not None
    bob_sender, first_bob_message = group_encrypt(team_id, bob_sender, b"first from Bob")
    bob_sender, second_bob_message = group_encrypt(team_id, bob_sender, b"second from Bob")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)

    alice_peer_for_bob, plaintext = group_decrypt(second_bob_message, alice_peer_for_bob)
    assert plaintext == b"second from Bob"
    assert alice_peer_for_bob.skipped_message_keys
    save_peer_sender_key(local_db1, team_id, alice_peer_for_bob)

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )
    finalized = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    assert "bootstrap_id_hex" in finalized

    root2_peer_for_bob = load_peer_sender_key(local_db2, team_id, bob["device_key_id"])
    assert root2_peer_for_bob is not None
    assert root2_peer_for_bob.skipped_message_keys == alice_peer_for_bob.skipped_message_keys

    root2_peer_for_bob, plaintext = group_decrypt(first_bob_message, root2_peer_for_bob)
    assert plaintext == b"first from Bob"


def test_linked_device_bootstrap_exclusion_cuts_off_peer(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    bob_root = workspace / "install-bob"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    bob_root.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    bob_hex = create_new_participant(bob_root, "Bob")

    join_request = create_identity_join_request(root2)
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    team_result = manager1.create_team("ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    member_id = bytes.fromhex(team_result["member_id_hex"])
    bob = _bootstrap_remote_member_installation(
        root1,
        alice_hex,
        bob_root,
        bob_hex,
        "ProjectX",
        team_id,
        member_id,
    )
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)
    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None
    alice_peer_for_bob = load_peer_sender_key(local_db1, team_id, bob["device_key_id"])
    assert alice_peer_for_bob is not None

    bob_sender, initial_bob_message = group_encrypt(team_id, bob_sender, b"before bootstrap")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)
    alice_peer_for_bob, plaintext = group_decrypt(initial_bob_message, alice_peer_for_bob)
    save_peer_sender_key(local_db1, team_id, alice_peer_for_bob)
    assert plaintext == b"before bootstrap"

    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join("ProjectX")
    created = manager1.create_linked_device_bootstrap(
        "ProjectX",
        prepared["join_request_bundle"],
    )
    finalized = manager2.finalize_linked_device_bootstrap(
        "ProjectX",
        created["bootstrap_bundle"],
    )
    assert "bootstrap_id_hex" in finalized

    root2_peer_for_bob = load_peer_sender_key(local_db2, team_id, bob["device_key_id"])
    assert root2_peer_for_bob is not None
    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None
    bob_sender, readable_message = group_encrypt(team_id, bob_sender, b"readable after bootstrap")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)
    root2_peer_for_bob, plaintext = group_decrypt(readable_message, root2_peer_for_bob)
    save_peer_sender_key(local_db2, team_id, root2_peer_for_bob)
    assert plaintext == b"readable after bootstrap"

    _alice_private_key, alice_public_key = provisioning.get_current_team_device_key(
        root1,
        alice_hex,
        "ProjectX",
    )
    alice_device_key_id = key_id_from_public(alice_public_key)
    rotated = provisioning.rotate_team_sender_key(bob_root, bob_hex, "ProjectX")
    redistribution = provisioning.redistribute_sender_key(
        bob_root,
        bob_hex,
        "ProjectX",
        target_device_key_ids=[alice_device_key_id],
    )
    assert redistribution["skipped_device_key_ids_hex"] == []
    assert {
        artifact["target_device_key_id_hex"] for artifact in redistribution["artifacts"]
    } == {alice_device_key_id.hex()}

    provisioning.receive_sender_key_distribution(
        root1,
        alice_hex,
        "ProjectX",
        redistribution["artifacts"][0]["distribution_payload"],
    )
    alice_peer_for_bob = load_peer_sender_key(
        local_db1,
        team_id,
        bytes.fromhex(rotated["sender_device_key_id_hex"]),
    )
    assert alice_peer_for_bob is not None

    bob_sender = load_team_sender_key(bob["local_db"], team_id)
    assert bob_sender is not None
    bob_sender, post_rotation_message = group_encrypt(team_id, bob_sender, b"after exclusion")
    save_team_sender_key(bob["local_db"], team_id, bob_sender)

    alice_peer_for_bob, plaintext = group_decrypt(post_rotation_message, alice_peer_for_bob)
    assert plaintext == b"after exclusion"
    with pytest.raises(InvalidSignature):
        group_decrypt(post_rotation_message, root2_peer_for_bob)


def test_linked_device_bootstrap_prepare_reentry_is_rejected(playground_dir):
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
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, "ProjectX", team_id, member_id)

    manager2 = TeamManager(root2, alice_hex)
    manager2.prepare_linked_device_team_join("ProjectX")
    with pytest.raises(ValueError, match="already in progress"):
        manager2.prepare_linked_device_team_join("ProjectX")
