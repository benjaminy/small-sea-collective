"""Micro tests for unioning Constitution events from parked Core commits."""

import pathlib
import sqlite3

from sqlalchemy import create_engine

from cod_sync.repo import Repo
from small_sea_manager import provisioning
from small_sea_manager.constitution_projection import store_and_project
from test_constitution_projection import _delegation, _mode
from test_admission_records import _setup_team
from wrasse_trust.events import encode_event, make_event
from wrasse_trust.constitution import canonical_constitution_bytes, derive_record_id, sign_constitution_record
from wrasse_trust.keys import key_id_from_public
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _source(root, alice_hex, event=None, *, corrupt=False, non_sqlite=False, conflict=False, source_ref=None):
    sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    db = sync / "core.db"
    repo = Repo(sync / ".git", sync)
    original = db.read_bytes()
    original_head = repo.head()
    if non_sqlite:
        db.write_bytes(b"not a sqlite database")
    else:
        engine = create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            if event is not None:
                if conflict:
                    existing = conn.exec_driver_sql("SELECT event_id FROM constitution_event LIMIT 1").scalar_one()
                    conn.exec_driver_sql("DELETE FROM constitution_event WHERE event_id=?", (existing,))
                    conn.exec_driver_sql(
                        "INSERT INTO constitution_event(event_id,event_type,encoded) VALUES(?,?,?)",
                        (existing, event.event_type, encode_event(event)),
                    )
                else:
                    store_and_project(conn, event)
            if corrupt:
                conn.exec_driver_sql("UPDATE constitution_event SET encoded = x'00' WHERE rowid = (SELECT min(rowid) FROM constitution_event)")
        engine.dispose()
    repo.stage(["core.db"])
    sha = repo.commit("Create parked event source")
    db.write_bytes(original)
    import subprocess
    subprocess.run(["git", "-C", str(sync), "update-ref", "refs/heads/main", original_head], check=True)
    ref = source_ref or "refs/small-sea/core-peer/" + "ab" * 32 + "/latest"
    repo.create_ref_immutable(ref, sha)
    return repo, sha, ref


def _fixture(playground_dir, event_factory=None, **kwargs):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, sync = _setup_team(root)
    event = event_factory() if event_factory else make_event(
        "future_union_test", {"value": 1}, (), Ed25519PrivateKey.generate().private_bytes_raw()
    )
    repo, sha, ref = _source(root, alice_hex, event, **kwargs)
    return root, alice_hex, sync, repo, sha, ref, event


def _integrate(args):
    root, alice_hex, _sync, _repo, sha, _ref, _event = args
    return provisioning.integrate_core_events(root, alice_hex, "ProjectX", sha)


def test_union_adds_teammate_events_and_projects_rows(playground_dir):
    private, _row, payload = _delegation()
    event = make_event("workhorse_delegation", payload, (), private)
    args = _fixture(playground_dir, lambda: event)
    assert _integrate(args) == {"outcome": "integrated", "code": None, "new_events": 1}
    with sqlite3.connect(args[2] / "core.db") as conn:
        assert conn.execute("SELECT count(*) FROM workhorse_delegation WHERE record_id=?", (_row["record_id"],)).fetchone()[0] == 1


def test_union_is_noop_on_replay(playground_dir):
    args = _fixture(playground_dir)
    assert _integrate(args)["outcome"] == "integrated"
    assert _integrate(args) == {"outcome": "no_change", "code": None, "new_events": 0}


def test_union_refuses_whole_batch_on_bad_event(playground_dir):
    args = _fixture(playground_dir, corrupt=True)
    before = (args[2] / "core.db").read_bytes()
    assert _integrate(args)["code"] == "bad_event"
    assert (args[2] / "core.db").read_bytes() == before


def test_union_refuses_conflicting_event_id(playground_dir):
    args = _fixture(playground_dir, conflict=True)
    # A valid event with the same ID but noncanonical trailing whitespace
    # models a conflicting encoding already present in the live Core.
    encoded = encode_event(args[6])
    with sqlite3.connect(args[2] / "core.db") as conn:
        conn.execute(
            "INSERT INTO constitution_event(event_id,event_type,encoded) VALUES(?,?,?)",
            (args[6].event_id, args[6].event_type, encoded + b" "),
        )
        conn.commit()
    assert _integrate(args)["code"] == "event_conflict"


def test_union_refuses_non_sqlite_source(playground_dir):
    args = _fixture(playground_dir, non_sqlite=True)
    assert _integrate(args)["code"] == "bad_source_db"


def test_union_leaves_parked_refs_untouched(playground_dir):
    args = _fixture(playground_dir)
    before = args[3].list_refs("refs/small-sea/core-peer")
    _integrate(args)
    assert args[3].list_refs("refs/small-sea/core-peer") == before


def test_union_commits_core_db_to_git(playground_dir):
    args = _fixture(playground_dir)
    repo, sha = args[3], args[4]
    result = _integrate(args)
    assert result["outcome"] == "integrated"
    assert repo.work_tree_paths_differ_from_head(["core.db"]) is False
    assert repo.head() != sha


def test_union_one_bad_source_does_not_block_another(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, sync = _setup_team(root)
    event_bad = make_event("bad_source", {}, (), Ed25519PrivateKey.generate().private_bytes_raw())
    teammate = "ab" * 32
    _bad_repo, bad_sha, bad_ref = _source(root, alice_hex, event_bad, corrupt=True, source_ref=f"refs/small-sea/core-peer/{teammate}/observations/bad")
    event = make_event("another_union_test", {}, (), Ed25519PrivateKey.generate().private_bytes_raw())
    _good_repo, good_sha, good_ref = _source(root, alice_hex, event, source_ref=f"refs/small-sea/core-peer/{teammate}/observations/good")
    from small_sea_manager.manager import TeamManager
    tm = TeamManager(root, alice_hex)
    # Source refs share one logical teammate, so leave the older bad head
    # visible alongside the latest good one by making the refs divergent.
    result = tm.integrate_core_sources("ProjectX")
    assert len(result) >= 2
    assert {item["outcome"] for item in result} >= {"refused", "integrated"}


def test_union_mode_change_updates_berth_role(playground_dir):
    private, row, payload = _mode()
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud, sync = _setup_team(root)
    with sqlite3.connect(sync / "core.db") as conn:
        teammate_id = conn.execute("SELECT id FROM teammate LIMIT 1").fetchone()[0]
        berth_id = conn.execute("SELECT id FROM team_app_berth LIMIT 1").fetchone()[0]
    fields = {k: payload[k] for k in ("record_type", "author_teammate_id", "author_device_key_id", "created_at", "anchor_commit", "constitution_digest", "schema_version", "teammate_id", "berth_id", "mode")}
    fields["teammate_id"], fields["berth_id"] = teammate_id.hex(), berth_id.hex()
    canonical = canonical_constitution_bytes(fields)
    payload.update(fields, record_id=derive_record_id(canonical).hex(), signature=sign_constitution_record(private, canonical).hex())
    row["teammate_id"], row["berth_id"] = teammate_id, berth_id
    row["record_id"] = derive_record_id(canonical)
    event = make_event("integration_mode_change", payload, (), private)
    repo, sha, ref = _source(root, alice_hex, event)
    args = (root, alice_hex, sync, repo, sha, ref, event)
    result = _integrate(args)
    assert result["outcome"] == "integrated"
    with sqlite3.connect(args[2] / "core.db") as conn:
        role = conn.execute("SELECT role FROM berth_role WHERE teammate_id=? AND berth_id=?", (row["teammate_id"], row["berth_id"])).fetchone()[0]
        assert role == "read-write"
