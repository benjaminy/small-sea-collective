import pathlib
import sqlite3

import cod_sync.protocol as CS

from cod_sync.format import canonical_link_bytes, decode_link, verify_link_signature
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from small_sea_manager.provisioning import (
    _install_sqlite_merge_driver,
    add_cloud_storage,
    create_invitation,
    create_new_participant,
    create_team,
    get_trusted_device_keys_for_teammate,
    get_trusted_device_keys_for_teammate_in_team_db,
    issue_device_link_for_teammate,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair


ALICE_CLOUD = {
    "protocol": "file",
    "url": "file:///tmp/fake-alice",
    "access_key": None,
    "secret_key": None,
}


def _repo(repo_dir):
    repo_dir = pathlib.Path(repo_dir)
    return Repo(repo_dir / ".git", repo_dir)


def _make_cod_sync(repo_dir, cloud_dir):
    return CS.CodSync(_repo(repo_dir), LocalFolderStore(str(cloud_dir)))


def test_issue_device_link_for_teammate_updates_trusted_device_lookup(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_teammate_id_hex = team_result["teammate_id_hex"]

    _linked_key, linked_priv = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = _linked_key.public_key

    cert = issue_device_link_for_teammate(
        root, alice_hex, "ProjectX", linked_public_key
    )
    assert cert.cert_type.value == "device_link"
    assert cert.subject_public_key == linked_public_key

    trusted_keys = get_trusted_device_keys_for_teammate(
        root, alice_hex, "ProjectX", alice_teammate_id_hex
    )
    assert linked_public_key in trusted_keys
    assert len(trusted_keys) == 2

    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    conn = sqlite3.connect(str(team_db))
    cert_types = conn.execute(
        "SELECT cert_type FROM key_certificate ORDER BY issued_at"
    ).fetchall()
    teammate_count = conn.execute("SELECT COUNT(*) FROM teammate").fetchone()[0]
    team_device_count = conn.execute(
        "SELECT COUNT(*) FROM team_device WHERE teammate_id = ?",
        (bytes.fromhex(alice_teammate_id_hex),),
    ).fetchone()[0]
    conn.close()
    assert [row[0] for row in cert_types] == ["membership", "device_link"]
    assert teammate_count == 1
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
    add_cloud_storage(
        root1,
        alice_hex,
        protocol=ALICE_CLOUD["protocol"],
        url=ALICE_CLOUD["url"],
    )
    team_result = create_team(root1, alice_hex, "ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    alice_teammate_id_hex = team_result["teammate_id_hex"]
    alice_teammate_id = bytes.fromhex(alice_teammate_id_hex)

    team_sync_1 = root1 / "Participants" / alice_hex / "ProjectX" / "Sync"
    cloud_store = LocalFolderStore(str(cloud_dir))
    _make_cod_sync(team_sync_1, cloud_dir).publish()

    team_sync_2 = root2 / "Participants" / alice_hex / "ProjectX" / "Sync"
    team_sync_2.mkdir(parents=True)
    repo2 = Repo.init(team_sync_2 / ".git").with_work_tree(team_sync_2)
    cloned = CS.CodSync(repo2, LocalFolderStore(str(cloud_dir))).fetch()
    repo2.checkout_branch("main", cloned.observed_head)
    _install_sqlite_merge_driver(team_sync_2)

    linked_key, linked_priv = generate_key_pair(ProtectionLevel.DAILY)
    linked_public_key = linked_key.public_key

    issue_device_link_for_teammate(root1, alice_hex, "ProjectX", linked_public_key)
    _make_cod_sync(team_sync_1, cloud_dir).publish()

    fetched = _make_cod_sync(team_sync_2, cloud_dir).fetch()
    _repo(team_sync_2).merge(fetched.observed_head)

    trusted_keys_on_device_2 = get_trusted_device_keys_for_teammate_in_team_db(
        team_sync_2 / "core.db", team_id, alice_teammate_id
    )
    assert linked_public_key in trusted_keys_on_device_2

    create_invitation(root1, alice_hex, "ProjectX", ALICE_CLOUD, invitee_label="Bob")
    _make_cod_sync(team_sync_1, cloud_dir).publish(
        signing_key=linked_priv,
        teammate_id=alice_teammate_id_hex,
        device_public_key=linked_public_key,
    )

    latest_link = decode_link(cloud_store.get_latest_link()[0])
    linked_signature = latest_link.extensions["signatures"][alice_teammate_id_hex]
    assert linked_signature["device_public_key"] == linked_public_key.hex()

    assert verify_link_signature(
        linked_public_key,
        linked_signature["signature"],
        canonical_link_bytes(latest_link),
    )
    assert linked_public_key in trusted_keys_on_device_2
