"""Micro tests for source binding and inspection/decision interleavings.

Use real SQLite, Git and Cod Sync with a local folder as transport. Only the
session and team lookup are substituted; these schedules need no providers.
"""

import contextlib
import pathlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import test_note_to_self_integration as fixtures
from cod_sync.protocol import CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.cloud_errors import CloudAllocationConflictExn
from small_sea_manager import berth_source_decision as decision
from small_sea_manager import manager as manager_module
from small_sea_note_to_self.db import attached_note_to_self_connection


@pytest.fixture
def inspection(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    participant, nts_repo = fixtures._participant(root)
    fixtures._competing_allocations(root, participant, nts_repo)
    manager_module.TeamManager(root, participant).integrate_note_to_self()
    (root / "cloud").mkdir()
    store = LocalFolderStore(root / "cloud")
    CodSync(nts_repo, store).publish()
    team_repo = Repo.init(root / "inspection" / ".git")
    state = {"core_berth_id": fixtures._BERTH_ID, "placement": "paused"}
    monkeypatch.setattr(manager_module.provisioning, "derive_team_join_state", lambda *args: state)
    monkeypatch.setattr(manager_module, "CandidateInspectionStore", lambda *args, **kwargs: store)

    def fresh_manager():
        manager = manager_module.TeamManager(root, participant)
        manager._team_repo = lambda team: team_repo
        manager._get_or_open_session = lambda team: SimpleNamespace(token="test")
        manager._candidate_announcement_status = lambda *args: "missing"
        return manager

    manager = fresh_manager()
    reviewed = manager.berth_source_status("Test")
    key = next(c["candidate_key"] for c in reviewed["candidates"] if c["location"] == "loc-A")
    return SimpleNamespace(
        root=root, participant=participant, manager=manager, fresh_manager=fresh_manager,
        repo=team_repo, publisher=nts_repo, store=store, reviewed=reviewed, key=key, head=nts_repo.head(),
    )


@pytest.mark.parametrize("field,value", [
    ("url", "http://changed.invalid"),
    ("protocol", "dropbox"),
    ("client_id", "changed-client"),
    ("path_metadata", "changed-path"),
])
@pytest.mark.parametrize("withdrawn", [False, True])
def test_inspection_refuses_account_route_changes(inspection, field, value, withdrawn):
    env = inspection
    backend = object.__new__(SmallSeaBackend)
    backend.root_dir = env.root
    session = SimpleNamespace(
        participant_id=bytes.fromhex(env.participant), berth_id=fixtures._BERTH_ID,
        mode="passthrough",
    )
    backend._lookup_session = lambda token: session
    calls = []

    def adapter_for(session, cloud):
        calls.append(cloud)
        return SimpleNamespace(download=lambda path: (True, b"bytes", "etag"))

    backend._make_storage_adapter_from_record = adapter_for
    with contextlib.closing(attached_note_to_self_connection(env.root, env.participant)) as conn:
        conn.execute(
            "INSERT INTO local.cloud_storage_credential (cloud_storage_id, access_key, secret_key) VALUES (?, ?, ?)",
            (fixtures._CLOUD_ID, "test-key", "test-secret"),
        )
        if withdrawn:
            conn.execute("DELETE FROM berth_cloud_allocation WHERE location = 'loc-A'")
        conn.commit()
    backend.inspect_berth_source_candidate("test", env.key, "latest-link.yaml")
    with contextlib.closing(attached_note_to_self_connection(env.root, env.participant)) as conn:
        # A credential refresh is allowed without changing the candidate.
        conn.execute("UPDATE local.cloud_storage_credential SET access_key = 'refreshed'")
        conn.commit()
    backend.inspect_berth_source_candidate("test", env.key, "L-example.yaml")
    assert calls[-1].access_key == "refreshed"
    with contextlib.closing(attached_note_to_self_connection(env.root, env.participant)) as conn:
        conn.execute(f"UPDATE cloud_storage SET {field} = ? WHERE id = ?", (value, fixtures._CLOUD_ID))
        conn.commit()
    with pytest.raises(CloudAllocationConflictExn):
        backend.inspect_berth_source_candidate("test", env.key, "B-example.bundle")
    assert len(calls) == 2, "the changed route reached an adapter"


@pytest.mark.parametrize("stop_after", ["create_ref_immutable", "advance_ref"])
def test_interrupted_report_is_reconstructed_before_resolution(inspection, monkeypatch, stop_after):
    env = inspection
    original = getattr(env.repo, stop_after)

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted after Git publication")

    monkeypatch.setattr(env.repo, stop_after, interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        env.manager.inspect_berth_source_candidate("Test", env.key)
    assert not any(c["observations"] for c in fixtures._pause_report(env.root, env.participant)["candidates"])

    # Resolve directly after a restart, without a status refresh repairing the
    # report first. Resolution itself must find the durable Git evidence.
    fresh = env.fresh_manager()
    result = fresh.resolve_berth_source("Test", env.key, env.reviewed["evidence_digest"])
    assert result["resolved"] is False
    assert result["reason"] == "evidence_changed"
    candidate = next(c for c in result["status"]["candidates"] if c["candidate_key"] == env.key)
    assert any(o.get("observed_head") == env.head for o in candidate["observations"])
    assert all("/observations/" in o["ref_name"] for o in candidate["observations"])
    assert result["status"]["paused"] is True
    assert fresh.berth_source_status("Test")["evidence_digest"] == result["status"]["evidence_digest"]


def test_resolution_cannot_pass_an_inspection_publishing_refs(inspection, monkeypatch):
    env = inspection
    published = threading.Event()
    release = threading.Event()
    original = env.repo.create_ref_immutable

    def hold_after_ref(*args, **kwargs):
        result = original(*args, **kwargs)
        published.set()
        assert release.wait(5), "test failed to release inspection"
        return result

    monkeypatch.setattr(env.repo, "create_ref_immutable", hold_after_ref)
    connect = decision.attached_note_to_self_connection
    main_thread = threading.get_ident()

    def impatient_resolution(*args, **kwargs):
        conn = connect(*args, **kwargs)
        if threading.get_ident() == main_thread:
            conn.execute("PRAGMA busy_timeout = 0")
        return conn

    monkeypatch.setattr(decision, "attached_note_to_self_connection", impatient_resolution)
    with ThreadPoolExecutor(max_workers=1) as pool:
        inspection_result = pool.submit(env.manager.inspect_berth_source_candidate, "Test", env.key)
        try:
            assert published.wait(5)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                env.manager.resolve_berth_source("Test", env.key, env.reviewed["evidence_digest"])
        finally:
            release.set()
        assert inspection_result.result(timeout=5)["retained"] is True
    result = env.manager.resolve_berth_source("Test", env.key, env.reviewed["evidence_digest"])
    assert result["reason"] == "evidence_changed"


def test_resolution_before_fetch_finishes_preserves_later_evidence(inspection, monkeypatch):
    env = inspection
    original = env.store.get_latest_link

    def resolve_during_fetch():
        result = env.manager.resolve_berth_source("Test", env.key, env.reviewed["evidence_digest"])
        assert result["resolved"] is True
        return original()

    monkeypatch.setattr(env.store, "get_latest_link", resolve_during_fetch)
    result = env.manager.inspect_berth_source_candidate("Test", env.key)
    assert result["retained"] is False
    assert env.repo.resolve_ref(result["observation"]["ref_name"]) == env.head
    status = env.fresh_manager().berth_source_status("Test")
    assert status["paused"] is False
    assert status["decided"]["evidence_digest"] == env.reviewed["evidence_digest"]
    assert status["evidence_digest"] != env.reviewed["evidence_digest"]
    candidate = next(c for c in status["candidates"] if c["candidate_key"] == env.key)
    assert any(o.get("observed_head") == env.head for o in candidate["observations"])
    with contextlib.closing(attached_note_to_self_connection(env.root, env.participant)) as conn:
        choice = decision.read_choice(conn, fixtures._BERTH_ID)
    assert not any(c["observations"] for c in choice["report"]["candidates"])


def test_later_fetch_does_not_erase_an_interrupted_observation(inspection, monkeypatch):
    env = inspection
    original = env.repo.advance_ref

    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted report")

    monkeypatch.setattr(env.repo, "advance_ref", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        env.manager.inspect_berth_source_candidate("Test", env.key)
    monkeypatch.setattr(env.repo, "advance_ref", original)
    fixtures._apply_local(env.root, env.participant, [fixtures._insert_team(b"\xee" * 16, "LaterWork")])
    later_head = fixtures._commit_local(env.root, env.participant, env.publisher)
    CodSync(env.publisher, env.store).publish()
    env.manager.inspect_berth_source_candidate("Test", env.key)
    status = env.fresh_manager().berth_source_status("Test")
    candidate = next(c for c in status["candidates"] if c["candidate_key"] == env.key)
    assert {o["observed_head"] for o in candidate["observations"]} == {env.head, later_head}
    assert all(env.repo.resolve_ref(o["ref_name"]) == o["observed_head"] for o in candidate["observations"])


def test_divergent_inspection_preserves_both_heads(inspection, monkeypatch):
    env = inspection
    env.manager.inspect_berth_source_candidate("Test", env.key)
    parent = env.publisher.resolve_ref("refs/heads/main^")
    sibling = fixtures._source_commit(
        env.publisher, parent, [fixtures._insert_team(b"\xdd" * 16, "DivergentWork")]
    )
    bundle = env.root / "sibling.bundle"
    env.publisher.create_bundle_from_head(bundle, sibling)
    publisher = Repo.init(env.root / "sibling" / ".git")
    publisher.import_bundle(bundle)
    publisher.create_ref_immutable("refs/heads/main", sibling)
    (env.root / "sibling-cloud").mkdir()
    store = LocalFolderStore(env.root / "sibling-cloud")
    CodSync(publisher, store).publish()
    monkeypatch.setattr(manager_module, "CandidateInspectionStore", lambda *args, **kwargs: store)

    result = env.manager.inspect_berth_source_candidate("Test", env.key)
    assert result["observation"]["disposition"] == "divergent"
    assert env.repo.resolve_ref(manager_module.berth_source_candidate_ref(env.key)) == env.head
    status = env.manager.berth_source_status("Test")
    candidate = next(c for c in status["candidates"] if c["candidate_key"] == env.key)
    assert {o["observed_head"] for o in candidate["observations"]} == {env.head, sibling}
    assert all(env.repo.resolve_ref(o["ref_name"]) == o["observed_head"] for o in candidate["observations"])
