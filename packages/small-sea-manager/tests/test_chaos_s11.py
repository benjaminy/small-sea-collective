"""Micro test S11: local authoring joins all integrated heads.

Baseline heads H on Alice. Remote source A descends from H, but before A is
integrated Alice appends local L, which also descends from H. After integrating
A, A and L are both heads. A third local event J must take exactly sorted([A,
L]) as parents and become the sole head. Replaying A's source must not replace
local history: L, A, and J all survive with their encoded bytes and J's parent
list unchanged.
"""

import pathlib
import sqlite3
import subprocess

from sqlalchemy import create_engine

from cod_sync.repo import Repo

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from small_sea_manager import constitution_store, provisioning
from small_sea_manager.constitution_projection import store_and_project
from test_admission_records import _admit, _setup_team
from wrasse_trust.events import decode_event, encode_event, make_event

TEAM = "ProjectX"


def _heads(sync: pathlib.Path) -> list[bytes]:
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    with engine.connect() as conn:
        heads = constitution_store.current_heads(conn)
    engine.dispose()
    return heads


def _stored_rows(sync: pathlib.Path) -> list[tuple]:
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()


def _encoded_of(rows: list[tuple], event_id: bytes) -> bytes:
    for row in rows:
        if row[0] == event_id:
            return row[2]
    raise AssertionError(f"event {event_id.hex()} not in stored rows")


def _park_source(root, alice_hex, teammate_id_hex, event, name) -> str:
    """Build a source in a separate temp Git repo containing only the given
    event, then fetch its SHA into Alice's Sync under a fresh per-teammate ref."""
    source = root / f"s11-source-{name}"
    source.mkdir()
    db = source / "core.db"
    # Direct insertion deliberately permits incomplete sources: A's parents
    # (the receiver's baseline heads) are not present in this source.
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
    repo.config("user.name", "S11 Source")
    repo.config("user.email", "s11@example.invalid")
    repo.stage(["core.db"])
    repo.commit(f"S11 source {name}")
    sha = repo.head()

    sync = root / "Participants" / alice_hex / TEAM / "Sync"
    ref = f"refs/small-sea/core-peer/{teammate_id_hex}/observations/s11-{name}"
    before_head = Repo(sync / ".git", sync).head()
    subprocess.run(
        ["git", "-C", str(sync), "fetch", "--no-tags", str(source), f"{sha}:{ref}"],
        check=True,
    )
    after = Repo(sync / ".git", sync)
    assert after.head() == before_head, "fetch must not move the receiver's main"
    assert not after.work_tree_paths_differ_from_head(["core.db"]), "fetch must not touch the worktree"
    return sha


def test_local_authoring_joins_all_integrated_heads(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, sync = _setup_team(root)
    acceptance = _admit(root, alice_hex, bob_hex, cloud)
    raw = provisioning.export_admission_package(root, alice_hex, TEAM, bytes.fromhex(acceptance["record_id"]))
    provisioning.import_admission_package(root, bob_hex, TEAM, raw)

    # Clean committed baseline.
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S11 baseline after admission")

    # Step 1: capture the receiver's baseline heads H.
    heads = _heads(sync)
    H = tuple(heads)

    # Step 2: remote A descends from H.
    private_a = Ed25519PrivateKey.generate().private_bytes_raw()
    a_event = make_event("chaos_probe", {"label": "A"}, H, private_a)
    bob_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)["self_in_team"]
    sha_a = _park_source(root, alice_hex, bob_id.hex(), a_event, "a")

    # Step 3: before integrating A, append local L, which also descends from H.
    private_l = Ed25519PrivateKey.generate().private_bytes_raw()
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    with engine.begin() as conn:
        (l_event,) = constitution_store.append_local_event(
            conn, "chaos_probe", {"label": "L"}, private_l
        )
    engine.dispose()
    assert l_event.parents == H, f"L must descend from the baseline heads, got {l_event.parents}"

    # Step 4: integrate A; A and L are both heads.
    result = provisioning.integrate_core_events(root, alice_hex, TEAM, sha_a)
    assert result == {"outcome": "integrated", "code": None, "new_events": 1}, result
    assert _heads(sync) == sorted([a_event.event_id, l_event.event_id]), (
        f"after integrating A, both A and L must be heads, got {_heads(sync)}"
    )

    # Step 5: append local J and commit.
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    with engine.begin() as conn:
        (j_event,) = constitution_store.append_local_event(
            conn, "chaos_probe", {"label": "J"}, private_l
        )
    engine.dispose()
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit("S11 local event J")

    # J's parents are exactly the sorted IDs of A and L.
    assert j_event.parents == tuple(sorted([a_event.event_id, l_event.event_id])), (
        f"J must join all integrated heads, got parents {[p.hex() for p in j_event.parents]}"
    )
    # J is the sole head.
    assert _heads(sync) == [j_event.event_id], f"J must be the sole head, got {_heads(sync)}"

    # Replay A's source: L, A, and J all survive, encoded bytes and J's parents intact.
    before = _stored_rows(sync)
    before_a, before_l, before_j = (
        _encoded_of(before, a_event.event_id),
        _encoded_of(before, l_event.event_id),
        _encoded_of(before, j_event.event_id),
    )
    replay = provisioning.integrate_core_events(root, alice_hex, TEAM, sha_a)
    assert replay["outcome"] in ("no_change", "integrated"), replay
    assert replay["new_events"] == 0, f"replay must add no new events, got {replay}"

    after = _stored_rows(sync)
    for event in (a_event, l_event, j_event):
        assert any(row[0] == event.event_id for row in after), (
            f"replay must preserve {event.payload.get('label')}"
        )
    assert _encoded_of(after, a_event.event_id) == before_a
    assert _encoded_of(after, l_event.event_id) == before_l
    assert _encoded_of(after, j_event.event_id) == before_j
    stored_j = decode_event(_encoded_of(after, j_event.event_id))
    assert stored_j.parents == tuple(sorted([a_event.event_id, l_event.event_id])), (
        f"replay must preserve J's parent list, got {[p.hex() for p in stored_j.parents]}"
    )
    assert _heads(sync) == [j_event.event_id], f"J must remain the sole head, got {_heads(sync)}"
