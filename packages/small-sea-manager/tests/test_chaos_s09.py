"""Micro test S09: a pending Constitution-event chain survives reconnects and separate sources.

Chain R -> A -> B -> C (C is a certificate event). Each link arrives as its own
synthetic singleton source, integrated in the order C, B, A. Before the root R
arrives, all three descendants must sit in the pending table and C's
certificate must not be projected. When R arrives in a later, separate
integration, the whole chain must promote and C must gain exactly one
key_certificate row. Replaying every source SHA afterwards adds nothing.
Every inspection opens a fresh database connection; no engine is retained
between calls.
"""

import pathlib
import sqlite3
import subprocess

from cod_sync.repo import Repo
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from small_sea_manager import provisioning
from small_sea_manager.constitution_projection import encode_certificate
from test_admission_records import _admit, _setup_team
from wrasse_trust.events import encode_event, make_event
from wrasse_trust.identity import CertType, issue_cert
from wrasse_trust.keys import ParticipantKey, ProtectionLevel, key_id_from_public

TEAM = "ProjectX"


def _cert():
    """Model on test_constitution_projection._cert, with 16-byte team/teammate IDs."""
    issuer = Ed25519PrivateKey.generate()
    issuer_private = issuer.private_bytes_raw()
    issuer_public = issuer.public_key().public_bytes_raw()
    subject_public = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    issuer_key = ParticipantKey(key_id_from_public(issuer_public), issuer_public, ProtectionLevel.DAILY, "now")
    subject = ParticipantKey(key_id_from_public(subject_public), subject_public, ProtectionLevel.DAILY, "now")
    teammate_id = b"\x01" * 16
    team_id = b"\x02" * 16
    cert = issue_cert(subject, issuer_key, issuer_private, teammate_id, CertType.MEMBERSHIP, team_id, {"teammate_id": "admitted"})
    return cert, teammate_id, issuer_private


def _snapshot(sync: pathlib.Path):
    """Read the Constitution state through a freshly opened connection."""
    with sqlite3.connect(sync / "core.db") as conn:
        stored = conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()
        pending = conn.execute(
            "SELECT event_id, encoded FROM constitution_event_pending ORDER BY event_id"
        ).fetchall()
        certs = conn.execute("SELECT * FROM key_certificate ORDER BY cert_id").fetchall()
    return stored, pending, certs


def _stored_ids(sync):
    return {row[0] for row in _snapshot(sync)[0]}


def _cert_row_count(sync, cert_id: bytes) -> int:
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute("SELECT count(*) FROM key_certificate WHERE cert_id = ?", (cert_id,)).fetchone()[0]


def _source_sha(root, alice_hex: str, events, seq: int) -> str:
    """Build one singleton source in a separate temp Git repo, transfer it into
    Alice's Sync repo under a fresh ref, and integrate it."""
    source = root / f"s09-source-{seq}"
    source.mkdir()
    db = source / "core.db"
    with sqlite3.connect(db) as raw:
        raw.execute("CREATE TABLE constitution_event (event_id BLOB PRIMARY KEY, event_type TEXT, encoded BLOB)")
        raw.execute("CREATE TABLE constitution_event_pending (event_id BLOB PRIMARY KEY, encoded BLOB)")
        for event in events:
            raw.execute(
                "INSERT INTO constitution_event (event_id, event_type, encoded) VALUES (?, ?, ?)",
                (event.event_id, event.event_type, encode_event(event)),
            )
    repo = Repo.init(source / ".git").with_work_tree(source)
    repo.config("user.name", "S09 Source")
    repo.config("user.email", "s09@example.invalid")
    repo.stage(["core.db"])
    repo.commit(f"S09 source {seq}")
    sha = repo.head()

    sync = root / "Participants" / alice_hex / TEAM / "Sync"
    sender_id = provisioning.derive_team_join_state(root, alice_hex, TEAM)["self_in_team"]
    ref = f"refs/small-sea/core-peer/{sender_id.hex()}/observations/s09-{seq}"
    before_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source), f"{sha}:{ref}"],
        check=True,
    )
    after = Repo(sync / ".git", sync)
    assert after.head() == before_head, "fetch must not move the receiver's main"
    assert not after.work_tree_paths_differ_from_head(["core.db"]), "fetch must not touch the receiver's worktree"
    result = provisioning.integrate_core_events(root, alice_hex, TEAM, sha)
    assert result["outcome"] == "integrated", f"source {seq} refused: {result}"
    assert result["new_events"] == 1, result
    return sha


def test_pending_chain_survives_reconnects_and_separate_sources(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, sync = _setup_team(root)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    raw = provisioning.export_admission_package(root, alice_hex, TEAM, bytes.fromhex(acceptance["record_id"]))
    provisioning.import_admission_package(root, bob_hex, TEAM, raw)

    # Start from a clean committed baseline.
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S09 baseline after admission")
    _, pending_before_admit, _ = _snapshot(sync)
    assert pending_before_admit == [], "baseline must have no pending events"

    # Build the chain R -> A -> B -> C.
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    r_event = make_event("chaos_probe", {"label": "R"}, (), private)
    a_event = make_event("chaos_probe", {"label": "A"}, (r_event.event_id,), private)
    b_event = make_event("chaos_probe", {"label": "B"}, (a_event.event_id,), private)
    cert, issuer_teammate_id, issuer_private = _cert()
    c_event = make_event(
        "key_certificate",
        encode_certificate(cert, issuer_teammate_id),
        (b_event.event_id,),
        issuer_private,
    )

    # Integrate the singleton sources in the order C, B, A.
    sha_c = _source_sha(root, alice_hex, (c_event,), 1)
    ids = _stored_ids(sync)
    assert c_event.event_id not in ids, "C must not be stored before its parent"
    _, pending, _ = _snapshot(sync)
    assert [row[0] for row in pending] == [c_event.event_id], f"only C pending after source 1: {pending}"

    sha_b = _source_sha(root, alice_hex, (b_event,), 2)
    ids = _stored_ids(sync)
    assert b_event.event_id not in ids and c_event.event_id not in ids
    _, pending, _ = _snapshot(sync)
    assert sorted(row[0] for row in pending) == sorted([b_event.event_id, c_event.event_id])

    sha_a = _source_sha(root, alice_hex, (a_event,), 3)
    ids = _stored_ids(sync)
    for missing in (r_event, a_event, b_event, c_event):
        assert missing.event_id not in ids, f"{missing.payload} stored before the root R arrived"
    _, pending, _ = _snapshot(sync)
    assert sorted(row[0] for row in pending) == sorted(
        [a_event.event_id, b_event.event_id, c_event.event_id]
    ), "all three descendants must be pending before R arrives"
    assert _cert_row_count(sync, cert.cert_id) == 0, "C's certificate must be absent before R arrives"

    # The root R arrives in a