import base64
import json
import pathlib
import shutil
import sqlite3
import subprocess
from dataclasses import replace

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from small_sea_note_to_self.db import device_local_db_path
from cod_sync.protocol import CodSync
from cod_sync.store import LocalFolderStore, SmallSeaStore
from cod_sync.repo import Repo
from cryptography.exceptions import InvalidSignature
from cuttlefish.group import (
    create_sender_key,
    group_encrypt,
    process_sender_key_distribution,
)
from fastapi.testclient import TestClient
from small_sea_hub.crypto import serialize_group_message
from small_sea_hub.server import app
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import (
    complete_invitation_acceptance,
    create_invitation,
    create_new_participant,
    create_team,
    get_current_team_device_key,
    list_invitations,
)
from test_support import (
    accept_and_export,
    acceptance_record_from_courier,
    publish_storage_announcement_for_session,
    route_sidecar_from_courier,
)
from wrasse_trust.identity import verify_membership_cert
from wrasse_trust.identity import issue_membership_cert
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    verify_teammate_berth_storage_announcement_signature,
)


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]  # auto-approved
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _push_via_hub(http, session_hex, repo_dir, **push_kwargs):
    """Push a team repo to cloud via Hub using SmallSeaStore."""
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text
    publish_storage_announcement_for_session(app.state.backend, session_hex)
    remote = SmallSeaStore(session_hex, base_url="http://testserver", client=http)
    repo_path = pathlib.Path(repo_dir)
    cs = CodSync(Repo(repo_path / ".git", repo_path), remote)
    cs.publish(**push_kwargs)


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            }],
        }),
    )


def _core_allocation(root, participant_hex, team_result):
    allocation = provisioning.get_berth_cloud_allocation_for_berth(
        root, participant_hex, team_result["berth_id_hex"]
    )
    assert allocation is not None
    return allocation


def _localfolder_invitation(root, monkeypatch=None, forge_core=False):
    cloud = pathlib.Path(root) / "alice-cloud"
    cloud.mkdir()
    alice = create_new_participant(root, "Alice")
    bob = create_new_participant(root, "Bob")
    provisioning.add_cloud_storage(root, alice, protocol="localfolder", url=str(cloud))
    provisioning.create_team(root, alice, "ProjectX")
    alice_sync = pathlib.Path(root) / "Participants" / alice / "ProjectX" / "Sync"
    CodSync(Repo(alice_sync / ".git", alice_sync), LocalFolderStore(str(cloud))).publish()
    token = create_invitation(
        root, alice, "ProjectX", {"protocol": "localfolder", "url": str(cloud)}
    )
    CodSync(Repo(alice_sync / ".git", alice_sync), LocalFolderStore(str(cloud))).publish()
    if monkeypatch is not None and forge_core:
        original = provisioning._load_team_certificates

        def forged_certificates(conn, team_id):
            if pathlib.Path(conn.engine.url.database).parent.name == "Sync":
                inviter_id = provisioning._team_row(root, alice, "ProjectX")[1]
                inviter_public = conn.execute(
                    provisioning.text("SELECT public_key FROM team_device WHERE teammate_id = :id"),
                    {"id": inviter_id},
                ).fetchone()[0]
                attacker, attacker_private = generate_key_pair(ProtectionLevel.DAILY)
                attacker_id = provisioning.uuid7()
                genesis = issue_membership_cert(
                    attacker, attacker, attacker_private, team_id, attacker_id, attacker_id
                )
                inviter_key = provisioning._participant_key_from_public(inviter_public)
                forged_membership = issue_membership_cert(
                    inviter_key, attacker, attacker_private, team_id, attacker_id, inviter_id
                )
                conn.execute(provisioning.text("DELETE FROM key_certificate"))
                provisioning._upsert_teammate_row(conn, attacker_id, display_name="Mallory")
                provisioning._store_team_certificate(conn, genesis, attacker_id)
                provisioning._store_team_certificate(conn, forged_membership, attacker_id)
            return original(conn, team_id)

        monkeypatch.setattr(provisioning, "_load_team_certificates", forged_certificates)
    return alice, bob, token, LocalFolderStore(str(cloud))


def test_create_invitation(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    provisioning.add_cloud_storage(root, alice_hex, **alice_cloud)
    create_team(root, alice_hex, "ProjectX")
    token = create_invitation(
        root, alice_hex, "ProjectX", alice_cloud, invitee_label="Bob"
    )
    assert isinstance(token, str)
    assert len(token) > 0

    # Verify invitation row exists
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "awaiting_invitee"
    assert invitations[0]["invitee_label"] == "Bob"


def test_create_invitation_includes_bucket(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")
    alice_cloud = {
        "protocol": "s3",
        "url": "http://localhost:9000",
        "access_key": "alice-key",
        "secret_key": "alice-secret",
    }
    provisioning.add_cloud_storage(root, alice_hex, **alice_cloud)
    create_team(root, alice_hex, "ProjectX")
    token_b64 = create_invitation(root, alice_hex, "ProjectX", alice_cloud)
    token_json = base64.b64decode(token_b64).decode()
    token = json.loads(token_json)

    assert "inviter_bucket" in token
    assert token["inviter_bucket"].startswith("ss-")
    assert len(token["inviter_bucket"]) == 3 + 32  # "ss-" + UUIDv7 hex
    assert len(token["team_id"]) == 32
    assert token["inviter_sender_key"]["group_id"] == token["team_id"]
    _alice_team_private_key, alice_team_public_key = get_current_team_device_key(
        root, alice_hex, "ProjectX"
    )
    assert token["inviter_sender_key"]["sender_device_key_id"] == key_id_from_public(
        alice_team_public_key
    ).hex()


def test_invitation_rejects_token_with_invalid_anchor_signature(playground_dir):
    root = pathlib.Path(playground_dir)
    _alice, bob, token, inviter_store = _localfolder_invitation(root)
    token_data = json.loads(base64.b64decode(token))
    signature = bytearray.fromhex(token_data["authority_anchor_signature"])
    signature[0] ^= 1
    token_data["authority_anchor_signature"] = signature.hex()
    tampered_token = base64.b64encode(
        json.dumps(token_data, sort_keys=True, separators=(",", ":")).encode()
    ).decode()

    with pytest.raises(ValueError, match="anchor signature is invalid"):
        provisioning.accept_invitation(root, bob, tampered_token, inviter_store)


def test_invitation_rejects_core_with_forged_genesis(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    _alice, bob, token, inviter_store = _localfolder_invitation(
        root, monkeypatch=monkeypatch, forge_core=True
    )
    team_id = bytes.fromhex(json.loads(base64.b64decode(token))["team_id"])

    with pytest.raises(ValueError, match="does not match inviter signature"):
        provisioning.accept_invitation(root, bob, token, inviter_store)

    assert provisioning.get_authority_anchor(root, bob, team_id) is None


def _run_full_invitation_flow(playground_dir, minio_server_gen, *, link_invitee_device=False):
    """Full invitation flow routed through the Hub."""
    alice_minio = minio_server_gen()
    bob_minio = minio_server_gen()

    root = pathlib.Path(playground_dir)

    # -- Shared Hub --
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # -- Provision participants --
    alice_hex = create_new_participant(root, "Alice")
    bob_hex = create_new_participant(root, "Bob")

    # -- Register cloud storage via Manager --
    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="s3",
        url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    provisioning.add_cloud_storage(
        root,
        bob_hex,
        protocol="s3",
        url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    # -- Alice: create team and push via Hub --
    team_result = create_team(root, alice_hex, "ProjectX")
    alice_teammate_id_hex = team_result["teammate_id_hex"]
    team_bucket = _core_allocation(root, alice_hex, team_result)["location"]

    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # Make Alice's bucket publicly readable (anonymous clone via /cloud_proxy)
    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    # -- Alice: create invitation and re-push --
    token = create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    token_data = json.loads(base64.b64decode(token).decode())
    assert "inviter_bucket" in token_data
    assert "access_key" not in token_data.get("inviter_cloud", {})
    assert "secret_key" not in token_data.get("inviter_cloud", {})

    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Bob: accept via Manager --
    # Route preparation materializes Bob's Core bucket through his Hub and
    # applies the public-read policy itself (SmallSeaS3Adapter.materialize), so
    # no _make_bucket_public call for Bob and no _push_via_hub helper runs here.
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = accept_and_export(bob_manager, token)

    assert isinstance(acceptance_b64, str)
    alice_anchor = provisioning.get_authority_anchor(
        root, alice_hex, bytes.fromhex(token_data["team_id"])
    )
    bob_anchor = provisioning.get_authority_anchor(
        root, bob_hex, bytes.fromhex(token_data["team_id"])
    )
    assert bob_anchor["anchor_public_key"] == alice_anchor["anchor_public_key"]
    assert bob_anchor["adopted_via"] == "invitation-acceptance"
    assert bob_anchor["enrollment_completed_at"] is not None
    assert provisioning.load_transitional_authority_view(
        root, bob_hex, "ProjectX"
    ).identifier == provisioning.load_transitional_authority_view(
        root, alice_hex, "ProjectX"
    ).identifier

    acceptance = acceptance_record_from_courier(acceptance_b64)
    bob_teammate_id_hex = acceptance["author_teammate_id"]
    assert bob_teammate_id_hex != bob_hex
    assert len(bob_teammate_id_hex) == 32
    assert acceptance["team_id"] == token_data["team_id"]
    assert len(acceptance["invitee_device_public_key"]) == 64
    assert "acceptor_sender_key" not in acceptance
    assert "acceptor_cloud" not in acceptance
    assert "acceptor_bucket" not in acceptance

    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(bob_team_db)) as conn:
        bob_teammate_rows = conn.execute(
            "SELECT id FROM teammate WHERE id = ?",
            (bytes.fromhex(bob_teammate_id_hex),),
        ).fetchall()
        bob_ann_rows = conn.execute(
            "SELECT announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature "
            "FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
            (bytes.fromhex(bob_teammate_id_hex),),
        ).fetchall()
    assert bob_teammate_rows == []

    # Manager route preparation published Bob's own berth storage announcement
    # after Hub materialization, signed with Bob's freshly generated team
    # device key, and attached the stored row to the courier token.
    assert len(bob_ann_rows) == 1
    bob_ann = TeammateBerthStorageAnnouncement(*bob_ann_rows[0])
    bob_device_public_key = bytes.fromhex(acceptance["invitee_device_public_key"])
    assert bob_ann.signer_key_id == key_id_from_public(bob_device_public_key)
    assert verify_teammate_berth_storage_announcement_signature(
        bob_ann, bob_device_public_key
    )
    bob_allocation = provisioning.get_berth_cloud_allocation_for_berth(
        root, bob_hex, bob_ann.berth_id.hex()
    )
    assert bob_allocation is not None
    assert bob_ann.protocol == bob_allocation["protocol"]
    assert bob_ann.url == bob_allocation["url"]
    assert bob_ann.location == bob_allocation["location"]

    # The sidecar is the stored row, field for field -- never re-signed.
    sidecar = route_sidecar_from_courier(acceptance_b64)
    assert sidecar == provisioning.serialize_berth_storage_announcement(bob_ann)

    # -- Alice: complete the acceptance --
    completion = complete_invitation_acceptance(
        root, alice_hex, "ProjectX", acceptance_b64
    )
    assert completion == {
        "route_delivery": "imported",
        "route_reason": None,
        "admission": "finalized",
    }

    # Alice's inserted row is byte-identical to Bob's.
    aconn = sqlite3.connect(str(root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"))
    alice_bob_ann_rows = aconn.execute(
        "SELECT announcement_id, teammate_id, berth_id, protocol, url, location, "
        "announced_at, signer_key_id, signature "
        "FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
        (bytes.fromhex(bob_teammate_id_hex),),
    ).fetchall()
    aconn.close()
    assert alice_bob_ann_rows == bob_ann_rows

    # --- Verify Alice's invitation is accepted ---
    invitations = list_invitations(root, alice_hex, "ProjectX")
    assert len(invitations) == 1
    assert invitations[0]["status"] == "finalized"

    # --- Verify Alice's team DB has 2 teammates, a device row for Bob, and 2 berth_roles ---
    alice_team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    aconn = sqlite3.connect(str(alice_team_db))
    teammates = aconn.execute(
        "SELECT id, display_name FROM teammate ORDER BY id"
    ).fetchall()
    assert len(teammates) == 2
    teammate_ids = {row[0].hex() for row in teammates}
    assert alice_teammate_id_hex in teammate_ids
    assert bob_teammate_id_hex in teammate_ids
    bob_teammate_row = next(row for row in teammates if row[0] == bytes.fromhex(bob_teammate_id_hex))
    assert bob_teammate_row[1] == "Bob"
    team_devices = aconn.execute(
        "SELECT teammate_id, device_key_id, public_key "
        "FROM team_device ORDER BY teammate_id, device_key_id"
    ).fetchall()
    assert len(team_devices) == 2
    bob_team_device_row = next(
        row for row in team_devices if row[0] == bytes.fromhex(bob_teammate_id_hex)
    )
    assert bob_team_device_row[1] == bytes.fromhex(acceptance["author_device_key_id"])
    assert bob_team_device_row[2] == bytes.fromhex(acceptance["invitee_device_public_key"])

    bob_cert_row = aconn.execute(
        "SELECT cert_id, cert_type, subject_key_id, subject_public_key, issuer_key_id, "
        "issuer_teammate_id, issued_at, claims, signature "
        "FROM key_certificate WHERE subject_public_key = ?",
        (bytes.fromhex(acceptance["invitee_device_public_key"]),),
    ).fetchone()
    assert bob_cert_row is not None
    assert bob_cert_row[1] == "membership"
    assert bob_cert_row[3] == bytes.fromhex(acceptance["invitee_device_public_key"])
    assert bob_cert_row[5] == bytes.fromhex(alice_teammate_id_hex)
    assert json.loads(bob_cert_row[7])["teammate_id"] == bob_teammate_id_hex
    bob_membership_cert = provisioning._deserialize_cert(
        {
            "cert_id": bob_cert_row[0].hex(),
            "cert_type": bob_cert_row[1],
            "team_id": token_data["team_id"],
            "subject_key_id": bob_cert_row[2].hex(),
            "subject_public_key": bob_cert_row[3].hex(),
            "issuer_key_id": bob_cert_row[4].hex(),
            "issuer_participant_id": bob_cert_row[5].hex(),
            "issued_at_iso": bob_cert_row[6],
            "claims": json.loads(bob_cert_row[7]),
            "signature": bob_cert_row[8].hex(),
        }
    )
    alice_device_public_key = next(
        row[2] for row in team_devices if row[0] == bytes.fromhex(alice_teammate_id_hex)
    )
    assert verify_membership_cert(
        bob_membership_cert,
        issuer_public_key=alice_device_public_key,
        team_id=bytes.fromhex(token_data["team_id"]),
        issuer_teammate_id=bytes.fromhex(alice_teammate_id_hex),
        admitted_teammate_id=bytes.fromhex(bob_teammate_id_hex),
        subject_public_key=bytes.fromhex(acceptance["invitee_device_public_key"]),
    )

    roles = aconn.execute("SELECT teammate_id, role FROM berth_role").fetchall()
    assert len(roles) == 2
    role_map = {row[0].hex(): row[1] for row in roles}
    assert role_map[alice_teammate_id_hex] == "read-write"
    assert role_map[bob_teammate_id_hex] == "read-write"
    aconn.close()

    # --- Verify Bob's local clone still reflects the pre-finalization team view until sync ---
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    bconn = sqlite3.connect(str(bob_team_db))
    teammates = bconn.execute(
        "SELECT id, display_name FROM teammate ORDER BY id"
    ).fetchall()
    assert len(teammates) == 1
    teammate_ids = {row[0].hex() for row in teammates}
    assert alice_teammate_id_hex in teammate_ids
    team_devices = bconn.execute(
        "SELECT teammate_id, public_key FROM team_device ORDER BY teammate_id, device_key_id"
    ).fetchall()
    assert len(team_devices) == 1
    # team_device no longer carries transport. Alice's storage is discoverable
    # through her signed berth storage announcement (published by create_team),
    # which Bob received when he cloned the team repo.
    alice_storage = bconn.execute(
        "SELECT protocol, url FROM teammate_berth_storage_announcement "
        "WHERE teammate_id = ? ORDER BY announcement_id DESC LIMIT 1",
        (bytes.fromhex(alice_teammate_id_hex),),
    ).fetchone()
    assert alice_storage is not None
    assert alice_storage[0] == "s3"
    assert alice_storage[1] == alice_minio["endpoint"]
    bconn.close()

    # --- Verify Bob's NoteToSelf has the team pointer but NOT a TeamAppBerth for ProjectX ---
    bob_user_db = root / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    buconn = sqlite3.connect(str(bob_user_db))
    buconn.row_factory = sqlite3.Row
    bob_local_db = device_local_db_path(root, bob_hex)
    bulconn = sqlite3.connect(str(bob_local_db))
    teams = buconn.execute("SELECT * FROM team WHERE name = 'ProjectX'").fetchall()
    assert len(teams) == 1
    assert teams[0]["id"] == bytes.fromhex(token_data["team_id"])
    assert teams[0]["self_in_team"] == bytes.fromhex(bob_teammate_id_hex)
    alice_sender_device_key_id = key_id_from_public(alice_device_public_key)
    bob_sender_device_key_id = key_id_from_public(
        bytes.fromhex(acceptance["invitee_device_public_key"])
    )

    with pytest.raises(sqlite3.OperationalError):
        buconn.execute(
            "SELECT sender_device_key_id, signing_private_key "
            "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
            (bytes.fromhex(token_data["team_id"]), alice_sender_device_key_id),
        ).fetchone()

    alice_peer_sender_key = bulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
        (bytes.fromhex(token_data["team_id"]), alice_sender_device_key_id),
    ).fetchone()
    assert alice_peer_sender_key is not None
    assert alice_peer_sender_key[0] == alice_sender_device_key_id
    assert alice_peer_sender_key[1] is None

    bob_team_sender_key = bulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM team_sender_key WHERE team_id = ?",
        (bytes.fromhex(token_data["team_id"]),),
    ).fetchone()
    assert bob_team_sender_key is not None
    assert bob_team_sender_key[0] == bob_sender_device_key_id
    assert bob_team_sender_key[1] is not None

    other_berths = buconn.execute(
        "SELECT tab.* FROM team_app_berth tab "
        "JOIN team t ON tab.team_id = t.id "
        "WHERE t.name = 'ProjectX'"
    ).fetchall()
    assert len(other_berths) == 0
    buconn.close()
    bulconn.close()

    bob_team_token = _open_session(http, "Bob", "ProjectX", mode="passthrough")
    assert isinstance(bob_team_token, str)

    alice_user_db = root / "Participants" / alice_hex / "NoteToSelf" / "Sync" / "core.db"
    auconn = sqlite3.connect(str(alice_user_db))
    alice_local_db = device_local_db_path(root, alice_hex)
    aulconn = sqlite3.connect(str(alice_local_db))
    with pytest.raises(sqlite3.OperationalError):
        auconn.execute(
            "SELECT sender_device_key_id, signing_private_key "
            "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
            (bytes.fromhex(token_data["team_id"]), bob_sender_device_key_id),
        ).fetchone()
    bob_peer_sender_key = aulconn.execute(
        "SELECT sender_device_key_id, signing_private_key "
        "FROM peer_sender_key WHERE team_id = ? AND sender_device_key_id = ?",
        (bytes.fromhex(token_data["team_id"]), bob_sender_device_key_id),
    ).fetchone()
    assert bob_peer_sender_key is None
    auconn.close()
    aulconn.close()

    # --- Verify Bob's team dir has a git repo with correct commit ---
    bob_sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    result = subprocess.run(
        ["git", "-C", str(bob_sync), "log", "--oneline"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Created admission proposal" in result.stdout

    # --- The delivery witness: Alice routes to Bob's storage on first contact ---
    #
    # No sync delivered Bob's announcement to Alice and no manual routing
    # fixture ran: the only path the route took is the acceptance courier.
    # Passthrough sessions and a runtime artifact keep this about routing --
    # Alice holds no receiver record for Bob yet, and upload_runtime_artifact
    # skips the own-announcement gate.
    backend.upload_runtime_artifact(bob_team_token, "witness.txt", b"hello from Bob")
    alice_team_passthrough = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    ok, data, _etag = backend.download_runtime_artifact_from_peer(
        alice_team_passthrough, bob_teammate_id_hex, "witness.txt"
    )
    assert ok, data
    assert data == b"hello from Bob"

    if link_invitee_device:
        shutil.copy2(alice_team_sync / "core.db", bob_sync / "core.db")
        bob_device_root = root / "bob-install-b"
        bob_device_root.mkdir()
        join_request = create_identity_join_request(bob_device_root)
        welcome = bob_manager.authorize_identity_join(join_request["join_request_artifact"])
        bootstrap_existing_identity(
            bob_device_root, welcome["welcome_bundle"], _http_client=http
        )
        bob_device_local = TeamManager(bob_device_root, bob_hex)
        accounts = bob_device_local.list_cloud_storage()
        bob_device_local.connect_cloud_storage_credentials(
            accounts[0]["id"],
            access_key=bob_minio["access_key"],
            secret_key=bob_minio["secret_key"],
        )
        bob_manager.push_note_to_self()
        linked_manager = TeamManager(bob_device_root, bob_hex, _http_client=http)
        linked_manager.refresh_note_to_self()
        prepared = linked_manager.prepare_linked_device_team_join("ProjectX")
        created = bob_manager.create_linked_device_bootstrap(
            "ProjectX", prepared["join_request_bundle"]
        )
        linked_manager.finalize_linked_device_bootstrap(
            "ProjectX", created["bootstrap_bundle"]
        )

        team_id = bytes.fromhex(token_data["team_id"])
        linked_anchor = provisioning.get_authority_anchor(bob_device_root, bob_hex, team_id)
        assert linked_anchor is not None
        assert linked_anchor["anchor_public_key"] == bob_anchor["anchor_public_key"]
        invitee_view = provisioning.load_transitional_authority_view(
            root, bob_hex, "ProjectX"
        )
        linked_view = provisioning.load_transitional_authority_view(
            bob_device_root, bob_hex, "ProjectX"
        )
        assert linked_view.identifier == invitee_view.identifier


def test_full_invitation_flow(playground_dir, minio_server_gen):
    _run_full_invitation_flow(playground_dir, minio_server_gen)


def test_linked_device_of_invitee_adopts_matching_anchor(playground_dir, minio_server_gen):
    _run_full_invitation_flow(playground_dir, minio_server_gen, link_invitee_device=True)


def test_double_accept_rejected(playground_dir, minio_server_gen):
    """Second acceptance of the same invitation should fail."""
    alice_minio = minio_server_gen()
    bob_minio = minio_server_gen()
    carol_minio = minio_server_gen()

    root = pathlib.Path(playground_dir)

    # -- Shared Hub --
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # -- Provision participants --
    alice_hex = create_new_participant(root, "Alice")
    bob_hex = create_new_participant(root, "Bob")
    carol_hex = create_new_participant(root, "Carol")

    # -- Register cloud storage via Manager --
    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="s3",
        url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    provisioning.add_cloud_storage(
        root,
        bob_hex,
        protocol="s3",
        url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )
    carol_nts = _open_session(http, "Carol", "NoteToSelf", mode="passthrough")
    provisioning.add_cloud_storage(
        root,
        carol_hex,
        protocol="s3",
        url=carol_minio["endpoint"],
        access_key=carol_minio["access_key"],
        secret_key=carol_minio["secret_key"],
    )

    # -- Alice: create team, push, create invitation --
    team_result = create_team(root, alice_hex, "ProjectX")
    team_bucket = _core_allocation(root, alice_hex, team_result)["location"]

    alice_team_token = _open_session(http, "Alice", "ProjectX", mode="passthrough")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_via_hub(http, alice_team_token, alice_team_sync)

    _make_bucket_public(
        alice_minio["endpoint"], alice_minio["access_key"],
        alice_minio["secret_key"], team_bucket,
    )

    token = create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
    )
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Bob: accept --
    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = accept_and_export(bob_manager, token)

    # -- Alice: complete Bob's acceptance and re-push so Carol can clone the latest --
    complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)
    _push_via_hub(http, alice_team_token, alice_team_sync)

    # -- Carol: accept the same token (provisioning succeeds, completion fails) --
    carol_manager = TeamManager(root, carol_hex, _http_client=http)
    carol_acceptance_b64 = accept_and_export(carol_manager, token)

    with pytest.raises(ValueError, match="already finalized"):
        complete_invitation_acceptance(root, alice_hex, "ProjectX", carol_acceptance_b64)


def test_bootstrap_decrypt_does_not_walk_the_chain_for_a_forged_iteration(monkeypatch):
    team_id = b"t" * 16
    inviter_key, distribution = create_sender_key(team_id, b"d" * 32)
    acceptor_has_inviter = process_sender_key_distribution(distribution)
    _, message = group_encrypt(team_id, inviter_key, b"bootstrap", b"context")

    advances = []
    real_advance = provisioning._advance_chain_key
    monkeypatch.setattr(
        provisioning,
        "_advance_chain_key",
        lambda chain_key: advances.append(chain_key) or real_advance(chain_key),
    )

    forged = serialize_group_message(replace(message, iteration=1_000_000))
    with pytest.raises(InvalidSignature):
        provisioning.decrypt_invitation_bootstrap_payload(acceptor_has_inviter, forged)
    assert advances == []

    # Control: the genuine bytes still read, twice, from the retained key.
    state, plaintext = provisioning.decrypt_invitation_bootstrap_payload(
        acceptor_has_inviter, serialize_group_message(message)
    )
    assert plaintext == b"bootstrap"
    _, plaintext = provisioning.decrypt_invitation_bootstrap_payload(
        state, serialize_group_message(message)
    )
    assert plaintext == b"bootstrap"
