"""Micro tests for TeamManager team publication and outgoing sync status (#184).

The publication witnesses are micro integration tests: they inspect state read
back through the Hub-backed remote, not local HEAD or call counts, so a
commit-after-push implementation cannot pass them. That requires MinIO — the
Hub has no localfolder storage adapter, so no Hub-backed push test can avoid it.
"""

import pathlib
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from cod_sync.protocol import (
    CodSync,
    PublicationIntegrationRequiredError,
    PublicationOutcomeUnresolvedError,
    PublicationRetryableError,
)
from cod_sync.store import CasConflictError, SmallSeaStore
from cod_sync.repo import Repo
from fastapi.testclient import TestClient
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app

_TEAM = "ProjectX"
_HUB_URL = "http://testserver"


# ---------------------------------------------------------------------------
# Local helpers (no Hub, no cloud)
# ---------------------------------------------------------------------------


def _team_sync_dir(root, participant_hex, team_name=_TEAM):
    return root / "Participants" / participant_hex / team_name / "Sync"


def _team_repo(root, participant_hex, team_name=_TEAM):
    sync_dir = _team_sync_dir(root, participant_hex, team_name)
    return Repo(sync_dir / ".git", sync_dir)


def _marker(root, participant_hex, team_name=_TEAM):
    return root / "Participants" / participant_hex / team_name / ".ss_last_push"


def _local_team(root):
    """Provision a participant with one team and no cloud storage configured."""
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.create_team(root, alice_hex, _TEAM)
    return alice_hex


def _announcement_ids(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT announcement_id FROM teammate_berth_storage_announcement"
        ).fetchall()
    return {row[0].hex() for row in rows}


# ---------------------------------------------------------------------------
# Hub + MinIO fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=None)


class _HubEnv:
    """One participant with a team whose Core berth has a cloud allocation.

    The team is created before any cloud storage exists, so create_team
    publishes no storage announcement of its own. The first announcement is
    therefore a real uncommitted Core mutation — the witness this branch needs.
    """

    def __init__(self, root, minio):
        self.root = root
        self.alice_hex = Provisioning.create_new_participant(root, "Alice")
        team = Provisioning.create_team(root, self.alice_hex, _TEAM)
        self.berth_id = bytes.fromhex(team["berth_id_hex"])

        self.backend = SmallSea.SmallSeaBackend(
            root_dir=str(root), auto_approve_sessions=True
        )
        hub_app.state.backend = self.backend
        self.http = TestClient(hub_app)

        nts_token = _open_session(self.http, "NoteToSelf", mode="passthrough")
        cloud_id = self.backend.add_cloud_location(
            nts_token, "s3", minio["endpoint"],
            access_key=minio["access_key"], secret_key=minio["secret_key"],
        )
        self.allocation = Provisioning.add_berth_cloud_allocation_by_berth_id(
            root, self.alice_hex, self.berth_id, cloud_id
        )
        self.manager = TeamManager(root, self.alice_hex, _http_client=self.http)

    @property
    def repo(self):
        return _team_repo(self.root, self.alice_hex)

    @property
    def core_db(self):
        return _team_sync_dir(self.root, self.alice_hex) / "core.db"

    @property
    def marker(self):
        return _marker(self.root, self.alice_hex)

    def announce_storage(self):
        """Write a signed team-Core row that commits nothing."""
        return self.manager.publish_teammate_berth_storage_announcement(
            _TEAM, self.berth_id, self.allocation
        )

    def published_core_db(self, dest_name):
        """Fetch what the Hub-backed remote actually holds; return the work tree."""
        dest = self.root / dest_name
        dest.mkdir(parents=True)
        repo = Repo.init(dest / ".git").with_work_tree(dest)
        store = SmallSeaStore(
            _open_session(self.http, _TEAM), base_url=_HUB_URL, client=self.http
        )
        result = CodSync(repo, store).fetch()
        repo.checkout_branch("main", start_point=result.observed_head)
        return dest, result.observed_head


def _open_session(http, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": "Alice",
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Publish Core State Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture()
def env(playground_dir, minio):
    return _HubEnv(pathlib.Path(playground_dir), minio)


# ---------------------------------------------------------------------------
# Step 2: publication carries completed but uncommitted Core state
# ---------------------------------------------------------------------------


def test_push_team_publishes_uncommitted_core_mutation(env):
    """A helper that commits nothing must still reach the published chain.

    Fails against the pre-#184 push_team, which pushed HEAD without committing
    core.db. publish_teammate_berth_storage_announcement is the witness
    precisely because it does not commit itself.
    """
    announced = env.announce_storage()
    assert announced["wrote"] is True
    assert env.repo.work_tree_paths_differ_from_head(["core.db"]) is True

    # An unrelated work-tree file must not be swept into the publication.
    (env.core_db.parent / "scratch.txt").write_text("not publication state\n")

    assert env.manager.push_team(_TEAM) == "published"

    clone_dir, _sha = env.published_core_db("clone")
    published = _announcement_ids(clone_dir / "core.db")
    assert announced["announcement_id_hex"] in published

    assert not (clone_dir / "scratch.txt").exists()
    status = {e["path"]: e["xy"] for e in env.repo.status()}
    assert status["scratch.txt"] == "??", "publication must not stage unrelated paths"


# ---------------------------------------------------------------------------
# Step 3: unchanged publication is an explicit no-op
# ---------------------------------------------------------------------------


def test_second_push_is_already_present_and_opens_no_session(env, monkeypatch):
    env.announce_storage()
    assert env.manager.push_team(_TEAM) == "published"

    head_after_first = env.repo.head()
    log_after_first = env.repo.log(limit=50)

    opened = []
    real_open = env.manager._get_or_open_session
    monkeypatch.setattr(
        env.manager,
        "_get_or_open_session",
        lambda *a, **kw: (opened.append(a), real_open(*a, **kw))[1],
    )

    assert env.manager.push_team(_TEAM) == "already_present"
    assert opened == [], "no-op publication must not open a Hub session"
    # No empty commit, and no bundle attempt that Git would reject.
    assert env.repo.head() == head_after_first
    assert env.repo.log(limit=50) == log_after_first


# ---------------------------------------------------------------------------
# Step 5: the marker identifies the exact successful publication
# ---------------------------------------------------------------------------


def test_marker_matches_the_published_main_sha(env):
    env.announce_storage()
    env.manager.push_team(_TEAM)

    _clone_dir, published_sha = env.published_core_db("clone")
    assert env.marker.read_text().strip() == published_sha
    assert env.manager.get_team_sync_status(_TEAM) == "synced"


def test_push_failure_leaves_marker_intact_and_work_preserved(env, monkeypatch):
    env.announce_storage()
    env.manager.push_team(_TEAM)
    marker_before = env.marker.read_bytes()
    head_before = env.repo.head()

    Provisioning.set_team_admission_policy(env.root, env.alice_hex, _TEAM, quorum=2)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected transport failure")

    monkeypatch.setattr(SmallSeaStore, "put_latest_link", _boom)

    with pytest.raises(RuntimeError, match="injected transport failure"):
        env.manager.push_team(_TEAM)

    assert env.marker.read_bytes() == marker_before
    new_head = env.repo.head()
    assert new_head != head_before, "the publication commit must be preserved"
    assert env.manager.get_team_sync_status(_TEAM) == "needs_push"


def test_session_open_failure_leaves_marker_intact_and_work_preserved(env, monkeypatch):
    """Step 3 moves the commit ahead of session opening; the work must survive."""
    env.announce_storage()
    env.manager.push_team(_TEAM)
    marker_before = env.marker.read_bytes()
    head_before = env.repo.head()

    Provisioning.set_team_admission_policy(env.root, env.alice_hex, _TEAM, quorum=2)

    def _denied(*_args, **_kwargs):
        raise RuntimeError("PIN denied")

    monkeypatch.setattr(env.manager, "_get_or_open_session", _denied)

    with pytest.raises(RuntimeError, match="PIN denied"):
        env.manager.push_team(_TEAM)

    assert env.marker.read_bytes() == marker_before
    assert env.repo.head() != head_before
    assert env.manager.get_team_sync_status(_TEAM) == "needs_push"


def test_initial_session_failure_remains_never_pushed(playground_dir, monkeypatch):
    """Without a successful marker, never_pushed remains the specific state."""
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    manager = TeamManager(root, alice_hex)
    repo = _team_repo(root, alice_hex)
    head_before = repo.head()

    Provisioning.set_team_admission_policy(root, alice_hex, _TEAM, quorum=2)

    def _denied(*_args, **_kwargs):
        raise RuntimeError("PIN denied")

    monkeypatch.setattr(manager, "_get_or_open_session", _denied)

    with pytest.raises(RuntimeError, match="PIN denied"):
        manager.push_team(_TEAM)

    assert repo.head() != head_before, "the publication commit must be preserved"
    assert not _marker(root, alice_hex).exists()
    assert manager.get_team_sync_status(_TEAM) == "never_pushed"


# ---------------------------------------------------------------------------
# Step 6: CAS failure contract
# ---------------------------------------------------------------------------


def test_cas_conflict_reports_the_typed_state_and_preserves_local_commit(env, monkeypatch):
    """A refused head write reaches the caller as Cod Sync's typed result.

    The stored chain still holds the head this attempt built on, so the
    attempted state is a descendant of it and nothing needs integrating: the
    invocation is retryable, and push_team neither translates it into a
    hand-written message nor touches the marker.
    """
    env.announce_storage()
    env.manager.push_team(_TEAM)
    marker_before = env.marker.read_bytes()
    head_before = env.repo.head()
    published_head = marker_before.decode().strip()

    Provisioning.set_team_admission_policy(env.root, env.alice_hex, _TEAM, quorum=2)

    def _conflict(*_args, **_kwargs):
        raise CasConflictError("etag mismatch")

    monkeypatch.setattr(SmallSeaStore, "put_latest_link", _conflict)

    with pytest.raises(PublicationRetryableError) as excinfo:
        env.manager.push_team(_TEAM)

    failure = excinfo.value
    assert isinstance(failure.cause, CasConflictError)
    assert failure.attempted_head == env.repo.head()
    assert failure.predecessor_head == published_head
    assert failure.observed_head == published_head

    assert env.marker.read_bytes() == marker_before
    assert env.repo.head() != head_before
    assert env.manager.get_team_sync_status(_TEAM) == "needs_push"


# ---------------------------------------------------------------------------
# Step 4: the web route distinguishes the two outcomes
# ---------------------------------------------------------------------------


def test_push_route_reports_already_published_on_second_push(env):
    env.announce_storage()
    web = create_app(env.root, env.alice_hex)
    web.state.manager = env.manager
    client = TestClient(web)

    first = client.post(f"/teams/{_TEAM}/push")
    assert first.status_code == 200, first.text
    assert "Pushed to cloud." in first.text

    second = client.post(f"/teams/{_TEAM}/push")
    assert second.status_code == 200, second.text
    assert "Already published." in second.text
    assert "Pushed to cloud." not in second.text


def test_push_route_offers_no_integration_action_manager_does_not_have(env, monkeypatch):
    """Divergence is reported without pointing at an operation that does not exist.

    Manager gains an integration operation in #185 and #48. Until then the
    route says what happened and what is preserved, and names no action.
    """
    env.announce_storage()
    env.manager.push_team(_TEAM)
    marker_before = env.marker.read_bytes()
    Provisioning.set_team_admission_policy(env.root, env.alice_hex, _TEAM, quorum=2)

    def _diverged(self, *_args, **_kwargs):
        raise PublicationIntegrationRequiredError(
            "the store's head diverges from local main",
            attempted_head=env.repo.head(),
            observed_head="cc" * 20,
            merge_base="dd" * 20,
            parked_ref="refs/cod-sync/parked/whatever",
        )

    monkeypatch.setattr(CodSync, "publish", _diverged)

    web = create_app(env.root, env.alice_hex)
    web.state.manager = env.manager
    client = TestClient(web)
    resp = client.post(f"/teams/{_TEAM}/push")

    assert resp.status_code == 200, resp.text
    assert "holds changes this installation does not have" in resp.text
    assert "Both histories are kept locally" in resp.text
    for absent in ("merge", "Merge", "pull", "Pull", "attempted_head"):
        assert absent not in resp.text
    assert env.marker.read_bytes() == marker_before


def test_push_route_does_not_claim_local_status_settles_an_unknown_outcome(
    env, monkeypatch
):
    env.announce_storage()
    env.manager.push_team(_TEAM)
    marker_before = env.marker.read_bytes()
    Provisioning.set_team_admission_policy(env.root, env.alice_hex, _TEAM, quorum=2)

    def _unresolved(self, *_args, **_kwargs):
        raise PublicationOutcomeUnresolvedError(
            "the head write may still take effect",
            attempted_head=env.repo.head(),
        )

    monkeypatch.setattr(CodSync, "publish", _unresolved)

    web = create_app(env.root, env.alice_hex)
    web.state.manager = env.manager
    resp = TestClient(web).post(f"/teams/{_TEAM}/push")

    assert resp.status_code == 200, resp.text
    assert "The local commit is preserved" in resp.text
    assert "A later push will observe the cloud state afresh" in resp.text
    assert "sync status" not in resp.text
    assert env.marker.read_bytes() == marker_before
    assert env.manager.get_team_sync_status(_TEAM) == "needs_push"


# ---------------------------------------------------------------------------
# Step 7: outgoing sync status
# ---------------------------------------------------------------------------


def test_status_never_pushed_without_marker(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status(_TEAM) == "never_pushed"


def test_status_never_pushed_when_repo_has_no_commits(playground_dir):
    """The no-HEAD branch runs first: `git diff HEAD` is fatal without commits."""
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    sync_dir = _team_sync_dir(root, alice_hex, "EmptyTeam")
    sync_dir.mkdir(parents=True)
    Repo.init(sync_dir / ".git")
    # A marker would otherwise be consulted; the no-HEAD branch must win.
    _marker(root, alice_hex, "EmptyTeam").write_text("0" * 40)

    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status("EmptyTeam") == "never_pushed"


def test_status_synced_when_head_marked_and_core_clean(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    _marker(root, alice_hex).write_text(_team_repo(root, alice_hex).head())

    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status(_TEAM) == "synced"


def test_status_needs_push_when_core_db_is_dirty(playground_dir):
    """set_team_admission_policy mutates core.db and commits nothing."""
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    _marker(root, alice_hex).write_text(_team_repo(root, alice_hex).head())

    Provisioning.set_team_admission_policy(root, alice_hex, _TEAM, quorum=2)

    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status(_TEAM) == "needs_push"


def test_status_ignores_unrelated_dirty_paths(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    repo = _team_repo(root, alice_hex)
    _marker(root, alice_hex).write_text(repo.head())

    sync_dir = _team_sync_dir(root, alice_hex)
    (sync_dir / "untracked.txt").write_text("scratch\n")
    (sync_dir / "staged.txt").write_text("staged\n")
    repo.stage(["staged.txt"])
    (sync_dir / ".gitattributes").write_text("# modified tracked file\n")

    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status(_TEAM) == "synced"


def test_status_needs_push_for_unpublished_commit(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _local_team(root)
    repo = _team_repo(root, alice_hex)
    _marker(root, alice_hex).write_text(repo.head())

    Provisioning.set_team_admission_policy(root, alice_hex, _TEAM, quorum=2)
    repo.commit_paths(["core.db"], "Update team Core")

    manager = TeamManager(root, alice_hex)
    assert manager.get_team_sync_status(_TEAM) == "needs_push"
