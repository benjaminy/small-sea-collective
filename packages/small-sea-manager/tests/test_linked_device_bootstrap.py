import base64
import json
import pathlib
import shutil
import sqlite3
import subprocess

import pytest
from sqlalchemy import create_engine, text

from cuttlefish.group import group_decrypt, group_encrypt
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager import provisioning
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    save_team_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
)
from wrasse_trust.identity import issue_membership_cert
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public


def _copy_team_baseline(root1, root2, participant_hex: str, team_name: str, team_id: bytes, member_id: bytes):
    src = provisioning._team_sync_dir(root1, participant_hex, team_name).parent
    dst = provisioning._team_sync_dir(root2, participant_hex, team_name).parent
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


def _git_commit_count(repo_dir: pathlib.Path) -> int:
    return int(
        subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-list", "--count", "HEAD"],
            text=True,
        ).strip()
    )


def _team_db(root: pathlib.Path, participant_hex: str, team_name: str) -> pathlib.Path:
    return provisioning._team_db_path(root, participant_hex, team_name)


def _add_remote_member_to_team(
    root: pathlib.Path,
    participant_hex: str,
    team_name: str,
    *,
    display_name: str,
):
    team_id, issuer_member_id = provisioning._team_row(root, participant_hex, team_name)
    issuer_private_key, issuer_public_key = provisioning.get_current_team_device_key(
        root,
        participant_hex,
        team_name,
    )
    remote_member_id = provisioning.uuid7()
    remote_key, _remote_private_key = generate_key_pair(ProtectionLevel.DAILY)
    membership_cert = issue_membership_cert(
        subject_key=provisioning._participant_key_from_public(remote_key.public_key),
        issuer_key=provisioning._participant_key_from_public(issuer_public_key),
        issuer_private_key=issuer_private_key,
        team_id=team_id,
        issuer_member_id=issuer_member_id,
        admitted_member_id=remote_member_id,
    )

    team_db = _team_db(root, participant_hex, team_name)
    engine = create_engine(f"sqlite:///{team_db}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO member (id, display_name) VALUES (:id, :display_name)"),
                {"id": remote_member_id, "display_name": display_name},
            )
            berth_rows = conn.execute(text("SELECT id FROM team_app_berth")).fetchall()
            for berth_row in berth_rows:
                conn.execute(
                    text(
                        "INSERT INTO berth_role (id, member_id, berth_id, role) "
                        "VALUES (:id, :member_id, :berth_id, :role)"
                    ),
                    {
                        "id": provisioning.uuid7(),
                        "member_id": remote_member_id,
                        "berth_id": berth_row[0],
                        "role": "read-write",
                    },
                )
            provisioning._store_team_certificate(
                conn,
                membership_cert,
                issuer_member_id=issuer_member_id,
            )
            provisioning._upsert_team_device_row(
                conn,
                remote_member_id,
                remote_key.public_key,
                protocol="localfolder",
                url=str(root / f"{display_name.lower()}-cloud"),
                bucket=f"{display_name.lower()}-bucket",
            )
    finally:
        engine.dispose()

    return {
        "member_id": remote_member_id,
        "public_key": remote_key.public_key,
        "device_key_id": key_id_from_public(remote_key.public_key),
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
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

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
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

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


def test_linked_device_bootstrap_requires_real_redistribution_for_other_senders(playground_dir):
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

    bob = _add_remote_member_to_team(
        root1,
        alice_hex,
        "ProjectX",
        display_name="Bob",
    )
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

    local_db1 = device_local_db_path(root1, alice_hex)
    local_db2 = device_local_db_path(root2, alice_hex)

    bob_sender, bob_distribution = provisioning.create_sender_key(
        team_id,
        bob["device_key_id"],
    )
    save_peer_sender_key(
        local_db1,
        team_id,
        receiver_record_from_distribution(bob_distribution),
    )

    alice_peer_for_bob = load_peer_sender_key(local_db1, team_id, bob["device_key_id"])
    assert alice_peer_for_bob is not None
    bob_sender, bob_message = group_encrypt(team_id, bob_sender, b"hello from Bob")
    alice_peer_for_bob, plaintext = group_decrypt(bob_message, alice_peer_for_bob)
    assert plaintext == b"hello from Bob"

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
    manager1.complete_linked_device_bootstrap(
        "ProjectX",
        finalized["sender_distribution_payload"],
    )

    assert load_peer_sender_key(local_db2, team_id, bob["device_key_id"]) is None


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
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

    manager2 = TeamManager(root2, alice_hex)
    manager2.prepare_linked_device_team_join("ProjectX")
    with pytest.raises(ValueError, match="already in progress"):
        manager2.prepare_linked_device_team_join("ProjectX")


def test_linked_device_bootstrap_retry_after_interrupted_complete_is_idempotent(
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
    _copy_team_baseline(root1, root2, alice_hex, "ProjectX", team_id, member_id)

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

    original_clear = provisioning._clear_pending_linked_team_bootstrap

    def fail_after_save(*args, **kwargs):
        raise RuntimeError("simulated crash after save before clear")

    monkeypatch.setattr(
        provisioning,
        "_clear_pending_linked_team_bootstrap",
        fail_after_save,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        manager1.complete_linked_device_bootstrap(
            "ProjectX",
            finalized["sender_distribution_payload"],
        )

    monkeypatch.setattr(
        provisioning,
        "_clear_pending_linked_team_bootstrap",
        original_clear,
    )
    retried = manager1.complete_linked_device_bootstrap(
        "ProjectX",
        finalized["sender_distribution_payload"],
    )
    retried_again = manager1.complete_linked_device_bootstrap(
        "ProjectX",
        finalized["sender_distribution_payload"],
    )
    assert retried_again == retried
