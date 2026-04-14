import json
import pathlib
import shutil
import sqlite3

import pytest
from cryptography.exceptions import InvalidSignature

from cuttlefish.group import group_decrypt, group_encrypt
from small_sea_manager.manager import TeamManager
from small_sea_manager import provisioning
from small_sea_manager.provisioning import (
    _publish_local_device_prekey_bundle,
    _serialize_prekey_bundle,
    _store_team_certificate,
    _team_row,
    _team_sync_dir,
    create_new_participant,
    create_team,
    get_current_team_device_key,
)
from small_sea_note_to_self.db import device_local_db_path, note_to_self_sync_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
)
from sqlalchemy import create_engine, text
from wrasse_trust.identity import issue_membership_cert
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public


def _copy_team_baseline(
    src_root,
    src_participant_hex: str,
    dst_root,
    dst_participant_hex: str,
    team_name: str,
    team_id: bytes,
    member_id: bytes,
):
    src = src_root / "Participants" / src_participant_hex / team_name
    dst = dst_root / "Participants" / dst_participant_hex / team_name
    shutil.copytree(src, dst)
    with sqlite3.connect(note_to_self_sync_db_path(dst_root, dst_participant_hex)) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, member_id),
        )
        conn.commit()


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


def _team_db(root: pathlib.Path, participant_hex: str, team_name: str) -> pathlib.Path:
    return root / "Participants" / participant_hex / team_name / "Sync" / "core.db"


def _add_same_member_linked_device_bundle(root: pathlib.Path, participant_hex: str, team_name: str):
    linked_device_key, _linked_device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    return _publish_device_prekey_bundle_for_public_key(
        root,
        participant_hex,
        team_name,
        linked_device_key.public_key,
        issue_link_cert=True,
    )


def _publish_device_prekey_bundle_for_public_key(
    root: pathlib.Path,
    participant_hex: str,
    team_name: str,
    public_key: bytes,
    *,
    issue_link_cert: bool = False,
):
    team_id, _member_id = _team_row(root, participant_hex, team_name)
    if issue_link_cert:
        provisioning.issue_device_link_for_member(
            root,
            participant_hex,
            team_name,
            public_key,
        )
    device_key_id = key_id_from_public(public_key)
    identity = provisioning.generate_identity_key_pair()
    signed_prekey, _signed_prekey_private_key = provisioning.generate_signed_prekey(
        identity.signing_private_key
    )
    one_time_prekeys = provisioning.generate_one_time_prekeys(2)
    bundle = provisioning.build_prekey_bundle(
        participant_id=device_key_id,
        identity=identity,
        signed_prekey=signed_prekey,
        one_time_prekeys=[prekey for prekey, _private_key in one_time_prekeys],
    )
    team_db = _team_db(root, participant_hex, team_name)
    with sqlite3.connect(team_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO device_prekey_bundle
            (device_key_id, prekey_bundle_json, published_at)
            VALUES (?, ?, ?)
            """,
            (
                device_key_id,
                json.dumps(_serialize_prekey_bundle(bundle), sort_keys=True),
                "2026-04-13T00:00:00+00:00",
            ),
        )
        conn.commit()
    return device_key_id


def _bootstrap_remote_member_installation(workspace: pathlib.Path):
    alice_root = workspace / "alice"
    bob_root = workspace / "bob"
    alice_root.mkdir()
    bob_root.mkdir()

    alice_hex = create_new_participant(alice_root, "Alice")
    bob_hex = create_new_participant(bob_root, "Bob")

    team_result = create_team(alice_root, alice_hex, "ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    alice_member_id = bytes.fromhex(team_result["member_id_hex"])
    alice_local_db = device_local_db_path(alice_root, alice_hex)
    alice_sender = load_team_sender_key(alice_local_db, team_id)
    assert alice_sender is not None

    bob_member_id = provisioning.uuid7()
    alice_team_db = _team_db(alice_root, alice_hex, "ProjectX")
    _copy_team_baseline(
        alice_root,
        alice_hex,
        bob_root,
        bob_hex,
        "ProjectX",
        team_id,
        bob_member_id,
    )

    bob_team_id, bob_self_member_id = _team_row(bob_root, bob_hex, "ProjectX")
    assert bob_team_id == team_id
    assert bob_self_member_id == bob_member_id

    bob_team_keys = provisioning._generate_initial_team_device_key(bob_root, bob_hex, team_id)
    alice_private_key, alice_public_key = get_current_team_device_key(
        alice_root, alice_hex, "ProjectX"
    )
    bob_membership_cert = issue_membership_cert(
        subject_key=provisioning._participant_key_from_public(bob_team_keys["device_key"].public_key),
        issuer_key=provisioning._participant_key_from_public(alice_public_key),
        issuer_private_key=alice_private_key,
        team_id=team_id,
        issuer_member_id=alice_member_id,
        admitted_member_id=bob_member_id,
    )
    for team_db in (alice_team_db, _team_db(bob_root, bob_hex, "ProjectX")):
        engine = create_engine(f"sqlite:///{team_db}")
        try:
            with engine.begin() as conn:
                berth_id = conn.execute(text("SELECT id FROM team_app_berth LIMIT 1")).fetchone()[0]
                existing_member = conn.execute(
                    text("SELECT 1 FROM member WHERE id = :id"),
                    {"id": bob_member_id},
                ).fetchone()
                if existing_member is None:
                    conn.execute(
                        text("INSERT INTO member (id, display_name) VALUES (:id, :display_name)"),
                        {"id": bob_member_id, "display_name": "Bob"},
                    )
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO team_device "
                        "(device_key_id, member_id, public_key, protocol, url, bucket, created_at) "
                        "VALUES (:device_key_id, :member_id, :public_key, :protocol, :url, :bucket, :created_at)"
                    ),
                    {
                        "device_key_id": key_id_from_public(bob_team_keys["device_key"].public_key),
                        "member_id": bob_member_id,
                        "public_key": bob_team_keys["device_key"].public_key,
                        "protocol": "localfolder",
                        "url": str(workspace / "bob-cloud"),
                        "bucket": "bucket-bob",
                        "created_at": provisioning._now_iso(),
                    },
                )
                _store_team_certificate(conn, bob_membership_cert, issuer_member_id=alice_member_id)
                role_exists = conn.execute(
                    text("SELECT 1 FROM berth_role WHERE member_id = :member_id AND berth_id = :berth_id"),
                    {"member_id": bob_member_id, "berth_id": berth_id},
                ).fetchone()
                if role_exists is None:
                    conn.execute(
                        text(
                            "INSERT INTO berth_role (id, member_id, berth_id, role) "
                            "VALUES (:id, :member_id, :berth_id, :role)"
                        ),
                        {
                            "id": provisioning.uuid7(),
                            "member_id": bob_member_id,
                            "berth_id": berth_id,
                            "role": "read-write",
                        },
                    )
        finally:
            engine.dispose()

    bob_sender_record, bob_distribution = provisioning.create_sender_key(
        team_id,
        key_id_from_public(bob_team_keys["device_key"].public_key),
    )
    save_peer_sender_key(
        alice_local_db,
        team_id,
        receiver_record_from_distribution(bob_distribution),
    )
    save_peer_sender_key(
        device_local_db_path(bob_root, bob_hex),
        team_id,
        receiver_record_from_distribution(
            provisioning.distribution_message_from_record(alice_sender)
        ),
    )
    _publish_local_device_prekey_bundle(
        bob_root,
        bob_hex,
        "ProjectX",
        commit_message=None,
    )
    bob_device_key_id = key_id_from_public(bob_team_keys["device_key"].public_key)
    _copy_device_prekey_bundle_row(
        _team_db(bob_root, bob_hex, "ProjectX"),
        alice_team_db,
        bob_device_key_id,
    )

    return {
        "alice_root": alice_root,
        "alice_hex": alice_hex,
        "alice_team_db": alice_team_db,
        "alice_member_id": alice_member_id,
        "alice_sender": alice_sender,
        "bob_root": bob_root,
        "bob_hex": bob_hex,
        "bob_member_id": bob_member_id,
        "bob_device_key_id": bob_device_key_id,
        "team_id": team_id,
    }


def test_create_team_publishes_local_device_prekey_bundle(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    result = create_team(root, alice_hex, "ProjectX")
    team_db = _team_db(root, alice_hex, "ProjectX")
    with sqlite3.connect(team_db) as conn:
        row = conn.execute(
            "SELECT device_key_id, prekey_bundle_json FROM device_prekey_bundle"
        ).fetchone()
    assert row is not None
    assert len(row[0]) == 16
    assert "signed_prekey" in row[1]
    assert result["team_id_hex"]


def test_rotate_and_redistribute_round_trip_cross_member(playground_dir):
    state = _bootstrap_remote_member_installation(pathlib.Path(playground_dir))
    alice_sender_before = load_team_sender_key(
        device_local_db_path(state["alice_root"], state["alice_hex"]),
        state["team_id"],
    )
    assert alice_sender_before is not None
    alice_sender_before, old_message = group_encrypt(
        state["team_id"], alice_sender_before, b"before rotation"
    )

    rotated = provisioning.rotate_team_sender_key(
        state["alice_root"], state["alice_hex"], "ProjectX"
    )
    redistribution = provisioning.redistribute_sender_key(
        state["alice_root"],
        state["alice_hex"],
        "ProjectX",
        target_device_key_ids=[state["bob_device_key_id"]],
    )
    assert redistribution["skipped_device_key_ids_hex"] == []
    assert len(redistribution["artifacts"]) == 1

    provisioning.receive_sender_key_distribution(
        state["bob_root"],
        state["bob_hex"],
        "ProjectX",
        redistribution["artifacts"][0]["distribution_payload"],
    )

    bob_peer_for_alice = load_peer_sender_key(
        device_local_db_path(state["bob_root"], state["bob_hex"]),
        state["team_id"],
        bytes.fromhex(rotated["sender_device_key_id_hex"]),
    )
    assert bob_peer_for_alice is not None

    alice_sender_after = load_team_sender_key(
        device_local_db_path(state["alice_root"], state["alice_hex"]),
        state["team_id"],
    )
    assert alice_sender_after is not None
    alice_sender_after, new_message = group_encrypt(
        state["team_id"], alice_sender_after, b"after rotation"
    )
    bob_peer_for_alice, plaintext = group_decrypt(new_message, bob_peer_for_alice)
    assert plaintext == b"after rotation"

    with pytest.raises(InvalidSignature):
        group_decrypt(old_message, bob_peer_for_alice)


def test_remove_member_rejects_self_removal(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    result = create_team(root, alice_hex, "ProjectX")
    alice_member_id = result["member_id_hex"]

    manager = TeamManager(root, alice_hex)
    with pytest.raises(ValueError, match="cannot remove self"):
        manager.remove_member("ProjectX", alice_member_id)


def test_remove_member_requires_core_write_permission(playground_dir):
    state = _bootstrap_remote_member_installation(pathlib.Path(playground_dir))
    with sqlite3.connect(state["alice_team_db"]) as conn:
        conn.execute("UPDATE berth_role SET role = 'read-only'")
        conn.commit()

    manager = TeamManager(state["alice_root"], state["alice_hex"])
    with pytest.raises(ValueError, match="Core berth"):
        manager.remove_member("ProjectX", state["bob_member_id"].hex())


def test_remove_member_purges_local_receiver_state_and_subject_side_certs(playground_dir):
    state = _bootstrap_remote_member_installation(pathlib.Path(playground_dir))
    manager = TeamManager(state["alice_root"], state["alice_hex"])
    result = manager.remove_member("ProjectX", state["bob_member_id"].hex())

    assert result["removed_member_id_hex"] == state["bob_member_id"].hex()
    assert state["bob_device_key_id"].hex() in result["removed_device_key_ids_hex"]
    assert result["redistribution_artifacts"] == []

    with sqlite3.connect(state["alice_team_db"]) as conn:
        member_row = conn.execute(
            "SELECT 1 FROM member WHERE id = ?",
            (state["bob_member_id"],),
        ).fetchone()
        assert member_row is None
        cert_rows = conn.execute("SELECT claims FROM key_certificate").fetchall()
    assert all(json.loads(row[0]).get("member_id") != state["bob_member_id"].hex() for row in cert_rows)

    peer_sender = load_peer_sender_key(
        device_local_db_path(state["alice_root"], state["alice_hex"]),
        state["team_id"],
        state["bob_device_key_id"],
    )
    assert peer_sender is None


def test_redistribute_sender_key_includes_same_member_linked_devices(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    linked_device_key_id = _add_same_member_linked_device_bundle(root, alice_hex, "ProjectX")

    redistribution = provisioning.redistribute_sender_key(root, alice_hex, "ProjectX")

    artifact_targets = {
        artifact["target_device_key_id_hex"] for artifact in redistribution["artifacts"]
    }
    assert linked_device_key_id.hex() in artifact_targets


def test_redistribute_sender_key_skips_trusted_devices_without_prekey_bundle(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    linked_device_key, _linked_device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    provisioning.issue_device_link_for_member(
        root,
        alice_hex,
        "ProjectX",
        linked_device_key.public_key,
    )

    redistribution = provisioning.redistribute_sender_key(root, alice_hex, "ProjectX")

    assert key_id_from_public(linked_device_key.public_key).hex() in redistribution["skipped_device_key_ids_hex"]


def test_reconcile_runtime_state_does_not_repeat_delivered_current_sender_key(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    linked_device_key_id = _add_same_member_linked_device_bundle(root, alice_hex, "ProjectX")

    first = provisioning.reconcile_runtime_state(root, alice_hex, "ProjectX")

    assert first["rotated"] is False
    assert linked_device_key_id.hex() in {
        artifact["target_device_key_id_hex"] for artifact in first["redistribution_artifacts"]
    }
    for artifact in first["redistribution_artifacts"]:
        provisioning.mark_redistribution_delivery(
            root,
            alice_hex,
            team_id=bytes.fromhex(first["team_id_hex"]),
            sender_device_key_id=bytes.fromhex(artifact["sender_device_key_id_hex"]),
            sender_chain_id=bytes.fromhex(artifact["sender_chain_id_hex"]),
            target_device_key_id=bytes.fromhex(artifact["target_device_key_id_hex"]),
        )

    second = provisioning.reconcile_runtime_state(root, alice_hex, "ProjectX")

    assert second["redistribution_artifacts"] == []
    assert second["skipped_device_key_ids_hex"] == []


def test_reconcile_runtime_state_retries_after_bundle_publication(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    linked_device_key, _linked_device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    provisioning.issue_device_link_for_member(
        root,
        alice_hex,
        "ProjectX",
        linked_device_key.public_key,
    )
    linked_device_key_id = key_id_from_public(linked_device_key.public_key)

    first = provisioning.reconcile_runtime_state(root, alice_hex, "ProjectX")
    assert linked_device_key_id.hex() in first["skipped_device_key_ids_hex"]
    assert first["redistribution_artifacts"] == []

    _publish_device_prekey_bundle_for_public_key(
        root,
        alice_hex,
        "ProjectX",
        linked_device_key.public_key,
    )

    second = provisioning.reconcile_runtime_state(root, alice_hex, "ProjectX")
    assert linked_device_key_id.hex() in {
        artifact["target_device_key_id_hex"] for artifact in second["redistribution_artifacts"]
    }


def test_reconcile_runtime_state_rotates_after_adopted_member_removal(playground_dir):
    state = _bootstrap_remote_member_installation(pathlib.Path(playground_dir))
    alice_sender_before = load_team_sender_key(
        device_local_db_path(state["alice_root"], state["alice_hex"]),
        state["team_id"],
    )
    assert alice_sender_before is not None
    removed_member_id = provisioning.uuid7()
    provisioning._store_runtime_reconciliation_state(
        state["alice_root"],
        state["alice_hex"],
        team_id=state["team_id"],
        trusted_member_ids_hex=[
            state["alice_member_id"].hex(),
            state["bob_member_id"].hex(),
            removed_member_id.hex(),
        ],
        trusted_device_key_ids_hex=[
            alice_sender_before.sender_device_key_id.hex(),
            state["bob_device_key_id"].hex(),
        ],
        last_sender_device_key_id=alice_sender_before.sender_device_key_id,
        last_sender_chain_id=alice_sender_before.chain_id,
    )

    reconciliation = provisioning.reconcile_runtime_state(
        state["alice_root"],
        state["alice_hex"],
        "ProjectX",
    )

    assert reconciliation["rotated"] is True
    assert removed_member_id.hex() in reconciliation["removed_member_ids_hex"]
    assert reconciliation["sender_chain_id_hex"] != alice_sender_before.chain_id.hex()


def test_parallel_device_prekey_bundle_rows_merge(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root = workspace / "alice"
    root.mkdir()
    alice_hex = create_new_participant(root, "Alice")
    create_team(root, alice_hex, "ProjectX")
    repo_a = _team_sync_dir(root, alice_hex, "ProjectX")
    repo_b = workspace / "repo-b"
    shutil.copytree(repo_a, repo_b)

    team_db_a = repo_a / "core.db"
    team_db_b = repo_b / "core.db"
    with sqlite3.connect(team_db_a) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO device_prekey_bundle
            (device_key_id, prekey_bundle_json, published_at)
            VALUES (?, ?, ?)
            """,
            (b"a" * 16, '{"row":"a"}', "2026-04-13T00:00:00+00:00"),
        )
        conn.commit()
    with sqlite3.connect(team_db_b) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO device_prekey_bundle
            (device_key_id, prekey_bundle_json, published_at)
            VALUES (?, ?, ?)
            """,
            (b"b" * 16, '{"row":"b"}', "2026-04-13T00:00:01+00:00"),
        )
        conn.commit()

    provisioning.CodSync.gitCmd(["-C", str(repo_a), "add", "core.db"])
    provisioning.CodSync.gitCmd(["-C", str(repo_a), "commit", "-m", "Publish row A"])
    provisioning.CodSync.gitCmd(["-C", str(repo_b), "add", "core.db"])
    provisioning.CodSync.gitCmd(["-C", str(repo_b), "commit", "-m", "Publish row B"])
    provisioning.CodSync.gitCmd(["-C", str(repo_a), "remote", "add", "other", str(repo_b)])
    provisioning.CodSync.gitCmd(["-C", str(repo_a), "fetch", "other"])
    provisioning.CodSync.gitCmd(["-C", str(repo_a), "merge", "--no-edit", "other/main"])

    with sqlite3.connect(team_db_a) as conn:
        rows = conn.execute(
            "SELECT device_key_id FROM device_prekey_bundle ORDER BY device_key_id"
        ).fetchall()
    device_key_ids = {row[0] for row in rows}
    assert b"a" * 16 in device_key_ids
    assert b"b" * 16 in device_key_ids
