"""Micro tests for the admission package the inviter exports after finalization."""

import json
import pathlib
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import small_sea_manager.provisioning as provisioning
from small_sea_manager.berth_authority import ModeChangeRecord
from wrasse_trust.identity import verify_membership_cert
from wrasse_trust.keys import key_id_from_public
from wrasse_trust.transport import key_certificate_from_team_db_record

from test_admission_records import _admit, _setup_team


def _berth_ids(root, alice_hex):
    db = provisioning._team_db_path(root, alice_hex, "ProjectX")
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM team_app_berth")}


def test_package_carries_verifiable_records_bound_to_the_acceptance(playground_dir):
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

    team_id = bytes.fromhex(body["team_id"])
    certs = {
        c["cert_id"]: key_certificate_from_team_db_record(
            team_id=team_id,
            cert_id=bytes.fromhex(c["cert_id"]),
            cert_type=c["cert_type"],
            subject_key_id=bytes.fromhex(c["subject_key_id"]),
            subject_public_key=bytes.fromhex(c["subject_public_key"]),
            issuer_key_id=bytes.fromhex(c["issuer_key_id"]),
            issuer_teammate_id=bytes.fromhex(c["issuer_teammate_id"]),
            issued_at=c["issued_at"],
            claims_json=c["claims"],
            signature=bytes.fromhex(c["signature"]),
        )
        for c in body["certificates"]
    }
    bob_device = bytes.fromhex(acceptance["invitee_device_public_key"])
    bob_teammate = bytes.fromhex(acceptance["author_teammate_id"])
    alice_teammate = bytes.fromhex(body["integration_mode_changes"][0]["author_teammate_id"])
    bob_certs = [c for c in certs.values() if c.subject_public_key == bob_device]
    assert len(bob_certs) == 1
    assert verify_membership_cert(
        bob_certs[0], alice_public, team_id, alice_teammate, bob_teammate, bob_device
    )
    # The anchor's self-issued genesis cert closes the chain.
    assert any(c.subject_public_key == alice_public for c in certs.values())

    bob_modes = [m for m in body["integration_mode_changes"] if m["teammate_id"] == bob_teammate.hex()]
    assert {bytes.fromhex(m["berth_id"]) for m in bob_modes} == _berth_ids(root, alice_hex)
    assert len(bob_modes) == len(_berth_ids(root, alice_hex))
    for m in body["integration_mode_changes"]:
        record = ModeChangeRecord(
            author_teammate_id=bytes.fromhex(m["author_teammate_id"]),
            author_device_key_id=bytes.fromhex(m["author_device_key_id"]),
            created_at=m["created_at"],
            anchor_commit=m["anchor_commit"],
            constitution_digest=bytes.fromhex(m["constitution_digest"]),
            schema_version=m["schema_version"],
            teammate_id=bytes.fromhex(m["teammate_id"]),
            berth_id=bytes.fromhex(m["berth_id"]),
            mode=m["mode"],
            signature=bytes.fromhex(m["signature"]),
        )
        assert record.author_device_key_id == key_id_from_public(alice_public)
        Ed25519PublicKey.from_public_bytes(alice_public).verify(
            record.signature, record.canonical()
        )


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
