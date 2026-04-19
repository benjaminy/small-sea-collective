"""Integration test: two devices concurrently create invitations, merge cleanly.

Uses LocalFolderRemote (file://) — no MinIO or hub needed.
"""

import os
import pathlib
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone

import cod_sync.protocol as CS
import small_sea_manager.provisioning as provisioning
from small_sea_manager.provisioning import (
    _install_sqlite_merge_driver,
    create_invitation,
    create_new_participant,
    create_team,
)

ALICE_CLOUD = {
    "protocol": "file",
    "url": "file:///tmp/fake-alice",
    "access_key": None,
    "secret_key": None,
}


def _make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory."""
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    return cod


def test_concurrent_invitations_merge(playground_dir):
    root1 = pathlib.Path(playground_dir) / "device1"
    root2 = pathlib.Path(playground_dir) / "device2"
    cloud_dir = pathlib.Path(playground_dir) / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    # 1. Create participant Alice on device 1
    alice_hex = create_new_participant(root1, "Alice")

    # 2. Create team on device 1
    team_result = create_team(root1, alice_hex, "ProjectX")

    team_sync_1 = root1 / "Participants" / alice_hex / "ProjectX" / "Sync"

    # 3. Push team repo to cloud via cod-sync
    cloud_remote = CS.LocalFolderRemote(str(cloud_dir))
    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = cloud_remote
    cod1.push_to_remote(["main"])

    # 4. Clone from cloud into device 2's team directory
    #    Device 2 needs the same path structure: Participants/<alice_hex>/ProjectX/Sync/
    team_sync_2 = root2 / "Participants" / alice_hex / "ProjectX" / "Sync"
    team_sync_2.mkdir(parents=True)

    cod2 = _make_cod_sync(team_sync_2, "cloud")
    cod2.clone_from_remote(f"file://{cloud_dir}")

    # Install merge driver on device 2 (git config is local-only)
    _install_sqlite_merge_driver(team_sync_2)

    # 5. Device 1: create invitation for Bob, push to cloud
    token_bob = create_invitation(
        root1, alice_hex, "ProjectX", ALICE_CLOUD, invitee_label="Bob"
    )

    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = cloud_remote
    cod1.push_to_remote(["main"])

    # 6. Device 2: create invitation for Carol (commit locally only)
    #    We need to insert directly into device 2's DB since create_invitation
    #    uses provisioning paths relative to root_dir
    _create_invitation_on_device(team_sync_2, "Carol")

    # 7. Device 2: fetch + merge from cloud
    #    This should trigger the splice-sqlite-merge driver
    cod2 = _make_cod_sync(team_sync_2, "cloud")
    cod2.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod2.fetch_from_remote(["main"])
    cod2.merge_from_remote(["main"])

    # 8. Assert: device 2's core.db has BOTH Bob and Carol admission proposals
    conn2 = sqlite3.connect(str(team_sync_2 / "core.db"))
    invitations_2 = conn2.execute(
        "SELECT invitee_label FROM admission_proposal ORDER BY invitee_label"
    ).fetchall()
    conn2.close()
    labels_2 = {row[0] for row in invitations_2}
    assert "Bob" in labels_2, f"Missing Bob in device 2. Got: {labels_2}"
    assert "Carol" in labels_2, f"Missing Carol in device 2. Got: {labels_2}"

    # 9. Push device 2 to cloud, fetch+merge on device 1
    cod2 = _make_cod_sync(team_sync_2, "cloud")
    cod2.remote = cloud_remote
    cod2.push_to_remote(["main"])

    cod1 = _make_cod_sync(team_sync_1, "cloud")
    cod1.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod1.add_remote(f"file://{cloud_dir}", [])
    cod1.fetch_from_remote(["main"])
    cod1.merge_from_remote(["main"])

    # 10. Assert: device 1 also has both proposals
    conn1 = sqlite3.connect(str(team_sync_1 / "core.db"))
    invitations_1 = conn1.execute(
        "SELECT invitee_label FROM admission_proposal ORDER BY invitee_label"
    ).fetchall()
    conn1.close()
    labels_1 = {row[0] for row in invitations_1}
    assert "Bob" in labels_1, f"Missing Bob in device 1. Got: {labels_1}"
    assert "Carol" in labels_1, f"Missing Carol in device 1. Got: {labels_1}"


def _create_invitation_on_device(team_sync_dir, invitee_label):
    """Insert an admission proposal directly into the team DB and commit."""
    import secrets

    db_path = pathlib.Path(team_sync_dir) / "core.db"
    head_commit = subprocess.run(
        ["git", "-C", str(team_sync_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    member_ids = [row[0].hex() for row in conn.execute("SELECT id FROM member ORDER BY id").fetchall()]
    admin_ids = [
        row[0].hex()
        for row in conn.execute(
            """
            SELECT br.member_id
            FROM berth_role br
            JOIN team_app_berth tab ON tab.id = br.berth_id
            JOIN app a ON a.id = tab.app_id
            WHERE a.name = 'SmallSeaCollectiveCore' AND br.role = 'read-write'
            ORDER BY br.member_id
            """
        ).fetchall()
    ]
    member_devices: dict[str, list[str]] = {member_id_hex: [] for member_id_hex in member_ids}
    for member_id, device_key_id in conn.execute(
        "SELECT member_id, device_key_id FROM team_device ORDER BY member_id, device_key_id"
    ).fetchall():
        member_devices.setdefault(member_id.hex(), []).append(device_key_id.hex())
    governance_snapshot = {
        "admins": admin_ids,
        "members": member_ids,
        "member_devices": member_devices,
    }
    governance_digest = provisioning._governance_digest(governance_snapshot)
    # The team DB is scoped to one team and does not persist a team_id column.
    # For this merge test we only need a stable non-null value to exercise
    # concurrent append-only proposal rows through the sqlite merge driver.
    pseudo_team_id = conn.execute("SELECT id FROM app LIMIT 1").fetchone()["id"]
    team_row = conn.execute("SELECT id FROM member ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        "INSERT INTO admission_proposal ("
        "proposal_id, nonce, team_id, inviter_member_id, invitee_member_id, "
        "invitee_label, role, anchor_commit, governance_digest, governance_snapshot_json, "
        "state, created_at, expires_at"
        ") VALUES (?, ?, ?, ?, ?, ?, 'admin', ?, ?, ?, 'awaiting_invitee', ?, ?)",
        (
            provisioning.uuid7(),
            secrets.token_bytes(16),
            pseudo_team_id,
            team_row["id"],
            provisioning.uuid7(),
            invitee_label,
            head_commit,
            governance_digest,
            provisioning._json_dumps_sorted(governance_snapshot),
            datetime.now(timezone.utc).isoformat(),
            provisioning._proposal_expiry(datetime.now(timezone.utc), 7 * 24 * 60 * 60),
        ),
    )
    conn.commit()
    conn.close()

    CS.gitCmd(["-C", str(team_sync_dir), "add", "core.db"])
    CS.gitCmd(
        [
            "-C",
            str(team_sync_dir),
            "commit",
            "-m",
            f"Created invitation for {invitee_label}",
        ]
    )
