"""Micro test S14: Git supersession keeps divergent event branches.

One source repository carries three commits: H1 holds event A, its
descendant H2 holds A and B, and the sibling commit H3 (branched from H1)
holds A and C. All three SHAs are fetched into observation refs for one
teammate. Head listing must mark H1 non-maximal while keeping both divergent
heads H2 and H3 maximal; integration must accept events from both branches
so the receiver stores A, B, and C exactly once; a second sweep adds
nothing. A bug that picks one divergent history as the winner would lose the
events unique to the other branch.
"""

import pathlib
import sqlite3
import subprocess

from cod_sync.repo import Repo
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from small_sea_manager import provisioning
from small_sea_manager.manager import TeamManager
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


def _write_db(db: pathlib.Path, events) -> None:
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as raw:
        raw.execute(
            "CREATE TABLE constitution_event (event_id BLOB PRIMARY KEY, event_type TEXT, encoded BLOB)"
        )
        raw.execute(
            "CREATE TABLE constitution_event_pending (event_id BLOB PRIMARY KEY, encoded BLOB)"
        )
        for event in events:
            raw.execute(
                "INSERT INTO constitution_event (event_id, event_type, encoded) VALUES (?, ?, ?)",
                (event.event_id, event.event_type, encode_event(event)),
            )


def _build_source(root, a, b, c):
    """Build H1(A), H2(A,B), and sibling H3(A,C) in a separate Git repo.

    Returns the source Repo and the three SHAs in (H1, H2, H3) order.
    """
    source = root / "s14-source"
    source.mkdir()
    db = source / "core.db"
    repo = Repo.init(source / ".git").with_work_tree(source)
    repo.config("user.name", "S14 Source")
    repo.config("user.email", "s14@example.invalid")

    _write_db(db, (a,))
    repo.stage(["core.db"])
    h1 = repo.commit("S14 source H1: A")

    _write_db(db, (a, b))
    repo.stage(["core.db"])
    h2 = repo.commit("S14 source H2: A B")

    # Branch H3 off H1: rewind the branch, then commit A and C.
    subprocess.run(
        ["git", "-C", str(source), "reset", "--soft", h1], check=True
    )
    _write_db(db, (a, c))
    repo.stage(["core.db"])
    h3 = repo.commit("S14 source H3: A C")

    return repo, h1, h2, h3


def _park(sync, source_dir, sha, teammate_id_hex, name) -> str:
    """Fetch one source SHA into a fresh per-teammate observation ref."""
    ref = f"refs/small-sea/core-peer/{teammate_id_hex}/observations/s14-{name}"
    before_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source_dir), f"{sha}:{ref}"],
        check=True,
    )
    after = Repo(sync / ".git", sync)
    assert after.head() == before_head, "fetch must not move the receiver's main"
    assert not after.work_tree_paths_differ_from_head(["core.db"]), "fetch must not touch the worktree"
    return ref


def test_divergent_source_branches_both_stay_maximal_and_integrate(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, sync = _setup_team(root)

    # Clean committed baseline.
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S14 baseline")
    baseline_stored, baseline_pending = _snapshot(sync)

    private = Ed25519PrivateKey.generate().private_bytes_raw()
    a = make_event("chaos_probe", {"label": "A"}, (), private)
    b = make_event("chaos_probe", {"label": "B"}, (), private)
    c = make_event("chaos_probe", {"label": "C"}, (), private)
    assert len({a.event_id, b.event_id, c.event_id}) == 3

    source_repo, h1, h2, h3 = _build_source(root, a, b, c)
    source_dir = source_repo.work_tree
    teammate_id = provisioning.derive_team_join_state(root, alice_hex, TEAM)["self_in_team"].hex()
    ref_h1 = _park(sync, source_dir, h1, teammate_id, "h1")
    ref_h2 = _park(sync, source_dir, h2, teammate_id, "h2")
    ref_h3 = _park(sync, source_dir, h3, teammate_id, "h3")

    tm = TeamManager(root, alice_hex)

    # -- heads: H1 non-maximal, both divergent heads maximal ---------------
    heads = {
        h.ref_name: h
        for h in tm.list_core_source_heads(TEAM)
        if h.ref_name in (ref_h1, ref_h2, ref_h3)
    }
    assert set(heads) == {ref_h1, ref_h2, ref_h3}, heads
    assert heads[ref_h1].head_sha == h1
    assert heads[ref_h2].head_sha == h2
    assert heads[ref_h3].head_sha == h3
    assert not heads[ref_h1].is_maximal, (
        f"H1 is an ancestor of both divergent heads and must not be maximal: {heads}"
    )
    assert sorted(heads[ref_h1].superseded_by_refs) == [ref_h2, ref_h3], (
        f"H1 must report both descendants as superseding: {heads[ref_h1]}"
    )
    assert heads[ref_h2].is_maximal, (
        f"divergent head H2 must stay maximal, not lose to sibling H3: {heads[ref_h2]}"
    )
    assert heads[ref_h3].is_maximal, (
        f"divergent head H3 must stay maximal, not lose to sibling H2: {heads[ref_h3]}"
    )

    # -- first sweep: only H2 and H3 integrate, both branches accepted -----
    results = tm.integrate_core_sources(TEAM)
    by_ref = {r["ref_name"]: r for r in results if r["ref_name"] in (ref_h1, ref_h2, ref_h3)}
    assert set(by_ref) == {ref_h2, ref_h3}, (
        f"only maximal heads H2 and H3 may receive integration results, got {results}"
    )
    assert by_ref[ref_h2]["outcome"] == "integrated", by_ref[ref_h2]
    assert by_ref[ref_h2]["new_events"] == 2, (
        f"H2 must contribute A and B as new, got {by_ref[ref_h2]}"
    )
    assert by_ref[ref_h3]["outcome"] == "integrated", by_ref[ref_h3]
    assert by_ref[ref_h3]["new_events"] == 1, (
        f"H3 must contribute only C as new (A already stored), got {by_ref[ref_h3]}"
    )

    # -- receiver stores A, B, and C exactly once each ----------------------
    stored, pending = _snapshot(sync)
    stored_ids = [row[0] for row in stored]
    for event, label in ((a, "A"), (b, "B"), (c, "C")):
        assert stored_ids.count(event.event_id) == 1, (
            f"{label} must be stored exactly once, got {stored_ids}"
        )
    assert len(stored) == len(baseline_stored) + 3, (
        f"the sweep must add exactly A, B, C on top of the baseline, got {stored}"
    )
    assert pending == baseline_pending, (
        f"parentless probe events must be stored, not left pending: {pending}"
    )

    # -- second sweep adds nothing -----------------------------------------
    head_before = repo.head()
    rows_before = _snapshot(sync)
    results2 = tm.integrate_core_sources(TEAM)
    by_ref2 = {r["ref_name"]: r for r in results2 if r["ref_name"] in (ref_h1, ref_h2, ref_h3)}
    assert set(by_ref2) == {ref_h2, ref_h3}, (
        f"H1 must remain non-maximal on resweep, got {results2}"
    )
    assert all(
        r["outcome"] == "no_change" and r["new_events"] == 0 for r in by_ref2.values()
    ), results2
    assert _snapshot(sync) == rows_before, "second sweep must not change stored or pending events"
    assert repo.head() == head_before, "second sweep must not move the receiver's main"
