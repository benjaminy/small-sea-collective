"""Micro test S10: duplicate pending children from different sources stay singular.

Parent P and child C. Two independent synthetic source commits, each parked
under a different teammate ID, each containing only C. Integrating all sources
twice must leave C in the pending table exactly once (never in the stored
table), and the first sweep must count C as new exactly once. Only when the
third source carrying P arrives does C promote: P and C each stored once,
pending empty, and a further sweep reports no new events. Every inspection
opens a fresh database connection.
"""

import pathlib
import sqlite3
import subprocess

from cod_sync.repo import Repo
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from small_sea_manager import provisioning
from small_sea_manager.manager import TeamManager
from test_admission_records import _admit, _setup_team
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


def _stored_ids(sync):
    return [row[0] for row in _snapshot(sync)[0]]


def _pending_ids(sync):
    return [row[0] for row in _snapshot(sync)[1]]


def _park_source(root, alice_hex, teammate_id_hex, events, name) -> str:
    """Build a source in a separate temp Git repo containing only the given
    events, then fetch its SHA into Alice's Sync under a fresh per-teammate ref."""
    source = root / f"s10-source-{name}"
    source.mkdir()
    db = source / "core.db"
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
    repo = Repo.init(source / ".git").with_work_tree(source)
    repo.config("user.name", "S10 Source")
    repo.config("user.email", "s10@example.invalid")
    repo.stage(["core.db"])
    repo.commit(f"S10 source {name}")
    sha = repo.head()

    sync = root / "Participants" / alice_hex / TEAM / "Sync"
    ref = f"refs/small-sea/core-peer/{teammate_id_hex}/observations/s10-{name}"
    before_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source), f"{sha}:{ref}"],
        check=True,
    )
    after = Repo(sync / ".git", sync)
    assert after.head() == before_head, "fetch must not move the receiver's main"
    assert not after.work_tree_paths_differ_from_head(["core.db"]), "fetch must not touch the worktree"
    return sha


def test_duplicate_pending_children_from_different_sources_are_singular(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, sync = _setup_team(root)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    raw = provisioning.export_admission_package(root, alice_hex, TEAM, bytes.fromhex(acceptance["record_id"]))
    provisioning.import_admission_package(root, bob_hex, TEAM, raw)

    alice_id = provisioning.derive_team_join_state(root, alice_hex, TEAM)["self_in_team"]
    bob_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)["self_in_team"]
    assert alice_id != bob_id, "need two distinct source identities"

    # Clean committed baseline.
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S10 baseline after admission")
    assert _pending_ids(sync) == [], "baseline must have no pending events"

    # Parent P and child C, sharing one signer.
    private = Ed25519PrivateKey.generate().private_bytes_raw()
    p_event = make_event("chaos_probe", {"label": "P"}, (), private)
    c_event = make_event("chaos_probe", {"label": "C"}, (p_event.event_id,), private)

    # Two independent source commits, each containing only C, under different
    # teammate IDs.
    _park_source(root, alice_hex, alice_id.hex(), (c_event,), "a")
    _park_source(root, alice_hex, bob_id.hex(), (c_event,), "b")

    manager = TeamManager(root, alice_hex)

    # Integrate all sources twice.
    sweep1 = manager.integrate_core_sources(TEAM)
    sweep2 = manager.integrate_core_sources(TEAM)

    # Across the first sweep, C counts as new exactly once.
    assert sum(item["new_events"] for item in sweep1) == 1, (
        f"first sweep must count C as new exactly once, got {sweep1}"
    )
    # The second sweep adds nothing.
    assert sum(item["new_events"] for item in sweep2) == 0, (
        f"second sweep must add no new events, got {sweep2}"
    )

    # Before P arrives: C pending exactly once, absent from stored events.
    stored = _stored_ids(sync)
    pending = _pending_ids(sync)
    assert stored.count(c_event.event_id) == 0, "C must not be stored before P arrives"
    assert pending.count(c_event.event_id) == 1, (
        f"C must occur exactly once in pending before P arrives, got {pending}"
    )
    assert len(pending) == 1, f"only C may be pending before P arrives, got {pending}"

    # Third source carrying P, then integrate again.
    _park_source(root, alice_hex, alice_id.hex(), (p_event,), "p")
    sweep3 = manager.integrate_core_sources(TEAM)
    assert sum(item["new_events"] for item in sweep3) >= 1, f"P must be accepted: {sweep3}"

    stored = _stored_ids(sync)
    pending = _pending_ids(sync)
    assert stored.count(p_event.event_id) == 1, f"P must be stored exactly once, got {stored}"
    assert stored.count(c_event.event_id) == 1, f"C must be stored exactly once, got {stored}"
    assert pending == [], f"pending must be empty after P arrives, got {pending}"

    # Another sweep reports no new events.
    sweep4 = manager.integrate_core_sources(TEAM)
    assert sum(item["new_events"] for item in sweep4) == 0, (
        f"no new events after promotion, got {sweep4}"
    )
