"""Micro tests for adoption, competing writers and restoring older installations.

Real SQLite and Git exercise the boundaries. Hub route lookup is real, with a
session supplied directly so these schedules require no provider or PIN flow.
Interruptions raise exceptions; they do not simulate power loss.
"""

import contextlib
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest

import test_note_to_self_integration as f
from cod_sync.repo import RepoError
from small_sea_hub import backend as hub_module
from small_sea_hub.cloud_errors import CloudBerthSourceAmbiguousExn, CloudBerthSourcePausedExn
from small_sea_manager import berth_source_decision as decision
from small_sea_manager import note_to_self_sync as sync
from small_sea_manager.manager import TeamManager
from small_sea_note_to_self.db import attached_note_to_self_connection, device_local_db_path


def _hub(root, participant):
    backend = object.__new__(hub_module.SmallSeaBackend)
    backend.root_dir = root
    session = SimpleNamespace(participant_id=bytes.fromhex(participant), berth_id=f._BERTH_ID)
    return backend, session


def _route(root, participant):
    backend, session = _hub(root, participant)
    return backend._resolve_berth_cloud_or_raise(session)


def _credentials(root, participant):
    with contextlib.closing(attached_note_to_self_connection(root, participant)) as conn:
        conn.execute(
            "INSERT INTO local.cloud_storage_credential (cloud_storage_id, access_key, secret_key) VALUES (?, ?, ?)",
            (f._CLOUD_ID, "test-key", "test-secret"),
        )
        conn.commit()


def _resolve(root, participant, location="loc-B"):
    report, digest = f._fresh_report(root, participant)
    decision.resolve_berth_source(root, participant, f._BERTH_ID, f._key_for(report, location), digest)
    return digest


def _new_source(root, participant, repo):
    base = repo.head()
    source = f._source_commit(repo, base, [
        f._allocation(b"\xd1" * 16, "loc-D"),
        f._insert_team(b"\xdd" * 16, "NewSiblingWork"),
    ])
    f._apply_local(root, participant, [f._insert_team(b"\xcc" * 16, "NewLocalWork")])
    f._commit_local(root, participant, repo)
    ref = f._park(repo, "uid-d", source)
    return sync.NoteToSelfSource(ref, source)


@pytest.mark.parametrize("interruption", ["before_projection", "after_commit"])
def test_interrupted_adoption_preserves_the_pause_boundary(tmp_path, monkeypatch, interruption):
    root = tmp_path / "installation"
    participant, repo = f._participant(root)
    f._competing_allocations(root, participant, repo)
    TeamManager(root, participant).integrate_note_to_self()
    _credentials(root, participant)
    old_digest = _resolve(root, participant)
    f._commit_local(root, participant, repo)
    source = _new_source(root, participant, repo)
    previous_head = repo.head()

    with monkeypatch.context() as patch:
        if interruption == "before_projection":
            original = sync.apply_delta

            def interrupted(conn, delta):
                original(conn, delta)
                assert conn.execute("SELECT COUNT(*) FROM berth_cloud_allocation").fetchone()[0] == 2
                # Another connection sees the old committed choice, never the
                # new candidate without its pause.
                assert f._live_locations(root, participant) == ["loc-B"]
                assert f._pause_report(root, participant) is None
                assert _route(root, participant).location == "loc-B"
                raise RuntimeError("interrupted before pause projection")

            patch.setattr(sync, "apply_delta", interrupted)
            with pytest.raises(RuntimeError, match="before pause"):
                sync.adopt_source(root, participant, repo, source)
            assert f._live_locations(root, participant) == ["loc-B"]
            assert f._pause_report(root, participant) is None
            assert "NewSiblingWork" not in f._team_names(root, participant)
        else:
            def interrupted(*args):
                assert f._live_locations(root, participant) == ["loc-B", "loc-D"]
                assert f._pause_report(root, participant) is not None
                with pytest.raises(CloudBerthSourcePausedExn):
                    _route(root, participant)
                raise RepoError("interrupted before Git merge recording")

            patch.setattr(sync, "_record_merge", interrupted)
            result = sync.adopt_source(root, participant, repo, source)
            assert result.outcome == "recording_pending"
        assert repo.head() == previous_head

    result = TeamManager(root, participant).integrate_note_to_self()
    assert [o.outcome for o in result.outcomes] == ["integrated"]
    assert f._live_locations(root, participant) == ["loc-B", "loc-D"]
    assert {"OnlyOnA", "NewLocalWork", "NewSiblingWork"} <= set(f._team_names(root, participant))
    assert f._team_names(root, participant).count("NewSiblingWork") == 1
    assert repo.is_ancestor(source.head_sha, repo.head())
    assert f._locations(f._pause_report(root, participant)) == ["loc-A", "loc-B", "loc-D"]
    with contextlib.closing(attached_note_to_self_connection(root, participant)) as conn:
        assert decision.read_choice(conn, f._BERTH_ID)["evidence_digest"] == old_digest
    with pytest.raises(CloudBerthSourcePausedExn):
        _route(root, participant)
    assert TeamManager(root, participant).integrate_note_to_self().outcomes == ()


@pytest.mark.parametrize("writer", ["adoption", "locator", "account"])
@pytest.mark.parametrize("first", ["writer", "resolution"])
def test_resolution_serializes_allocation_and_route_writers(tmp_path, monkeypatch, writer, first):
    root = tmp_path / "installation"
    participant, repo = f._participant(root)
    f._competing_allocations(root, participant, repo)
    TeamManager(root, participant).integrate_note_to_self()
    _credentials(root, participant)
    source = _new_source(root, participant, repo)
    report, digest = f._fresh_report(root, participant)
    key = f._key_for(report, "loc-B")
    backend, _ = _hub(root, participant)

    account_connect = attached_note_to_self_connection

    def write():
        if writer == "adoption":
            result = sync.adopt_source(root, participant, repo, source)
            assert result.outcome == "integrated", result
            return True
        if writer == "locator":
            return backend._writeback_locator(
                participant, b"\xa1" * 16, "loc-A", "loc-A-final", cloud_storage_id=f._CLOUD_ID,
            )
        with contextlib.closing(account_connect(root, participant)) as conn:
            conn.execute("UPDATE cloud_storage SET url = 'http://new-route' WHERE id = ?", (f._CLOUD_ID,))
            conn.commit()
        return True

    def resolve():
        return decision.resolve_berth_source(root, participant, f._BERTH_ID, key, digest)

    if first == "writer":
        assert write() is True
        with pytest.raises(decision.EvidenceChangedError) as raised:
            resolve()
        assert decision.report_digest(raised.value.report) != digest
        assert f._pause_report(root, participant) is not None
        with pytest.raises(CloudBerthSourcePausedExn):
            _route(root, participant)
        fresh, fresh_digest = f._fresh_report(root, participant)
        live_key = next(c["candidate_key"] for c in fresh["candidates"] if c["live"] and c["location"] == "loc-B")
        decision.resolve_berth_source(root, participant, f._BERTH_ID, live_key, fresh_digest)
    else:
        comparing = threading.Event()
        release = threading.Event()
        writing = threading.Event()
        original_digest = decision.report_digest
        original_connect = attached_note_to_self_connection

        def held_digest(value):
            result = original_digest(value)
            comparing.set()
            assert release.wait(10), "resolution was not released"
            return result

        def traced_connection(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            conn.set_trace_callback(lambda sql: writing.set() if (
                sql == "BEGIN IMMEDIATE" or sql.startswith("UPDATE") or sql.lstrip().startswith("UPDATE")
            ) else None)
            return conn

        with ThreadPoolExecutor(max_workers=2) as pool:
            with monkeypatch.context() as patch:
                patch.setattr(decision, "report_digest", held_digest)
                resolving = pool.submit(resolve)
                try:
                    assert comparing.wait(10)
                    # Prove the reservation excludes a second SQLite writer.
                    with f._live(root, participant) as conn:
                        conn.execute("PRAGMA busy_timeout = 0")
                        with pytest.raises(sqlite3.OperationalError, match="locked"):
                            conn.execute("BEGIN IMMEDIATE")
                    patch.setattr(sync, "attached_note_to_self_connection", traced_connection)
                    patch.setattr(hub_module, "attached_note_to_self_connection", traced_connection)
                    # Account writes use the same real attached connection.
                    account_connect = traced_connection
                    pending = pool.submit(write)
                    assert writing.wait(10)
                    with pytest.raises(TimeoutError):
                        pending.result(timeout=0.05)
                finally:
                    # Only the resolution's first digest is held. Adoption's
                    # later projection computes its own digest normally.
                    patch.setattr(decision, "report_digest", original_digest)
                    release.set()
                resolving.result(timeout=10)
                assert pending.result(timeout=10) is (writer != "locator")

    fresh, current_digest = f._fresh_report(root, participant)
    if writer == "adoption" and first == "resolution":
        assert f._live_locations(root, participant) == ["loc-B", "loc-D"]
        assert f._locations(fresh, live=False) == ["loc-A"]
        assert current_digest != digest
        assert "NewSiblingWork" in f._team_names(root, participant)
        with pytest.raises(CloudBerthSourcePausedExn):
            _route(root, participant)
    else:
        assert f._live_locations(root, participant) == ["loc-B"]
        assert f._pause_report(root, participant) is None
        route = _route(root, participant)
        assert route.location == "loc-B"
        assert route.url == ("http://new-route" if writer == "account" else "http://x")
        if writer == "locator" and first == "writer":
            assert {"loc-A", "loc-A-final"} <= set(f._locations(fresh, live=False))


@pytest.mark.parametrize("snapshot", ["before_detection", "paused"])
@pytest.mark.parametrize("scope", ["local_database", "whole_installation"])
@pytest.mark.parametrize("later", ["ambiguous", "withdrawn", "resolved"])
def test_older_snapshot_restoration_exposes_what_was_lost(tmp_path, snapshot, scope, later):
    root = tmp_path / "installation"
    saved = tmp_path / "snapshot"
    participant, repo = f._participant(root)
    f._competing_allocations(root, participant, repo)
    _credentials(root, participant)
    if snapshot == "paused":
        TeamManager(root, participant).integrate_note_to_self()
    snapshot_refs = repo.list_refs("refs/")
    snapshot_rows = f._live_locations(root, participant)
    shutil.copytree(root, saved)
    if snapshot == "before_detection":
        TeamManager(root, participant).integrate_note_to_self()
    if later == "withdrawn":
        f._apply_local(root, participant, [("DELETE FROM berth_cloud_allocation WHERE location = 'loc-A'", ())])
        f._fresh_report(root, participant)
        f._commit_local(root, participant, repo)
    elif later == "resolved":
        _resolve(root, participant)
        f._commit_local(root, participant, repo)
    latest_refs = repo.list_refs("refs/")
    latest_rows = f._live_locations(root, participant)

    # No connections or services are held open while replacing these files.
    if scope == "whole_installation":
        shutil.rmtree(root)
        shutil.copytree(saved, root)
    else:
        shutil.copy2(device_local_db_path(saved, participant), device_local_db_path(root, participant))
    assert repo.list_refs("refs/") == (snapshot_refs if scope == "whole_installation" else latest_refs)
    rows = snapshot_rows if scope == "whole_installation" else latest_rows
    assert f._live_locations(root, participant) == rows
    with contextlib.closing(attached_note_to_self_connection(root, participant)) as conn:
        assert decision.read_choice(conn, f._BERTH_ID) is None
        assert (decision.read_pause(conn, f._BERTH_ID) is not None) is (snapshot == "paused")

    if snapshot == "paused":
        with pytest.raises(CloudBerthSourcePausedExn):
            _route(root, participant)
    elif len(rows) > 1:
        with pytest.raises(CloudBerthSourceAmbiguousExn):
            _route(root, participant)
    else:
        # Restoring before detection loses the sole durable record of a held
        # pause/decision. Old Git candidates alone do not prove either one.
        assert _route(root, participant).location == "loc-B"
    report, _ = f._fresh_report(root, participant)
    paused = snapshot == "paused" or len(rows) > 1
    assert (f._pause_report(root, participant) is not None) is paused
    assert f._locations(report, live=True) == rows
    assert repo.list_refs("refs/") == (snapshot_refs if scope == "whole_installation" else latest_refs)
    if paused:
        with pytest.raises(CloudBerthSourcePausedExn):
            _route(root, participant)
