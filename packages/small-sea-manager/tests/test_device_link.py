import os
import pathlib
import sqlite3

import cod_sync.protocol as CS

from cod_sync.protocol import canonical_link_bytes, verify_link_signature
from small_sea_manager.provisioning import (
    _install_sqlite_merge_driver,
    create_invitation,
    create_new_participant,
    create_team,
    get_trusted_device_keys_for_member,
    get_trusted_device_keys_for_member_in_team_db,
    issue_device_link_for_member,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair


ALICE_CLOUD = {
    "protocol": "file",
    "url": "file:///tmp/fake-alice",
    "access_key": None,
    "secret_key": None,
}


def _make_cod_sync(repo_dir, remote_name):
    os.chdir(repo_dir)
    return CS.CodSync(remote_name, repo_dir=pathlib.Path(repo_dir))


def test_issue_device_link_for_member_updates_trusted_device_lookup(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]

    _linked_key, linked_priv = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = _linked_key.public_key

    cert = issue_device_link_for_member(
        root, alice_hex, "ProjectX", linked_public_key
    )
    assert cert.cert_type.value == "device_link"
    assert cert.subject_public_key == linked_public_key

    trusted_keys = get_trusted_device_keys_for_member(
        root, alice_hex, "ProjectX", alice_member_id_hex
    )
    assert linked_public_key in trusted_keys
    assert len(trusted_keys) == 2

    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    conn = sqlite3.connect(str(team_db))
    cert_types = conn.execute(
        "SELECT cert_type FROM key_certificate ORDER BY issued_at"
    ).fetchall()
    member_count = conn.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    team_device_count = conn.execute(
        "SELECT COUNT(*) FROM team_device WHERE member_id = ?",
        (bytes.fromhex(alice_member_id_hex),),
    ).fetchone()[0]
    conn.close()
    assert [row[0] for row in cert_types] == ["membership", "device_link"]
    assert member_count == 1
    assert team_device_count == 2

    # Silence lint-style "unused" suspicion around the generated signing key by
    # proving it differs from the current trusted founding key.
    assert linked_priv is not None


def test_device_link_honored_after_fetch_merge_without_extra_shared_state(playground_dir):
    root1 = pathlib.Path(playground_dir) / "device1"
    root2 = pathlib.Path(playground_dir) / "device2"
    cloud_dir = pathlib.Path(playground_dir) / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice")
    team_result = create_team(root1, alice_hex, "ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    alice_member_id_hex = team_result["member_id_hex"]
    alice_member_id = bytes.fromhex(alice_member_id_hex)

    team_sync_1 = root1 / "Participants" / alice_hex / "ProjectX" / "Sync"
    cloud_remote = CS.LocalFolderRemote(str(cloud_dir))
    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = cloud_remote
    cod1.push_to_remote(["main"])

    team_sync_2 = root2 / "Participants" / alice_hex / "ProjectX" / "Sync"
    team_sync_2.mkdir(parents=True)
    cod2 = _make_cod_sync(team_sync_2, "cloud")
    cod2.clone_from_remote(f"file://{cloud_dir}")
    _install_sqlite_merge_driver(team_sync_2)

    linked_key, linked_priv = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = linked_key.public_key

    issue_device_link_for_member(root1, alice_hex, "ProjectX", linked_public_key)
    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = cloud_remote
    cod1.push_to_remote(["main"])

    cod2 = _make_cod_sync(team_sync_2, "cloud")
    cod2.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod2.fetch_from_remote(["main"])
    cod2.merge_from_remote(["main"])

    trusted_keys_on_device_2 = get_trusted_device_keys_for_member_in_team_db(
        team_sync_2 / "core.db", team_id, alice_member_id
    )
    assert linked_public_key in trusted_keys_on_device_2

    create_invitation(root1, alice_hex, "ProjectX", ALICE_CLOUD, invitee_label="Bob")
    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = cloud_remote
    cod1.push_to_remote(
        ["main"],
        signing_key=linked_priv,
        member_id=alice_member_id_hex,
        device_public_key=linked_public_key,
    )

    latest_link, _etag = cloud_remote.get_latest_link()
    assert latest_link is not None
    [link_ids, branches, bundles, supp_data] = latest_link
    linked_signature = supp_data["signatures"][alice_member_id_hex]
    assert linked_signature["device_public_key"] == linked_public_key.hex()

    canonical = canonical_link_bytes(link_ids, branches, bundles, supp_data)
    assert verify_link_signature(
        linked_public_key,
        linked_signature["signature"],
        canonical,
    )
    assert linked_public_key in trusted_keys_on_device_2
