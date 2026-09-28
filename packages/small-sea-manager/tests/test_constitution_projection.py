import hashlib
import pathlib
import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from small_sea_manager import berth_authority, provisioning
from small_sea_manager.constitution_projection import (
    ProjectionError,
    apply_event,
    encode_certificate,
    record_workhorse_delegation,
    store_and_project,
)
from small_sea_manager.constitution_store import add_event, append_local_event, current_heads
from small_sea_note_to_self.ids import uuid7
from wrasse_trust.events import make_event
from wrasse_trust.identity import CertType, issue_cert
from wrasse_trust.keys import ParticipantKey, ProtectionLevel, key_id_from_public
from wrasse_trust.constitution import canonical_constitution_bytes, derive_record_id, sign_constitution_record


@pytest.fixture
def team_db(tmp_path):
    script = pathlib.Path(__file__).parents[1] / "small_sea_manager" / "sql" / "core_other_team.sql"
    path = tmp_path / "core.db"
    with sqlite3.connect(path) as raw:
        raw.executescript(script.read_text())
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _cert():
    issuer = Ed25519PrivateKey.generate()
    issuer_private = issuer.private_bytes_raw()
    issuer_public = issuer.public_key().public_bytes_raw()
    subject_public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    issuer_key_id = key_id_from_public(issuer_public)
    subject_key_id = key_id_from_public(subject_public)
    issuer_key = ParticipantKey(issuer_key_id, issuer_public, ProtectionLevel.DAILY, "now")
    subject = ParticipantKey(subject_key_id, subject_public, ProtectionLevel.DAILY, "now")
    cert = issue_cert(subject, issuer_key, issuer_private, b"teammate", CertType.MEMBERSHIP, b"team", {"teammate_id": "admitted"})
    return cert, b"issuer teammate", issuer_private, issuer_public


def _event(cert, teammate_id, private_key, parents=()):
    return make_event("key_certificate", encode_certificate(cert, teammate_id), tuple(parents), private_key)


def _row_count(conn):
    return conn.execute(text("SELECT count(*) FROM key_certificate")).scalar_one()


def test_certificate_event_projects_row(team_db):
    cert, teammate, private, _ = _cert()
    event = _event(cert, teammate, private)
    status, stored = store_and_project(team_db, event)
    assert status == "stored" and stored == [event]
    assert _row_count(team_db) == 1


def test_certificate_event_reprojection_is_noop(team_db):
    cert, teammate, private, _ = _cert()
    event = _event(cert, teammate, private)
    store_and_project(team_db, event)
    apply_event(team_db, event)
    assert _row_count(team_db) == 1


def test_certificate_event_with_wrong_signer_is_refused(team_db):
    cert, teammate, _, _ = _cert()
    other = Ed25519PrivateKey.generate().private_bytes_raw()
    event = _event(cert, teammate, other)
    with pytest.raises(ProjectionError, match="wrong_certificate_signer"):
        apply_event(team_db, event)


def test_certificate_event_with_bad_inner_signature_is_refused(team_db):
    cert, teammate, private, _ = _cert()
    cert = replace(cert, signature=b"\x00" * 64)
    event = _event(cert, teammate, private)
    with pytest.raises(ProjectionError, match="bad_certificate_signature"):
        apply_event(team_db, event)


def test_promoted_pending_events_are_projected(team_db):
    cert, teammate, private, _ = _cert()
    root = make_event("unknown", {}, (), private)
    child = _event(cert, teammate, private, (root.event_id,))
    assert store_and_project(team_db, child)[0] == "pending"
    _, stored = store_and_project(team_db, root)
    assert [event.event_id for event in stored] == [root.event_id, child.event_id]
    assert _row_count(team_db) == 1


def test_bad_inner_event_with_missing_parent_is_refused_not_pending(team_db):
    cert, teammate, private, _ = _cert()
    cert = replace(cert, signature=b"\x00" * 64)
    parent = make_event("unknown", {}, (), private)
    event = _event(cert, teammate, private, (parent.event_id,))
    with pytest.raises(ProjectionError, match="bad_certificate_signature"):
        store_and_project(team_db, event)
    assert team_db.execute(text("SELECT count(*) FROM constitution_event_pending")).scalar_one() == 0
    assert team_db.execute(text("SELECT count(*) FROM constitution_event")).scalar_one() == 0


def test_append_local_event_uses_current_heads(team_db):
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    [first] = append_local_event(team_db, "unknown", {"n": 1}, private)
    [second] = append_local_event(team_db, "unknown", {"n": 2}, private)
    assert second.parents == (first.event_id,)


def test_current_heads_with_concurrent_branches(team_db):
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    [root] = append_local_event(team_db, "unknown", {}, private)
    left = make_event("unknown", {"branch": "left"}, (root.event_id,), private)
    right = make_event("unknown", {"branch": "right"}, (root.event_id,), private)
    add_event(team_db, left)
    add_event(team_db, right)
    assert current_heads(team_db) == sorted([left.event_id, right.event_id])


def test_unknown_event_type_is_ignored(team_db):
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    event = make_event("future_kind", {"anything": True}, (), private)
    apply_event(team_db, event)
    assert _row_count(team_db) == 0


def _delegation(private=None):
    private = private or Ed25519PrivateKey.generate().private_bytes_raw()
    key = Ed25519PrivateKey.from_private_bytes(private)
    workhorse = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
    record = berth_authority.sign_workhorse_delegation(
        team_id=b"team", berth_id=b"berth", workhorse_public_key=workhorse,
        delegator_teammate_id=b"delegator", delegator_private_key=private,
    )
    row = {"record_id": record.record_id, "schema_version": berth_authority.DELEGATION_VERSION,
           "berth_id": record.berth_id, "workhorse_public_key": record.workhorse_public_key,
           "delegator_teammate_id": record.delegator_teammate_id,
           "delegator_public_key": record.delegator_public_key, "signature": record.signature}
    payload = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in row.items()}
    payload["team_id"] = b"team".hex()
    return private, row, payload


_EMPTY_SNAPSHOT_DIGEST = hashlib.sha256(b"{}").digest()


def _mode(private=None):
    private = private or Ed25519PrivateKey.generate().private_bytes_raw()
    public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()
    fields = {"record_type": "integration_mode_change", "author_teammate_id": b"author".hex(),
              "author_device_key_id": key_id_from_public(public).hex(), "created_at": "now",
              "anchor_commit": None, "constitution_digest": _EMPTY_SNAPSHOT_DIGEST.hex(), "schema_version": 1,
              "teammate_id": b"member".hex(), "berth_id": b"berth".hex(), "mode": "automatic"}
    canonical = canonical_constitution_bytes(fields)
    row = {"record_id": derive_record_id(canonical), **fields, "constitution_snapshot_json": "{}",
           "constitution_digest": _EMPTY_SNAPSHOT_DIGEST, "author_teammate_id": b"author",
           "author_device_key_id": key_id_from_public(public), "teammate_id": b"member", "berth_id": b"berth",
           "signature": sign_constitution_record(private, canonical)}
    payload = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in row.items()}
    return private, row, payload


def test_delegation_event_projects_row(team_db):
    private, row, payload = _delegation()
    event = make_event("workhorse_delegation", payload, (), private)
    assert store_and_project(team_db, event)[0] == "stored"
    assert team_db.execute(text("SELECT record_id FROM workhorse_delegation")).scalar_one() == row["record_id"]


def test_delegation_event_with_wrong_signer_is_refused(team_db):
    _, _, payload = _delegation()
    event = make_event("workhorse_delegation", payload, (), Ed25519PrivateKey.generate().private_bytes_raw())
    with pytest.raises(ProjectionError, match="wrong_delegation_signer"):
        apply_event(team_db, event)


def test_delegation_event_with_bad_inner_signature_is_refused(team_db):
    private, _, payload = _delegation()
    payload["signature"] = (b"x" * 64).hex()
    event = make_event("workhorse_delegation", payload, (), private)
    with pytest.raises(ProjectionError, match="bad_delegation_signature"):
        apply_event(team_db, event)


def test_delegation_payload_missing_team_id_is_refused(team_db):
    private, _, payload = _delegation()
    payload.pop("team_id")
    event = make_event("workhorse_delegation", payload, (), private)
    with pytest.raises(ProjectionError, match="bad_delegation_payload"):
        store_and_project(team_db, event)


@pytest.mark.parametrize("event_type,mutate", [
    ("workhorse_delegation", lambda p: p.update(schema_version="one")),
    ("integration_mode_change", lambda p: p.update(teammate_id=7)),
    ("key_certificate", lambda p: p.update(claims=[])),
])
def test_malformed_payload_field_types_are_refused(team_db, event_type, mutate):
    if event_type == "workhorse_delegation":
        private, _, payload = _delegation()
    elif event_type == "integration_mode_change":
        private, _, payload = _mode()
    else:
        cert, teammate, private, _ = _cert()
        payload = encode_certificate(cert, teammate)
    mutate(payload)
    event = make_event(event_type, payload, (), private)
    with pytest.raises(ProjectionError):
        store_and_project(team_db, event)
    assert team_db.execute(text("SELECT count(*) FROM constitution_event_pending")).scalar_one() == 0


def test_ensure_signing_records_one_delegation_event(playground_dir):
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    berth = provisioning.derive_team_join_state(root, participant, "ProjectX")["berth_id"]
    if isinstance(berth, str):
        berth = bytes.fromhex(berth)
    provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth)
    provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth)
    db = root / "Participants" / participant / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT count(*) FROM constitution_event WHERE event_type='workhorse_delegation'").fetchone()[0]
    assert count == 1


def test_mode_change_event_projects_row(team_db):
    private, row, payload = _mode()
    event = make_event("integration_mode_change", payload, (), private)
    assert store_and_project(team_db, event)[0] == "stored"
    assert team_db.execute(text("SELECT record_id FROM integration_mode_change")).scalar_one() == row["record_id"]


def test_mode_change_event_with_wrong_signer_is_refused(team_db):
    _, _, payload = _mode()
    event = make_event("integration_mode_change", payload, (), Ed25519PrivateKey.generate().private_bytes_raw())
    with pytest.raises(ProjectionError, match="wrong_mode_change_signer"):
        apply_event(team_db, event)


def test_mode_change_event_with_bad_inner_signature_is_refused(team_db):
    private, _, payload = _mode()
    payload["signature"] = (b"x" * 64).hex()
    event = make_event("integration_mode_change", payload, (), private)
    with pytest.raises(ProjectionError, match="bad_mode_change_signature"):
        apply_event(team_db, event)


def test_mode_change_author_site_records_event(playground_dir):
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    db = root / "Participants" / participant / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(db) as conn:
        berth = conn.execute("SELECT id FROM team_app_berth LIMIT 1").fetchone()[0]
        teammate = uuid7()
        conn.execute("INSERT INTO teammate (id, display_name) VALUES (?, 'Bob')", (teammate,))
        conn.commit()
    provisioning.set_teammate_integration_mode(root, participant, "ProjectX", teammate, berth, "proposal-only")
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT count(*) FROM constitution_event WHERE event_type='integration_mode_change'").fetchone()[0]
    assert count == 1


def test_delegate_workhorse_key_twice_records_one_event(playground_dir):
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    berth = provisioning.derive_team_join_state(root, participant, "ProjectX")["berth_id"]
    if isinstance(berth, str):
        berth = bytes.fromhex(berth)
    provisioning.delegate_workhorse_key(root, participant, "ProjectX", berth)
    provisioning.delegate_workhorse_key(root, participant, "ProjectX", berth)
    db = root / "Participants" / participant / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT count(*) FROM constitution_event WHERE event_type='workhorse_delegation'").fetchone()[0]
    assert count == 1


def test_mode_change_with_swapped_snapshot_is_refused(team_db):
    private, _, payload = _mode()
    payload["constitution_snapshot_json"] = '{"swapped": true}'
    event = make_event("integration_mode_change", payload, (), private)
    with pytest.raises(ProjectionError, match="bad_mode_change_snapshot"):
        store_and_project(team_db, event)
    assert team_db.execute(text("SELECT count(*) FROM constitution_event")).scalar_one() == 0


def test_certificate_event_derives_teammate_and_device_rows(team_db):
    cert, teammate, private, _ = _cert()
    admitted = b"admitted-teammate"
    cert.claims["teammate_id"] = admitted.hex()
    # Re-sign after changing a signed claim.
    issuer_public = Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes_raw()
    issuer_key = ParticipantKey(cert.issuer_key_id, issuer_public, ProtectionLevel.DAILY, "now")
    subject = ParticipantKey(cert.subject_key_id, cert.subject_public_key, ProtectionLevel.DAILY, "now")
    cert = issue_cert(subject, issuer_key, private, b"teammate", CertType.MEMBERSHIP, b"team", {"teammate_id": admitted.hex()})
    store_and_project(team_db, _event(cert, teammate, private))
    ids = {row[0] for row in team_db.execute(text("SELECT id FROM teammate"))}
    assert {teammate, admitted} <= ids
    device = team_db.execute(text("SELECT teammate_id, public_key FROM team_device WHERE device_key_id = :k"), {"k": cert.subject_key_id}).one()
    assert tuple(device) == (admitted, cert.subject_public_key)
