"""Micro tests for NoteToSelf refresh, team discovery, and joined_locally semantics.

Covers:
- list_known_teams / get_team return joined_locally correctly
- device-local NoteToSelf state is not synced by refresh
- two-device same-identity: device B does not see device A's team before refresh,
  but does see it (with joined_locally=False) after refresh
- device B does not auto-create a local team clone after refresh

The two-device tests use MinIO because Hub-mediated NoteToSelf sync requires a
real cloud backend (Hub's storage adapter does not support localfolder).
"""
import pathlib
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from cod_sync.protocol import PublicationIntegrationRequiredError, parked_ref_name
from cod_sync.repo import Repo
from cod_sync.store import SmallSeaStore
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager import note_to_self_sync
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from small_sea_note_to_self.db import note_to_self_sync_db_path



def _note_to_self_repo(root, participant_hex):
    repo_dir = pathlib.Path(root) / "Participants" / participant_hex / "NoteToSelf" / "Sync"
    return Repo(repo_dir / ".git", repo_dir)


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# joined_locally flag tests (no Hub or cloud needed)
# ---------------------------------------------------------------------------


def test_list_known_teams_joined_locally_for_local_team(playground_dir):
    """A team that exists in NoteToSelf AND has a local clone reports joined_locally=True."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    # create_team writes the team to both NoteToSelf and local FS
    manager = TeamManager(root, alice_hex)
    result = manager.create_team("CoolProject")
    team_id_hex = result["team_id_hex"]

    teams = manager.list_known_teams()
    cool = next((t for t in teams if t["name"] == "CoolProject"), None)
    assert cool is not None
    assert cool["joined_locally"] is True


def test_list_known_teams_joined_locally_false_for_nts_only_row(playground_dir):
    """A team row in shared NoteToSelf without a local clone reports joined_locally=False."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")

    # Insert a fake team into shared NoteToSelf DB directly (no local clone)
    fake_team_id = bytes(16)  # all-zeros, won't conflict with real UUIDs
    nts_db = note_to_self_sync_db_path(root, alice_hex)
    with sqlite3.connect(nts_db) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (fake_team_id, "GhostTeam", bytes(16)),
        )
        conn.commit()

    manager = TeamManager(root, alice_hex)
    teams = manager.list_known_teams()
    ghost = next((t for t in teams if t["name"] == "GhostTeam"), None)
    assert ghost is not None
    assert ghost["joined_locally"] is False


def test_get_team_not_joined_locally_returns_guard_dict(playground_dir):
    """get_team on a NoteToSelf-only team returns a guard dict without crashing."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")

    # Insert fake team row with no local clone
    nts_db = note_to_self_sync_db_path(root, alice_hex)
    with sqlite3.connect(nts_db) as conn:
        conn.execute(
            "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
            (bytes(16), "GhostTeam", bytes(16)),
        )
        conn.commit()

    manager = TeamManager(root, alice_hex)
    detail = manager.get_team("GhostTeam")
    assert detail["name"] == "GhostTeam"
    assert detail["joined_locally"] is False
    # guard dict must not contain teammates/invitations from a non-existent local DB
    assert detail["teammates"] == []
    assert detail["invitations"] == []


def test_get_team_joined_locally_returns_full_detail(playground_dir):
    """get_team on a fully joined team returns joined_locally=True with teammate data."""
    root = pathlib.Path(playground_dir)
    alice_hex = create_new_participant(root, "Alice")
    manager = TeamManager(root, alice_hex)
    manager.create_team("RealProject")

    detail = manager.get_team("RealProject")
    assert detail["joined_locally"] is True
    assert len(detail["teammates"]) == 1


# ---------------------------------------------------------------------------
# Two-device same-identity refresh test (uses Hub + MinIO)
# ---------------------------------------------------------------------------


def _wire_device_b_credentials(root_b, alice_hex, minio):
    """After bootstrap, connect device B's credentials to the inherited account.

    During bootstrap device B clones the shared NoteToSelf (getting the
    cloud_storage URL row), but has no matching credential row. Device B
    selects that inherited account through the Manager's own listing and
    connects credentials to it, which is what a user does (issue #237).
    """
    manager_b = TeamManager(root_b, alice_hex)
    accounts = manager_b.list_cloud_storage()
    assert accounts, "No cloud_storage row found in device B's shared NoteToSelf"
    manager_b.connect_cloud_storage_credentials(
        accounts[0]["id"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )


def test_refresh_note_to_self_two_device_team_discovery(playground_dir, minio_server_gen):
    """Device B discovers a team created on device A after refresh; not before.

    Uses MinIO + Hub TestClient. Hub backend is swapped between installations
    following the pattern in test_identity_bootstrap.py.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    # --- Device A: create identity, configure S3, push NoteToSelf ---
    alice_hex = create_new_participant(root_a, "Alice")

    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    # Open NoteToSelf session on device A so the Hub knows the berth
    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    nts_session_a = backend_a._lookup_session(nts_token_a)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a,
        alice_hex,
        nts_session_a.berth_id,
        cloud_storage_id,
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)

    # Device A creates a team and pushes NoteToSelf to S3
    manager_a.create_team("SharedProject")
    manager_a.push_note_to_self()

    # --- Bootstrap device B ---
    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    # bootstrap_existing_identity uses the Hub bootstrap transport for S3
    from small_sea_manager.manager import bootstrap_existing_identity as _bootstrap
    _bootstrap(root_b, welcome["welcome_bundle"], _http_client=http_a)

    # Sanity: device B has shared NoteToSelf
    shared_b = note_to_self_sync_db_path(root_b, alice_hex)
    assert shared_b.exists()

    # Wire S3 credentials for device B (production: user configures own cloud)
    _wire_device_b_credentials(root_b, alice_hex, minio)

    # --- Device A creates a SECOND team AFTER bootstrap ---
    manager_a.create_team("PostBootstrapProject")
    manager_a.push_note_to_self()

    # --- Switch Hub to device B ---
    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)

    # Before refresh: device B does NOT see PostBootstrapProject
    teams_before = manager_b.list_known_teams()
    team_names_before = {t["name"] for t in teams_before}
    assert "PostBootstrapProject" not in team_names_before, (
        "Device B must not see PostBootstrapProject before refresh"
    )

    # Device B must not have a local clone
    assert not (root_b / "Participants" / alice_hex / "PostBootstrapProject").exists()

    # --- Refresh ---
    refresh_result = manager_b.refresh_note_to_self()
    assert "teams" in refresh_result

    # After refresh: device B sees PostBootstrapProject
    teams_after = manager_b.list_known_teams()
    team_names_after = {t["name"] for t in teams_after}
    assert "PostBootstrapProject" in team_names_after, (
        "Device B must see PostBootstrapProject after refresh"
    )

    # S5: discovery is not team join
    assert not (root_b / "Participants" / alice_hex / "PostBootstrapProject").exists(), (
        "Device B must not auto-create a local team clone after discovery"
    )

    discovered = next(t for t in teams_after if t["name"] == "PostBootstrapProject")
    assert discovered["joined_locally"] is False

    # get_team guard: returns clear not-joined state
    detail = manager_b.get_team("PostBootstrapProject")
    assert detail["joined_locally"] is False
    assert detail["teammates"] == []


def test_refresh_does_not_sync_device_local_state(playground_dir, minio_server_gen):
    """Refresh must not move device-local secrets into the shared NoteToSelf DB."""
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    nts_session_a = backend_a._lookup_session(nts_token_a)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a,
        alice_hex,
        nts_session_a.berth_id,
        cloud_storage_id,
    )
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.create_team("MyTeam")
    manager_a.push_note_to_self()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    from small_sea_manager.manager import bootstrap_existing_identity as _bootstrap
    _bootstrap(root_b, welcome["welcome_bundle"], _http_client=http_a)
    _wire_device_b_credentials(root_b, alice_hex, minio)

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)

    manager_b.refresh_note_to_self()

    # Shared NoteToSelf DB must not contain device-local tables
    shared_b = note_to_self_sync_db_path(root_b, alice_hex)
    with sqlite3.connect(shared_b) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    local_only_tables = {
        "team_sender_key",
        "peer_sender_key",
        "team_device_key_secret",
        "note_to_self_sync_state",
    }
    leaked = local_only_tables & tables
    assert not leaked, f"Device-local tables leaked into shared NoteToSelf: {leaked}"




def test_unchanged_note_to_self_publication_invents_no_signal(playground_dir, minio_server_gen):
    """A no-op publication uploads nothing, so it notifies nobody.

    The adopted baseline tracks notifications this device has caught up with.
    Advancing it after a publication that never happened would make the next
    real push look already-adopted and be skipped.
    """
    minio = minio_server_gen()
    root = pathlib.Path(playground_dir) / "install"
    root.mkdir()

    alice_hex = create_new_participant(root, "Alice")
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    nts_token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    nts_session = backend._lookup_session(nts_token)
    berth_id = nts_session.berth_id
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root, alice_hex, berth_id, cloud_storage_id
    )

    manager = TeamManager(root, alice_hex, _http_client=http)
    manager.create_team("SharedProject")
    manager.push_note_to_self()
    after_real_push = Provisioning.get_note_to_self_adopted_signal_count(
        root, alice_hex, berth_id
    )

    # Nothing changed locally, so this publication is a no-op.
    manager.push_note_to_self()
    assert (
        Provisioning.get_note_to_self_adopted_signal_count(root, alice_hex, berth_id)
        == after_real_push
    )

    # A genuine change advances it again.
    manager.create_team("SecondProject")
    manager.push_note_to_self()
    assert (
        Provisioning.get_note_to_self_adopted_signal_count(root, alice_hex, berth_id)
        == after_real_push + 1
    )




def _diverge_two_devices(workspace, minio):
    """Bring two real installations of one identity to a parked divergence.

    Device A publishes SharedProject then OnlyOnA. Device B bootstraps in
    between, commits OnlyOnB without refreshing, and its push is refused with
    A's head parked. Returns everything the callers need to continue from
    there; the Hub backend is left pointing at device B.
    """
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    nts_session_a = backend_a._lookup_session(nts_token_a)
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a, alice_hex, nts_session_a.berth_id, cloud_storage_id
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.create_team("SharedProject")
    manager_a.push_note_to_self()
    shared_head = _note_to_self_repo(root_a, alice_hex).head()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)
    _wire_device_b_credentials(root_b, alice_hex, minio)

    # Device A publishes a team device B has not seen.
    manager_a.create_team("OnlyOnA")
    manager_a.push_note_to_self()
    cloud_head = _note_to_self_repo(root_a, alice_hex).head()

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)
    nts_token_b = _open_session(http_b, "Alice", "NoteToSelf", mode="passthrough")
    berth_id_b = backend_b._lookup_session(nts_token_b).berth_id

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    repo_b = _note_to_self_repo(root_b, alice_hex)
    # Bootstrap leaves device B on its own commit, but still on A's history.
    assert repo_b.is_ancestor(shared_head, repo_b.head())
    # push_note_to_self seeds an absent adopted-count row from the Hub's current
    # self-signal count, which device A's pushes have already advanced. Seed it
    # here so the post-push assertion has a baseline that does not depend on how
    # many of A's signals this Hub has recorded.
    assert (
        Provisioning.get_note_to_self_adopted_signal_count(root_b, alice_hex, berth_id_b)
        is None
    )
    Provisioning.set_note_to_self_adopted_signal_count(root_b, alice_hex, berth_id_b, 0)

    # Device B commits its own team without refreshing first.
    manager_b.create_team("OnlyOnB")
    with pytest.raises(PublicationIntegrationRequiredError) as excinfo:
        manager_b.push_note_to_self()

    return {
        "alice_hex": alice_hex,
        "root_a": root_a,
        "root_b": root_b,
        "http_a": http_a,
        "http_b": http_b,
        "backend_a": backend_a,
        "backend_b": backend_b,
        "manager_a": manager_a,
        "manager_b": manager_b,
        "repo_b": repo_b,
        "shared_head": shared_head,
        "cloud_head": cloud_head,
        "berth_id_b": berth_id_b,
        "failure": excinfo.value,
        "adopted_before": 0,
    }


def test_divergent_note_to_self_push_reports_integration_required(
    playground_dir, minio_server_gen
):
    """Two real installations of one identity witness publication divergence.

    Device B bootstraps, device A publishes a team B has not seen, and B then
    commits its own team without refreshing. B's push is not behind the cloud
    head and cannot replace it, so Cod Sync parks the observed head and hands
    the choice to the application. Nothing device-local moves: B keeps its own
    commit, and its adopted signal count stays where it was.
    """
    minio = minio_server_gen()
    scene = _diverge_two_devices(pathlib.Path(playground_dir), minio)
    alice_hex = scene["alice_hex"]
    root_b = scene["root_b"]
    repo_b = scene["repo_b"]
    failure = scene["failure"]

    local_head = repo_b.head()
    assert failure.attempted_head == local_head
    assert failure.observed_head == scene["cloud_head"]
    # Divergence, not a fast-forward in either direction: the reported base is
    # a common ancestor and neither head reaches the other.
    assert repo_b.is_ancestor(failure.merge_base, local_head)
    assert repo_b.is_ancestor(failure.merge_base, scene["cloud_head"])
    assert not repo_b.is_ancestor(local_head, scene["cloud_head"])
    assert not repo_b.is_ancestor(scene["cloud_head"], local_head)

    # The competing head survives the process that observed it.
    parked = parked_ref_name(failure.observed_link_uid)
    assert failure.parked_ref == parked
    assert _note_to_self_repo(root_b, alice_hex).resolve_ref(parked) == scene["cloud_head"]

    # Divergence changes nothing device-local.
    assert repo_b.head() == local_head
    # A publication that never landed must not advance the adopted baseline.
    assert (
        Provisioning.get_note_to_self_adopted_signal_count(
            root_b, alice_hex, scene["berth_id_b"]
        )
        == scene["adopted_before"]
    )




def test_integrated_note_to_self_round_trips_back_to_the_other_device(
    playground_dir, minio_server_gen, monkeypatch
):
    """The whole point of the branch: two devices' histories actually combine.

    Device B integrates the head its own push was refused for, publishes the
    result, and device A refreshes and sees every team. The round trip through
    cloud storage is what makes the claim real rather than locally plausible.
    """
    minio = minio_server_gen()
    scene = _diverge_two_devices(pathlib.Path(playground_dir), minio)
    alice_hex = scene["alice_hex"]
    root_b = scene["root_b"]
    repo_b = scene["repo_b"]
    local_head_b = repo_b.head()
    cloud_head = scene["cloud_head"]

    # A freshly constructed Manager finds the outstanding head in refs alone.
    fresh_b = TeamManager(root_b, alice_hex, _http_client=scene["http_b"])
    assert [source.head_sha for source in fresh_b.note_to_self_conflict_status()] == [
        cloud_head
    ]

    result = fresh_b.integrate_note_to_self()

    [outcome] = result.outcomes
    assert outcome.outcome == "integrated", outcome
    assert outcome.kind == "divergent"
    assert {t["name"] for t in fresh_b.list_known_teams()} >= {
        "SharedProject", "OnlyOnA", "OnlyOnB",
    }

    # The merge publishes, and the stored head now descends from both sides.
    # No SQLite writer reservation may be held across Hub I/O: the Hub is the
    # other writer, so holding one there would deadlock a real installation.
    original_put = SmallSeaStore.put_latest_link
    probes = []

    def put_latest_link_unlocked(self, *args, **kwargs):
        probes.append(True)
        probe = sqlite3.connect(str(note_to_self_sync_db_path(root_b, alice_hex)), timeout=0)
        try:
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
        finally:
            probe.close()
        return original_put(self, *args, **kwargs)

    monkeypatch.setattr(SmallSeaStore, "put_latest_link", put_latest_link_unlocked)
    fresh_b.push_note_to_self()
    monkeypatch.undo()
    assert probes, "the push never reached the store, so the probe proved nothing"
    published = repo_b.head()
    assert repo_b.is_ancestor(local_head_b, published)
    assert repo_b.is_ancestor(cloud_head, published)

    # Device A refreshes and sees the team it never had.
    app.state.backend = scene["backend_a"]
    manager_a = scene["manager_a"]
    refreshed = manager_a.refresh_note_to_self()
    assert refreshed["integration"].outcome == "integrated"
    assert {t["name"] for t in manager_a.list_known_teams()} >= {
        "SharedProject", "OnlyOnA", "OnlyOnB",
    }
    assert _note_to_self_repo(scene["root_a"], alice_hex).is_ancestor(
        published, "HEAD"
    )




def test_refresh_parks_the_fetched_head_before_touching_the_database(
    playground_dir, minio_server_gen, monkeypatch
):
    """A crash during adoption leaves a locally recoverable source.

    Cod Sync's fetch imports objects but creates no ref of its own, so if the
    Manager did not park the fetched head first, a failure during adoption
    would leave nothing durable naming what had been observed.
    """
    minio = minio_server_gen()
    scene = _diverge_two_devices(pathlib.Path(playground_dir), minio)
    alice_hex = scene["alice_hex"]
    root_b = scene["root_b"]
    repo_b = scene["repo_b"]
    local_head_b = repo_b.head()

    # Forget the head the refused publication parked, so only the refresh's own
    # ref can explain what the next process finds.
    repo_b._run(["update-ref", "-d", scene["failure"].parked_ref])
    assert TeamManager(root_b, alice_hex, _http_client=scene["http_b"]).note_to_self_conflict_status() == []

    def die(*args, **kwargs):
        raise RuntimeError("process died at the start of adoption")

    monkeypatch.setattr(note_to_self_sync, "adopt_source", die)
    manager_b = TeamManager(root_b, alice_hex, _http_client=scene["http_b"])
    with pytest.raises(RuntimeError):
        manager_b.refresh_note_to_self()
    monkeypatch.undo()

    assert repo_b.head() == local_head_b
    # A fresh Manager finds the fetched source with no Hub contact at all.
    offline = TeamManager(root_b, alice_hex, _http_client=scene["http_b"])
    assert [s.head_sha for s in offline.note_to_self_conflict_status()] == [
        scene["cloud_head"]
    ]
    assert (
        Provisioning.get_note_to_self_adopted_signal_count(
            root_b, alice_hex, scene["berth_id_b"]
        )
        == scene["adopted_before"]
    )
