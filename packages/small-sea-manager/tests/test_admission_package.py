"""Micro tests for the admission package the inviter exports after finalization."""

import json
import pathlib
import shutil
import sqlite3
import subprocess

import pytest
from cod_sync.store import LocalFolderStore
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import small_sea_manager.provisioning as provisioning
from small_sea_manager import berth_authority
from wrasse_trust.events import decode_event
from small_sea_manager.constitution_projection import decode_certificate

from test_admission_proposals import _bootstrap_existing_steward_clone, _push_to_localfolder
from test_admission_records import _admit, _setup_team


def _berth_ids(root, alice_hex):
    db = provisioning._team_db_path(root, alice_hex, "ProjectX")
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM team_app_berth")}


def test_package_carries_every_constitution_event(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, _ = _setup_team(root)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    acceptance_id = bytes.fromhex(acceptance["record_id"])

    raw = provisioning.export_admission_package(root, alice_hex, "ProjectX", acceptance_id)
    assert raw == provisioning.export_admission_package(root, alice_hex, "ProjectX", acceptance_id)

    package = json.loads(raw)
    body = package["body"]
    assert body["package_version"] == provisioning.ADMISSION_PACKAGE_VERSION
    assert body["acceptance_record_id"] == acceptance["record_id"]
    assert body["invitee_device_public_key"] == acceptance["invitee_device_public_key"]

    _, alice_public = provisioning.get_current_team_device_key(root, alice_hex, "ProjectX")
    assert body["sender_device_public_key"] == alice_public.hex()
    Ed25519PublicKey.from_public_bytes(alice_public).verify(
        bytes.fromhex(package["signature"]), provisioning._json_bytes(body)
    )

    source_db = provisioning._team_db_path(root, alice_hex, "ProjectX")
    with sqlite3.connect(source_db) as conn:
        expected = [row[0].hex() for row in conn.execute(
            "SELECT encoded FROM constitution_event ORDER BY event_id"
        )]
    assert body["constitution_events"] == expected
    assert body["constitution_events"]
    events = [decode_event(bytes.fromhex(encoded)) for encoded in body["constitution_events"]]
    assert {event.event_type for event in events} >= {"key_certificate", "integration_mode_change"}


def test_unknown_acceptance_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, _ = _setup_team(root)
    with pytest.raises(provisioning.AdmissionPackageUnavailableError):
        provisioning.export_admission_package(root, alice_hex, "ProjectX", b"\x00" * 32)


def test_unfinalized_acceptance_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, _ = _setup_team(root, quorum=2)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    with pytest.raises(provisioning.AdmissionPackageUnavailableError):
        provisioning.export_admission_package(
            root, alice_hex, "ProjectX", bytes.fromhex(acceptance["record_id"])
        )


# --------------------------------------------------------------------------- #
# Bob imports the package
# --------------------------------------------------------------------------- #


def _steward_package(root):
    alice_hex, bob_hex, cloud, _ = _setup_team(root)
    acceptance = _admit(
        root, alice_hex, bob_hex, cloud,
        mode_plan=provisioning.mode_plan_for_preset("steward"),
    )
    raw = provisioning.export_admission_package(
        root, alice_hex, "ProjectX", bytes.fromhex(acceptance["record_id"])
    )
    return alice_hex, bob_hex, acceptance, raw


def _resign(root, alice_hex, body) -> bytes:
    alice_private, _ = provisioning.get_current_team_device_key(root, alice_hex, "ProjectX")
    signature = provisioning._sign_bytes(alice_private, provisioning._json_bytes(body))
    return provisioning._json_bytes({"body": body, "signature": signature.hex()})


def _bob_state(root, bob_hex):
    sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    head = subprocess.run(
        ["git", "-C", str(sync), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return head, (sync / "core.db").read_bytes()


def test_bob_sees_his_own_admission_after_import(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, acceptance, raw = _steward_package(root)
    head_before, _ = _bob_state(root, bob_hex)

    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is True
    head_after, db_after = _bob_state(root, bob_hex)
    assert head_after != head_before

    view = provisioning.load_transitional_authority_view(root, bob_hex, "ProjectX")
    bob_teammate = bytes.fromhex(acceptance["author_teammate_id"])
    bob_device = bytes.fromhex(acceptance["invitee_device_public_key"])
    assert bob_device in view.trusted_keys[bob_teammate]
    for berth_id in _berth_ids(root, alice_hex):
        assert view.holds(bob_teammate, berth_id) == berth_authority.Standing.HELD

    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is False
    assert _bob_state(root, bob_hex) == (head_after, db_after)


def test_import_stores_events_and_projects_rows(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, acceptance, raw = _steward_package(root)
    source_db = provisioning._team_db_path(root, alice_hex, "ProjectX")
    with sqlite3.connect(source_db) as conn:
        source_events = [decode_event(row[0]) for row in conn.execute(
            "SELECT encoded FROM constitution_event"
        )]
    alice_event_ids = {event.event_id for event in source_events}
    certificates = [
        decode_certificate(event.payload)[0]
        for event in source_events
        if event.event_type == "key_certificate"
    ]
    alice_device = provisioning.get_current_team_device_key(root, alice_hex, "ProjectX")[1]
    expected_certs = {
        cert.cert_id: cert.subject_public_key
        for cert in certificates
        if cert.subject_public_key in (alice_device, bytes.fromhex(acceptance["invitee_device_public_key"]))
    }
    assert set(expected_certs.values()) == {
        alice_device, bytes.fromhex(acceptance["invitee_device_public_key"])
    }
    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is True

    bob_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(bob_db) as conn:
        assert {
            row[0] for row in conn.execute("SELECT event_id FROM constitution_event")
        } == alice_event_ids
        projected_certs = {
            row[0]: row[1]
            for row in conn.execute("SELECT cert_id, subject_public_key FROM key_certificate")
        }
    assert all(projected_certs[cert_id] == public_key for cert_id, public_key in expected_certs.items())


def test_import_then_core_union_is_noop(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, _acceptance, raw = _steward_package(root)
    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is True
    sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    alice_head = subprocess.run(
        ["git", "-C", str(sync), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    bob_sync = root / "Participants" / bob_hex / "ProjectX" / "Sync"
    subprocess.run(
        ["git", "-C", str(bob_sync), "fetch", "--no-tags", str(sync), alice_head],
        capture_output=True, text=True, check=True,
    )
    assert provisioning.integrate_core_events(root, bob_hex, "ProjectX", alice_head) == {
        "outcome": "no_change", "code": None, "new_events": 0
    }


def test_reimport_returns_false(playground_dir):
    root = pathlib.Path(playground_dir)
    _alice_hex, bob_hex, _acceptance, raw = _steward_package(root)
    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is True
    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", raw) is False


def _refused(root, bob_hex, raw, code):
    before = _bob_state(root, bob_hex)
    with pytest.raises(provisioning.AdmissionPackageRejectedError) as info:
        provisioning.import_admission_package(root, bob_hex, "ProjectX", raw)
    assert info.value.code == code
    assert _bob_state(root, bob_hex) == before


def test_tampered_event_refuses_whole_package_and_writes_nothing(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, _acceptance, raw = _steward_package(root)
    body = json.loads(raw)["body"]

    tampered = json.loads(raw)["body"]
    encoded = tampered["constitution_events"][0]
    event_body = json.loads(bytes.fromhex(encoded))
    signature = event_body["signature"]
    event_body["signature"] = ("0" if signature[-1] != "0" else "1") + signature[:-1]
    tampered["constitution_events"][0] = json.dumps(
        event_body, sort_keys=True, separators=(",", ":")
    ).encode().hex()
    _refused(root, bob_hex, _resign(root, alice_hex, tampered), "bad_record_signature")


def test_tampered_or_misaddressed_packages_are_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, _acceptance, raw = _steward_package(root)
    body = json.loads(raw)["body"]

    package = json.loads(raw)
    package["signature"] = "00" * 64
    _refused(root, bob_hex, provisioning._json_bytes(package), "bad_envelope")

    other = dict(body, acceptance_record_id="11" * 32)
    _refused(root, bob_hex, _resign(root, alice_hex, other), "wrong_acceptance")

    other = dict(body, invitee_device_public_key="22" * 32)
    _refused(root, bob_hex, _resign(root, alice_hex, other), "wrong_device")


def test_conflicting_record_id_is_refused(playground_dir):
    root = pathlib.Path(playground_dir)
    _alice_hex, bob_hex, acceptance, raw = _steward_package(root)
    body = json.loads(raw)["body"]
    bob_mode = next(
        event.payload for event in (decode_event(bytes.fromhex(encoded)) for encoded in body["constitution_events"])
        if event.event_type == "integration_mode_change"
        and event.payload["teammate_id"] == acceptance["author_teammate_id"]
    )

    db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO integration_mode_change (record_id, author_teammate_id, "
            "author_device_key_id, created_at, anchor_commit, constitution_digest, "
            "constitution_snapshot_json, schema_version, teammate_id, berth_id, mode, signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'proposal-only', ?)",
            tuple(
                bytes.fromhex(bob_mode[k]) if k in {
                    "record_id", "author_teammate_id", "author_device_key_id",
                    "constitution_digest", "teammate_id", "berth_id", "signature",
                } else bob_mode[k]
                for k in ("record_id", "author_teammate_id", "author_device_key_id",
                          "created_at", "anchor_commit", "constitution_digest",
                          "constitution_snapshot_json", "teammate_id", "berth_id", "signature")
            ),
        )
    _refused(root, bob_hex, raw, "conflicting_record")


# --------------------------------------------------------------------------- #
# The package's copy-paste token form
# --------------------------------------------------------------------------- #


def test_package_token_round_trips_the_exact_bytes(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, _ = _setup_team(root)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    raw = provisioning.export_admission_package(
        root, alice_hex, "ProjectX", bytes.fromhex(acceptance["record_id"])
    )

    token = provisioning.encode_admission_package_token(raw)
    assert provisioning.decode_admission_package_token(token) == raw

    with pytest.raises(provisioning.AdmissionPackageTokenError):
        provisioning.decode_admission_package_token("not a real token")


def test_finalize_admission_returns_a_package_bob_can_import(playground_dir):
    """Issue #266: an endorser's finalize_admission also hands back a package,
    just like complete_invitation_acceptance does when it finalizes inline."""
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
        root, inviter_hex=alice_hex, invitee_hex=carol_hex,
        team_name="ProjectX", display_name="Carol",
    )
    carol_sync = root / "Participants" / carol_hex / "ProjectX" / "Sync"
    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")

    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _push_to_localfolder(alice_sync, alice_cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_store=LocalFolderStore(str(alice_cloud)),
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposal_id = provisioning.list_invitations(root, alice_hex, "ProjectX")[0]["id"]

    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")
    provisioning.endorse_admission(root, carol_hex, "ProjectX", proposal_id)
    shutil.copy2(carol_sync / "core.db", alice_sync / "core.db")

    result = provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_id)
    package_token = result["admission_package"]
    assert package_token is not None

    package_bytes = provisioning.decode_admission_package_token(package_token)
    assert provisioning.import_admission_package(root, bob_hex, "ProjectX", package_bytes) is True

    view = provisioning.load_transitional_authority_view(root, bob_hex, "ProjectX")
    bob_teammate = bytes.fromhex(provisioning.derive_team_join_state(
        root, bob_hex, "ProjectX"
    )["self_in_team"].hex())
    berth_ids = _berth_ids(root, alice_hex)
    for berth_id in berth_ids:
        assert view.holds(bob_teammate, berth_id) == berth_authority.Standing.HELD
