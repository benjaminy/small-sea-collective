"""Chaos S08: every arrival order of a diamond produces the same DAG."""

import itertools
import pathlib
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text

from small_sea_manager.constitution_projection import store_and_project
from small_sea_manager.constitution_store import current_heads
from wrasse_trust.events import encode_event, make_event


def _team_db(tmp_path):
    """Schema recipe from tests/test_constitution_store.py::team_db."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "core.db"
    script = (
        pathlib.Path(__file__).parents[1]
        / "small_sea_manager"
        / "sql"
        / "core_other_team.sql"
    )
    with sqlite3.connect(path) as raw:
        raw.executescript(script.read_text())
    return create_engine(f"sqlite:///{path}")


def _diamond():
    key = Ed25519PrivateKey.generate().private_bytes_raw()

    def ev(label, parents):
        return make_event("chaos_probe", {"label": label}, tuple(parents), key)

    root = ev("R", ())
    a = ev("A", (root.event_id,))
    b = ev("B", (root.event_id,))
    j = ev("J", (a.event_id, b.event_id))
    return root, a, b, j


def _rows(conn):
    return conn.execute(
        text("SELECT event_id,event_type,encoded FROM constitution_event ORDER BY event_id")
    ).all()


def test_diamond_arrival_orders_converge(tmp_path):
    root, a, b, j = _diamond()
    siblings = [a, b, j]
    expected_rows = [
        (e.event_id, e.event_type, encode_event(e))
        for e in sorted((root, a, b, j), key=lambda e: e.event_id)
    ]

    for perm in itertools.permutations(siblings):
        perm_dir = tmp_path / "-".join(e.payload["label"] for e in perm)
        engine = _team_db(perm_dir)
        with engine.connect() as conn:
            # Root arrives first, committed.
            assert store_and_project(conn, root)[0] == "stored"
            conn.commit()

            # Each sibling/join arrives in one of the six orders, committed per arrival.
            for event in perm:
                store_and_project(conn, event)
                conn.commit()

            # Same four event rows, no pending rows, only J as head.
            assert _rows(conn) == expected_rows, f"rows differ for order {[e.payload['label'] for e in perm]}"
            assert (
                conn.execute(
                    text("SELECT event_id FROM constitution_event_pending ORDER BY event_id")
                ).all()
                == []
            ), f"pending rows left for order {[e.payload['label'] for e in perm]}"
            assert current_heads(conn) == [j.event_id], f"heads wrong for order {[e.payload['label'] for e in perm]}"

            # Replaying all four events is a no-op that reports already_present.
            for event in (root, a, b, j):
                status, stored = store_and_project(conn, event)
                assert status == "already_present", (
                    f"replay of {event.payload['label']} in order "
                    f"{[e.payload['label'] for e in perm]} returned {status!r}"
                )
                assert stored == []
        engine.dispose()
