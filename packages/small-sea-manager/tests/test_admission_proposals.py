import pathlib
import shutil
import sqlite3

import cod_sync.protocol as CS
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore

import small_sea_manager.provisioning as provisioning


def _push_to_localfolder(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    cod = CS.CodSync(Repo(repo_dir / ".git", repo_dir), LocalFolderStore(str(cloud_dir)))
    cod.publish()


def _bootstrap_existing_steward_clone(
    root: pathlib.Path,
    *,
    inviter_hex: str,
    invitee_hex: str,
    team_name: str,
    display_name: str,
) -> str:
    team_id, inviter_teammate_id = provisioning._team_row(root, inviter_hex, team_name)
    invitee_teammate_id = provisioning.uuid7()

    with provisioning.attached_note_to_self_connection(root, invitee_hex) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (team_id, team_name, invitee_teammate_id),
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
        issuer_teammate_id=inviter_teammate_id,
        admitted_teammate_id=invitee_teammate_id,
    )

    alice_db = inviter_sync / "core.db"
    provisioning.ensure_team_db_schema(alice_db)
    engine = provisioning._sqlite_engine(alice_db)
    try:
        with engine.begin() as conn:
            provisioning._upsert_teammate_row(conn, invitee_teammate_id, display_name=display_name)
            provisioning._upsert_team_device_row(
                conn,
                invitee_teammate_id,
                invitee_keys["device_key"].public_key,
            )
            provisioning._store_team_certificate(conn, membership_cert, issuer_teammate_id=inviter_teammate_id)
            berth_id = conn.execute(
                provisioning.text("SELECT id FROM team_app_berth LIMIT 1")
            ).fetchone()[0]
            conn.execute(
                provisioning.text(
                    "INSERT INTO berth_role (id, teammate_id, berth_id, role) VALUES (:id, :teammate_id, :berth_id, :role)"
                ),
                {
                    "id": provisioning.uuid7(),
                    "teammate_id": invitee_teammate_id,
                    "berth_id": berth_id,
                    "role": "read-write",
                },
            )
    finally:
        engine.dispose()
    shutil.copy2(alice_db, invitee_sync / "core.db")
    return invitee_teammate_id.hex()


def test_quorum_two_requires_second_steward_and_inviter_finalization(playground_dir):
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

    _bootstrap_existing_steward_clone(
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
        inviter_store=LocalFolderStore(str(alice_cloud)),
    )

    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    assert proposals[0]["status"] == "awaiting_quorum"

    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")
    provisioning.endorse_admission(root, carol_hex, "ProjectX", proposals[0]["id"])
    shutil.copy2(carol_sync / "core.db", alice_sync / "core.db")

    provisioning.finalize_admission(root, alice_hex, "ProjectX", proposals[0]["id"])
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    assert proposals[0]["status"] == "finalized"

    with sqlite3.connect(alice_sync / "core.db") as conn:
        teammate_count = conn.execute("SELECT COUNT(*) FROM teammate").fetchone()[0]
    assert teammate_count == 3


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
        inviter_store=LocalFolderStore(str(alice_cloud)),
    )

    with sqlite3.connect(alice_sync / "core.db") as conn:
        conn.execute(
            "INSERT INTO teammate (id, display_name) VALUES (?, ?)",
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


def test_contributor_role_finalizes_as_proposal_only_on_core(playground_dir):
    # A contributor is proposal-only on Core (architecture.md: proposal-only on
    # Core, automatic on other berths). Finalization now expands the preset into
    # per-berth integration_mode_change records (issue #164); the Core berth
    # gets `core_mode` (proposal-only -> `read-only` projection). A fixture with
    # a non-Core berth is exercised in test_admission_records.py.
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
        mode_plan=provisioning.mode_plan_for_preset("contributor"),
    )
    acceptance = provisioning.accept_invitation(
        root,
        bob_hex,
        token,
        inviter_store=LocalFolderStore(str(alice_cloud)),
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)

    with sqlite3.connect(alice_sync / "core.db") as conn:
        bob_core_role = conn.execute(
            "SELECT br.role FROM berth_role br "
            "JOIN teammate m ON m.id = br.teammate_id "
            "JOIN team_app_berth tab ON tab.id = br.berth_id "
            "JOIN app a ON a.id = tab.app_id "
            "WHERE m.display_name = 'Bob' AND a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()[0]
    # `read-only` is the projection's current stand-in for `proposal-only`.
    assert bob_core_role == "read-only"
