"""Micro test S15: integration reads the captured SHA even if a ref moves.

Source A is parked under a mutable ``latest`` observation ref and its SHA is
captured. The test wraps ``Repo.blob_at`` so that, immediately before the
first integration call reads the source, it moves that ref to source B.
Integration must still read A: the wrapper must observe A's SHA as the
requested revision, the receiver must gain only A's event, the ref must be
left at B, and a subsequent integration of B must add B without losing A.
A bug that re-resolves a mutable ref after source selection would integrate
B instead of A.
"""

import pathlib
import sqlite3
import subprocess

from cod_sync.repo import Repo
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from small_sea_manager import provisioning
from test_admission_records import _setup_team
from wrasse_trust.events import encode_event, make_event

TEAM = "ProjectX"


def _snapshot(sync: pathlib.Path):
    """Read stored and pending Constitution events through a fresh connection."""
    with sqlite3.connect(sync / "core.db") as conn:
        stored = conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()
        pending = conn.execute(
            "SELECT event_id, encoded FROM constitution_event_pending ORDER BY event_id"
        ).fetchall()
    return stored, pending


def _build_source(root: pathlib.Path, event, name) -> tuple[pathlib.Path, str]:
    """Build a synthetic source repo holding one event; return (dir, sha)."""
    source = root / f"s15-source-{name}"
    source.mkdir()
    db = source / "core.db"
    with sqlite3.connect(db) as raw:
        raw.execute(
            "CREATE TABLE constitution_event (event_id BLOB PRIMARY KEY, event_type TEXT, encoded BLOB)"
        )
        raw.execute(
            "CREATE TABLE constitution_event_pending (event_id BLOB PRIMARY KEY, encoded BLOB)"
        )
        raw.execute(
            "INSERT INTO constitution_event (event_id, event_type, encoded) VALUES (?, ?, ?)",
            (event.event_id, event.event_type, encode_event(event)),
        )
    repo = Repo.init(source / ".git").with_work_tree(source)
    repo.config("user.name", f"S15 Source {name}")
    repo.config("user.email", f"s15-{name}@example.invalid")
    repo.stage(["core.db"])
    sha = repo.commit(f"S15 source {name}")
    return source, sha


def _park(sync, source_dir, sha, teammate_id_hex, ref_name) -> None:
    """Fetch one source SHA into a fresh per-teammate observation ref."""
    before_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source_dir), f"{sha}:{ref_name}"],
        check=True,
    )
    after = Repo(sync / ".git", sync)
    assert after.head() == before_head, "fetch must not move the receiver's main"
    assert not after.work_tree_paths_differ_from_head(["core.db"]), "fetch must not touch the worktree"


def _ref_target(sync, ref_name) -> str:
    return subprocess.run(
        ["git", "-C", str(sync), "rev-parse", ref_name],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_integration_uses_captured_sha_even_if_ref_moves(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, sync = _setup_team(root)

    # Clean committed baseline.
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S15 baseline")
    baseline_stored, baseline_pending = _snapshot(sync)

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    a = make_event("chaos_probe", {"label": "A"}, (), private)
    b = make_event("chaos_probe", {"label": "B"}, (), private)
    assert len({a.event_id, b.event_id}) == 2

    source_a_dir, sha_a = _build_source(root, a, "a")
    source_b_dir, sha_b = _build_source(root, b, "b")

    teammate_id = provisioning.derive_team_join_state(root, alice_hex, TEAM)["self_in_team"].hex()
    latest = f"refs/small-sea/core-peer/{teammate_id}/observations/s15-latest"

    # 1. Park A under the mutable `latest` ref and capture its SHA.
    _park(sync, source_a_dir, sha_a, teammate_id, latest)
    assert _ref_target(sync, latest) == sha_a, "latest must start at A"
    # Bring B's objects into the receiver so `latest` can later be moved to B.
    _park(sync, source_b_dir, sha_b, teammate_id, f"{latest}-b")

    # 2. Wrap Repo.blob_at to move `latest` to B just before the read.
    observed_revs = []
    original_blob_at = Repo.blob_at

    def sneaky_blob_at(self, rev, path, dest):
        observed_revs.append(rev)
        subprocess.run(
            ["git", "-C", str(sync), "update-ref", latest, sha_b], check=True
        )
        return original_blob_at(self, rev, path, dest)

    try:
        Repo.blob_at = sneaky_blob_at
        result_a = provisioning.integrate_core_events(root, alice_hex, TEAM, sha_a)
    finally:
        Repo.blob_at = original_blob_at

    # 3a. The wrapper observed A's SHA as the requested revision.
    assert observed_revs, "integrate_core_events must read the source via Repo.blob_at"
    assert all(rev == sha_a for rev in observed_revs), (
        f"integration must request the captured SHA A, got {observed_revs}"
    )
    # The ref was moved by the wrapper and must remain at B.
    assert _ref_target(sync, latest) == sha_b, "latest must remain at B after integration"

    # 3b. The first call added only A's new event.
    assert result_a["outcome"] == "integrated", result_a
    assert result_a["new_events"] == 1, (
        f"integrating A must add exactly one new event, got {result_a}"
    )
    stored, pending = _snapshot(sync)
    stored_ids = [row[0] for row in stored]
    assert stored_ids.count(a.event_id) == 1, (
        f"receiver must store A exactly once, got {stored_ids}"
    )
    assert b.event_id not in stored_ids, (
        f"receiver must not have gained B while integrating A: {stored_ids}"
    )
    assert len(stored) == len(baseline_stored) + 1, (
        f"first integration must add exactly A on top of the baseline: {stored}"
    )
    assert pending == baseline_pending, (
        f"parentless probe events must be stored, not left pending: {pending}"
    )

    # 4. Integrate B afterwards; it adds B without losing A.
    result_b = provisioning.integrate_core_events(root, alice_hex, TEAM, sha_b)
    assert result_b["outcome"] == "integrated", result_b
    assert result_b["new_events"] == 1, (
        f"integrating B must add exactly one new event, got {result_b}"
    )
    stored2, pending2 = _snapshot(sync)
    stored_ids2 = [row[0] for row in stored2]
    assert stored_ids2.count(a.event_id) == 1, (
        f"A must survive integrating B, got {stored_ids2}"
    )
    assert stored_ids2.count(b.event_id) == 1, (
        f"B must be stored exactly once, got {stored_ids2}"
    )
    assert len(stored2) == len(baseline_stored) + 2, (
        f"final state must hold baseline plus A and B: {stored2}"
    )
    assert pending2 == baseline_pending, pending2
