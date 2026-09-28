import pathlib
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text

from small_sea_manager.constitution_projection import (
    ProjectionError,
    apply_event,
    encode_certificate,
    store_and_project,
)
from small_sea_manager.constitution_store import append_local_event, current_heads
from wrasse_trust.events import make_event
from wrasse_trust.identity import CertType, issue_cert
from wrasse_trust.keys import ParticipantKey, ProtectionLevel, key_id_from_public


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
    from dataclasses import replace

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
    from small_sea_manager.constitution_store import add_event

    add_event(team_db, left)
    add_event(team_db, right)
    assert current_heads(team_db) == sorted([left.event_id, right.event_id])


def test_unknown_event_type_is_ignored(team_db):
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    event = make_event("future_kind", {"anything": True}, (), private)
    apply_event(team_db, event)
    assert _row_count(team_db) == 0
