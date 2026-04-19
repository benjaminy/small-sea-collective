import pathlib
import shutil
import sqlite3

import cod_sync.protocol as CS

import small_sea_manager.provisioning as provisioning


def _push_to_localfolder(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    cod = CS.CodSync("origin", repo_dir=repo_dir)
    cod.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod.push_to_remote(["main"])


def _bootstrap_existing_admin_clone(
    root: pathlib.Path,
    *,
    inviter_hex: str,
    invitee_hex: str,
    team_name: str,
    display_name: str,
) -> str:
    team_id, inviter_member_id = provisioning._team_row(root, inviter_hex, team_name)
    invitee_member_id = provisioning.uuid7()

    with provisioning.attached_note_to_self_connection(root, invitee_hex) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, invitee_member_id),
        )
        conn.commit()

    inviter_sync = root / "Participants" / inviter_hex / team_name / "Sync"
    invitee_sync = root / "Participants" / invitee_hex / team_name / "Sync"
    invitee_sync.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(inviter_sync, invitee_sync)

    invitee_keys = provisioning._generate_initial_team_device_key(root, invitee_hex, team_id)
    inviter_private_key, inviter_public_key = provisioning.get_current_team_device_key(
        root, inviter_hex, team_name
    )
    membership_cert = provisioning.issue_membership_cert(
        subject_key=provisioning._participant_key_from_public(invitee_keys["device_key"].public_key),
        issuer_key=provisioning._participant_key_from_public(inviter_public_key),
        issuer_private_key=inviter_private_key,
        team_id=team_id,
        issuer_member_id=inviter_member_id,
        admitted_member_id=invitee_member_id,
    )

    alice_db = inviter_sync / "core.db"
    provisioning.ensure_team_db_schema(alice_db)
    engine = provisioning._sqlite_engine(alice_db)
    try:
        with engine.begin() as conn:
            provisioning._upsert_member_row(conn, invitee_member_id, display_name=display_name)
            provisioning._upsert_team_device_row(
                conn,
                invitee_member_id,
                invitee_keys["device_key"].public_key,
            )
            provisioning._store_team_certificate(conn, membership_cert, issuer_member_id=inviter_member_id)
            berth_id = conn.execute(
                provisioning.text("SELECT id FROM team_app_berth LIMIT 1")
            ).fetchone()[0]
            conn.execute(
                provisioning.text(
                    "INSERT INTO berth_role (id, member_id, berth_id, role) VALUES (:id, :member_id, :berth_id, :role)"
                ),
                {
                    "id": provisioning.uuid7(),
                    "member_id": invitee_member_id,
                    "berth_id": berth_id,
                    "role": "read-write",
                },
            )
    finally:
        engine.dispose()
    shutil.copy2(alice_db, invitee_sync / "core.db")
    return invitee_member_id.hex()


def test_quorum_two_requires_second_admin_and_inviter_finalization(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_cloud = root / "alice-cloud"
    alice_cloud.mkdir()

    alice_hex = provisioning.create_new_participant(root, "Alice")
    bob_hex = provisioning.create_new_participant(root, "Bob")
    carol_hex = provisioning.create_new_participant(root, "Carol")
    provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    provisioning.create_team(root, alice_hex, "ProjectX")
    provisioning.set_team_admission_policy(root, alice_hex, "ProjectX", quorum=2)

    alice_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_sync, alice_cloud)

    _bootstrap_existing_admin_clone(
        root,
        inviter_hex=alice_hex,
        invitee_hex=carol_hex,
        team_name="ProjectX",
        display_name="Carol",
    )
    carol_sync = root / "Participants" / carol_hex / "ProjectX" / "Sync"
    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")

    token = provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _push_to_localfolder(alice_sync, alice_cloud)
    acceptance = provisioning.accept_invitation(
        root,
        bob_hex,
        token,
        inviter_remote=CS.LocalFolderRemote(str(alice_cloud)),
    )

    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    assert proposals[0]["status"] == "awaiting_quorum"

    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")
    provisioning.sign_admin_approval(root, carol_hex, "ProjectX", proposals[0]["id"])
    shutil.copy2(carol_sync / "core.db", alice_sync / "core.db")

    provisioning.finalize_admission(root, alice_hex, "ProjectX", proposals[0]["id"])
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    assert proposals[0]["status"] == "finalized"

    with sqlite3.connect(alice_sync / "core.db") as conn:
        member_count = conn.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    assert member_count == 3


def test_governance_drift_invalidates_proposal(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_cloud = root / "alice-cloud"
    alice_cloud.mkdir()

    alice_hex = provisioning.create_new_participant(root, "Alice")
    bob_hex = provisioning.create_new_participant(root, "Bob")
    provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    provisioning.create_team(root, alice_hex, "ProjectX")
    alice_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_sync, alice_cloud)

    token = provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    acceptance = provisioning.accept_invitation(
        root,
        bob_hex,
        token,
        inviter_remote=CS.LocalFolderRemote(str(alice_cloud)),
    )

    with sqlite3.connect(alice_sync / "core.db") as conn:
        conn.execute(
            "INSERT INTO member (id, display_name) VALUES (?, ?)",
            (provisioning.uuid7(), "Unexpected"),
        )
        conn.commit()

    try:
        provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    except ValueError as exc:
        assert "governance drift" in str(exc)
    else:
        raise AssertionError("expected governance drift to invalidate the proposal")

    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    assert proposals[0]["status"] == "invalidated"


def test_observer_role_finalizes_as_read_only(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_cloud = root / "alice-cloud"
    alice_cloud.mkdir()

    alice_hex = provisioning.create_new_participant(root, "Alice")
    bob_hex = provisioning.create_new_participant(root, "Bob")
    provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    provisioning.create_team(root, alice_hex, "ProjectX")
    alice_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_sync, alice_cloud)

    token = provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
        role="observer",
    )
    acceptance = provisioning.accept_invitation(
        root,
        bob_hex,
        token,
        inviter_remote=CS.LocalFolderRemote(str(alice_cloud)),
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)

    with sqlite3.connect(alice_sync / "core.db") as conn:
        bob_role = conn.execute(
            "SELECT role FROM berth_role br JOIN member m ON m.id = br.member_id WHERE m.display_name = 'Bob'"
        ).fetchone()[0]
    assert bob_role == "read-only"
