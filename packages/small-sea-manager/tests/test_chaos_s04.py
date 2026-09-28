"""Chaos scenario S04: a later refusal rolls back pending promotion.

Store a valid certificate C whose only parent is an absent root P (C parks in
``constitution_event_pending``), commit that pending state, and snapshot it.
Then integrate a synthetic source containing P followed by a certificate whose
outer event signature is valid but whose inner signature is invalid. The whole
batch must be refused, and the refusal must restore the exact baseline: C
still pending, P absent, no certificate row projected. A clean retry with a
separate source containing only P must store P and promote C, empty pending,
and create exactly one certificate row.

If pending deletion and projection insertion survive the source-level rollback,
the baseline comparison after the refused batch fails.
"""

import hashlib
import pathlib
import sqlite3
import subprocess
from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine

from cod_sync.repo import Repo
from small_sea_manager import provisioning
from small_sea_manager.constitution_projection import encode_certificate, store_and_project
from small_sea_manager.constitution_store import current_heads
from test_admission_records import _setup_team
from wrasse_trust.events import encode_event, make_event
from wrasse_trust.identity import CertType, issue_cert
from wrasse_trust.keys import ParticipantKey, ProtectionLevel, key_id_from_public


def _id16(seed: bytes) -> bytes:
    return hashlib.sha256(seed).digest()[:16]


def _cert(seed: int) -> tuple:
    """Build a valid KeyCertificate; return (cert, issuer_teammate_id, issuer_private_key)."""
    issuer = Ed25519PrivateKey.generate()
    issuer_private = issuer.private_bytes_raw()
    issuer_public = issuer.public_key().public_bytes_raw()
    subject_public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    issuer_key = ParticipantKey(
        key_id_from_public(issuer_public), issuer_public, ProtectionLevel.DAILY,
        "2026-01-01T00:00:00+00:00",
    )
    subject = ParticipantKey(
        key_id_from_public(subject_public), subject_public, ProtectionLevel.DAILY,
        "2026-01-01T00:00:00+00:00",
    )
    cert = issue_cert(
        subject, issuer_key, issuer_private, _id16(f"issuer-{seed}".encode()),
        CertType.MEMBERSHIP, _id16(f"team-{seed}".encode()),
        {"teammate_id": _id16(f"teammate-{seed}".encode()).hex()},
    )
    return cert, _id16(f"issuer-teammate-{seed}".encode()), issuer_private


def _sync_dir(root: pathlib.Path, participant_hex: str) -> pathlib.Path:
    return root / "Participants" / participant_hex / "ProjectX" / "Sync"


def _snapshot(root: pathlib.Path, participant_hex: str):
    """Return (event_rows, pending_rows, projection_tables, heads, git_head)."""
    sync = _sync_dir(root, participant_hex)
    with sqlite3.connect(sync / "core.db") as conn:
        events = conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()
        pending = conn.execute(
            "SELECT event_id, encoded FROM constitution_event_pending ORDER BY event_id"
        ).fetchall()
        tables = {
            "key_certificate": conn.execute(
                "SELECT * FROM key_certificate ORDER BY cert_id"
            ).fetchall(),
            "workhorse_delegation": conn.execute(
                "SELECT * FROM workhorse_delegation ORDER BY record_id"
            ).fetchall(),
            "integration_mode_change": conn.execute(
                "SELECT * FROM integration_mode_change ORDER BY record_id"
            ).fetchall(),
        }
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    try:
        with engine.connect() as conn:
            heads = current_heads(conn)
    finally:
        engine.dispose()
    return events, pending, tables, heads, Repo(sync / ".git", sync).head()


def _commit_db(sync: pathlib.Path, message: str) -> None:
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        assert repo.commit(message) is not None


def _build_source(root: pathlib.Path, name: str, events) -> str:
    """Create a synthetic source repo whose core.db holds the given encoded events."""
    source = root / "sources" / name
    source.mkdir(parents=True)
    with sqlite3.connect(source / "core.db") as conn:
        conn.execute(
            "CREATE TABLE constitution_event("
            "event_id BLOB PRIMARY KEY, event_type TEXT, encoded BLOB)"
        )
        conn.execute(
            "CREATE TABLE constitution_event_pending("
            "event_id BLOB PRIMARY KEY, encoded BLOB)"
        )
        for event in events:
            conn.execute(
                "INSERT INTO constitution_event(event_id,event_type,encoded) VALUES(?,?,?)",
                (event.event_id, event.event_type, encode_event(event)),
            )
    repo = Repo.init(source / ".git").with_work_tree(source)
    repo.config("user.email", "chaos@example.com")
    repo.config("user.name", "Chaos")
    repo.stage(["core.db"])
    sha = repo.commit(f"Synthetic source {name}")
    assert sha is not None
    return sha


def _fetch_and_integrate(root: pathlib.Path, alice_hex: str, source_dir: pathlib.Path,
                         source_sha: str, ref: str) -> dict:
    sync = _sync_dir(root, alice_hex)
    pre_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source_dir),
         f"{source_sha}:{ref}"],
        check=True,
        capture_output=True,
    )
    post_fetch = Repo(sync / ".git", sync)
    assert post_fetch.head() == pre_head
    assert not post_fetch.work_tree_paths_differ_from_head(["core.db"])
    return provisioning.integrate_core_events(root, alice_hex, "ProjectX", source_sha)


def test_s04_later_refusal_rolls_back_pending_promotion(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, _sync = _setup_team(root)

    # Clean committed baseline.
    _commit_db(_sync_dir(root, alice_hex), "Baseline Core")

    # Step 1: unknown root P and certificate child C whose only parent is P.
    cert1, issuer_teammate1, private1 = _cert(1)
    p_key = Ed25519PrivateKey.generate().private_bytes_raw()
    p = make_event("chaos_probe", {"label": "S04-P"}, (), p_key)
    c = make_event(
        "key_certificate", encode_certificate(cert1, issuer_teammate1), (p.event_id,), private1
    )

    # Step 2: store C locally; it must park pending; commit; snapshot.
    engine = create_engine(f"sqlite:///{_sync_dir(root, alice_hex) / 'core.db'}")
    try:
        with engine.begin() as conn:
            status, stored = store_and_project(conn, c)
        assert status == "pending" and stored == []
    finally:
        engine.dispose()
    _commit_db(_sync_dir(root, alice_hex), "Store pending certificate C")
    baseline = _snapshot(root, alice_hex)
    # Baseline sanity: C is pending, P is absent, C's certificate is unprojected.
    assert [row[0] for row in baseline[1]] == [c.event_id]
    assert all(row[0] != p.event_id for row in baseline[0])

    # Step 3: source with P followed by a validly wrapped certificate whose
    # inner signature is invalid.
    cert2, issuer_teammate2, private2 = _cert(2)
    bad = make_event(
        "key_certificate",
        encode_certificate(replace(cert2, signature=b"\x00" * 64), issuer_teammate2),
        (),
        private2,
    )
    source_sha = _build_source(root, "s04-a", (p, bad))
    teammate_hex = provisioning.derive_team_join_state(root, alice_hex, "ProjectX")["self_in_team"]
    teammate_hex = teammate_hex.hex() if isinstance(teammate_hex, bytes) else teammate_hex
    ref = f"refs/small-sea/core-peer/{teammate_hex}/observations/s04-a"

    # Step 4: integrate the bad source; the whole batch must be refused and the
    # exact baseline restored (no pending deletion, no projection residue).
    result = _fetch_and_integrate(root, alice_hex, root / "sources" / "s04-a", source_sha, ref)
    assert result == {"outcome": "refused", "code": "bad_projection", "new_events": 0}, result
    assert _snapshot(root, alice_hex) == baseline, (
        "Refused batch left residue: C promoted out of pending, P stored, "
        "or certificate row projected"
    )

    # Step 5: clean retry with a separate source containing only P.
    retry_sha = _build_source(root, "s04-b", (p,))
    result = _fetch_and_integrate(
        root, alice_hex, root / "sources" / "s04-b", retry_sha, ref.replace("s04-a", "s04-b")
    )
    assert result == {"outcome": "integrated", "code": None, "new_events": 1}, result

    events, pending, tables, heads, _git_head = _snapshot(root, alice_hex)
    expected_events = sorted(
        [row for row in baseline[0]]
        + [(p.event_id, "chaos_probe", encode_event(p)),
           (c.event_id, "key_certificate", encode_event(c))],
        key=lambda row: row[0],
    )
    assert events == expected_events
    assert pending == []
    assert len(tables["key_certificate"]) == len(baseline[2]["key_certificate"]) + 1
    assert tables["workhorse_delegation"] == baseline[2]["workhorse_delegation"]
    assert tables["integration_mode_change"] == baseline[2]["integration_mode_change"]
    assert heads == sorted(baseline[3] + [c.event_id])
