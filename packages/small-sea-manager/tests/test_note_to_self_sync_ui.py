"""Micro tests for the Manager's NoteToSelf sync card and its three routes.

Transport is stubbed: what is under test is what the card offers, what the
routes enforce, and what they say about a stored history. Whether a push
actually reaches cloud storage is covered by test_note_to_self_refresh.py.
"""

import contextlib
import pathlib
import sqlite3
import subprocess

from cod_sync.protocol import PublicationIntegrationRequiredError, parked_ref_name
from cod_sync.repo import Repo
from fastapi.testclient import TestClient

from small_sea_manager import note_to_self_sync
from small_sea_manager.manager import TeamManager
from small_sea_manager.provisioning import create_new_participant
from small_sea_manager.web import create_app
from small_sea_note_to_self.db import note_to_self_sync_db_path

_NTS = "NoteToSelf"
_PASSTHROUGH = "passthrough"


def _install(root):
    """Create an installation and return (participant_hex, manager, client, repo)."""
    participant_hex = create_new_participant(root, "Alice")
    app = create_app(str(root), participant_hex)
    manager = TeamManager(root, participant_hex)
    app.state.manager = manager
    repo_dir = root / "Participants" / participant_hex / _NTS / "Sync"
    return participant_hex, manager, TestClient(app), Repo(repo_dir / ".git", repo_dir)


def _connect(manager):
    """Mark the passthrough Hub session active without contacting a Hub."""
    manager.set_session(_NTS, "test-token", mode=_PASSTHROUGH)


def _park_source(root, participant_hex, repo, statements, uid="uid-a"):
    """Park a stored head whose core.db is HEAD's with statements applied."""
    import tempfile

    base = repo.head()
    with tempfile.TemporaryDirectory() as work:
        db_path = pathlib.Path(work) / "core.db"
        repo.blob_at(base, "core.db", db_path)
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            for sql, args in statements:
                conn.execute(sql, args)
            conn.commit()
        blob = subprocess.run(
            ["git", "--git-dir", str(repo.git_dir), "hash-object", "-w", str(db_path)],
            capture_output=True, check=True, text=True,
        ).stdout.strip()
    tree = subprocess.run(
        ["git", "--git-dir", str(repo.git_dir), "mktree", "-z"],
        input=f"100644 blob {blob}\tcore.db\0".encode(),
        capture_output=True, check=True,
    ).stdout.decode().strip()
    sha = repo.commit_tree(tree, [base], "stored history")
    ref = parked_ref_name(uid)
    repo._run(["update-ref", ref, sha])
    return ref, sha


def _insert_team(team_id, name):
    return (
        "INSERT INTO team (id, name, self_in_team) VALUES (?, ?, ?)",
        (team_id, name, b"\x01" * 16),
    )


def _apply_local(root, participant_hex, statements):
    db = note_to_self_sync_db_path(root, participant_hex)
    with contextlib.closing(sqlite3.connect(str(db))) as conn:
        for sql, args in statements:
            conn.execute(sql, args)
        conn.commit()


def _assert_fragment(response):
    assert response.status_code == 200, response.text
    assert "<html" not in response.text.lower()
    assert '<div id="note-to-self-sync">' in response.text


# ---------------------------------------------------------------------------
# The card on an ordinary page load
# ---------------------------------------------------------------------------


def test_index_renders_the_card_and_offers_integration_from_refs_alone(playground_dir):
    """A restarted Manager offers the integration with no Hub contact."""
    root = pathlib.Path(playground_dir)
    participant_hex, _manager, client, repo = _install(root)
    _ref, sha = _park_source(root, participant_hex, repo, [_insert_team(b"\xaa" * 16, "OnlyOnA")])

    # A second, freshly constructed app over the same installation.
    fresh = create_app(str(root), participant_hex)
    fresh.state.manager = TeamManager(root, participant_hex)
    page = TestClient(fresh).get("/")

    assert page.status_code == 200
    assert '<div id="note-to-self-sync">' in page.text
    assert "Integrate stored history" in page.text
    assert sha[:12] in page.text


def test_index_says_nothing_is_outstanding_when_no_head_is_parked(playground_dir):
    root = pathlib.Path(playground_dir)
    _participant_hex, _manager, client, _repo = _install(root)

    page = client.get("/")

    assert "No stored NoteToSelf history is waiting" in page.text
    assert "Integrate stored history" not in page.text


def test_the_card_never_claims_a_sibling_device_authored_the_source(playground_dir):
    """Cod Sync proves structure and ancestry, not authorship (#190)."""
    root = pathlib.Path(playground_dir)
    participant_hex, _manager, client, repo = _install(root)
    _park_source(root, participant_hex, repo, [_insert_team(b"\xaa" * 16, "OnlyOnA")])

    page = client.get("/")

    assert "configured storage" in page.text
    assert "does not establish which device wrote" in page.text
    for claim in ("your other device", "another device of yours", "written by your"):
        assert claim not in page.text.lower()


# ---------------------------------------------------------------------------
# Hub gating
# ---------------------------------------------------------------------------


def test_without_a_session_push_and_refresh_are_unavailable_but_integration_is_not(
    playground_dir,
):
    root = pathlib.Path(playground_dir)
    participant_hex, manager, client, repo = _install(root)
    _park_source(root, participant_hex, repo, [_insert_team(b"\xaa" * 16, "OnlyOnA")])
    attempts = []
    manager.push_note_to_self = lambda: attempts.append("push")
    manager.refresh_note_to_self = lambda: attempts.append("refresh")

    page = client.get("/")
    assert "Connect to Hub above to push or refresh" in page.text

    for route in ("/note-to-self/push", "/note-to-self/refresh"):
        response = client.post(route)
        _assert_fragment(response)
        assert "Connect to Hub above before pushing or refreshing" in response.text
    assert attempts == [], "a gated route must not open transport"

    integrate = client.post("/note-to-self/integrate")
    _assert_fragment(integrate)
    assert "Integrated" in integrate.text
    assert "OnlyOnA" in integrate.text


def test_with_a_session_the_routes_call_the_manager_operations(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, manager, client, repo = _install(root)
    _connect(manager)
    calls = []
    manager.push_note_to_self = lambda: calls.append("push")
    manager.refresh_note_to_self = lambda: (
        calls.append("refresh")
        or {
            "berth_id": "00",
            "adopted_count": 1,
            "integration": note_to_self_sync.SourceOutcome(
                "refs/cod-sync/parked/uid-a", "a" * 40, "already_contained"
            ),
            "teams": [],
        }
    )

    push = client.post("/note-to-self/push")
    _assert_fragment(push)
    assert "Pushed NoteToSelf to cloud." in push.text

    refresh = client.post("/note-to-self/refresh")
    _assert_fragment(refresh)
    assert "was already part of this device" in refresh.text
    assert calls == ["push", "refresh"]


def test_a_divergent_push_points_at_the_integration_that_now_exists(playground_dir):
    root = pathlib.Path(playground_dir)
    _participant_hex, manager, client, _repo = _install(root)
    _connect(manager)

    def refuse():
        raise PublicationIntegrationRequiredError(
            "diverged", attempted_head="a" * 40, observed_head="b" * 40
        )

    manager.push_note_to_self = refuse

    response = client.post("/note-to-self/push")

    _assert_fragment(response)
    assert "Integrate the stored history, then push again." in response.text


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def test_a_successful_integration_replaces_the_team_sidebar_out_of_band(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, _manager, client, repo = _install(root)
    _park_source(root, participant_hex, repo, [_insert_team(b"\xaa" * 16, "DiscoveredTeam")])

    response = client.post("/note-to-self/integrate")

    _assert_fragment(response)
    assert '<div id="sidebar-teams" hx-swap-oob="true">' in response.text
    assert "DiscoveredTeam" in response.text


def test_a_refusal_leaves_the_sidebar_alone(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, _manager, client, repo = _install(root)
    _park_source(root, participant_hex, repo, [("PRAGMA user_version = 3", ())])

    response = client.post("/note-to-self/integrate")

    _assert_fragment(response)
    assert "hx-swap-oob" not in response.text


def test_a_semantic_refusal_names_the_table_key_and_kind(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex, manager, client, repo = _install(root)
    shared_team = b"\x5a" * 16
    _apply_local(root, participant_hex, [_insert_team(shared_team, "Shared")])
    note_to_self_sync.commit_core_db(root, participant_hex, repo, "shared team")
    _park_source(
        root,
        participant_hex,
        repo,
        [("UPDATE team SET name = 'RenamedByA' WHERE id = ?", (shared_team,))],
    )
    _apply_local(
        root,
        participant_hex,
        [("UPDATE team SET name = 'RenamedByB' WHERE id = ?", (shared_team,))],
    )
    note_to_self_sync.commit_core_db(root, participant_hex, repo, "local rename")

    response = client.post("/note-to-self/integrate")

    _assert_fragment(response)
    assert "team" in response.text
    assert shared_team.hex() in response.text
    assert "update/update" in response.text
    assert "Nothing was changed" in response.text
    # The offer survives the refusal.
    assert "Integrate stored history" in response.text


def test_integration_reports_when_nothing_is_outstanding(playground_dir):
    root = pathlib.Path(playground_dir)
    _participant_hex, _manager, client, _repo = _install(root)

    response = client.post("/note-to-self/integrate")

    _assert_fragment(response)
    assert "No stored NoteToSelf history is waiting to be integrated." in response.text
