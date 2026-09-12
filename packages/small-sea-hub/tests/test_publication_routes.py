"""Micro tests for the protected read entry points.

Real provisioning, real sessions, real crypto, real HTTP. Only placement and
provider I/O are substituted, so the bytes a route receives can be swapped the
way a misbehaving or compromised provider would swap them. The publication
check itself is never mocked out.

Transcript details belong to the shared boundary tests; what these check is
that each entry point resolves the right context and the right expected
publisher, and that a refusal reaches the client as a refusal.
"""

import base64
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cod_sync.protocol import CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from fastapi.testclient import TestClient

from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.server import app
from small_sea_manager import provisioning as p
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    load_team_sender_key,
)

OBJECT_PATH = "chains/latest-link.yaml"
OTHER_PATH = "chains/other-link.yaml"


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """Keep disposable provisioning repos independent of personal Git settings."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Publication tests")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tests@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Publication tests")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tests@example.invalid")
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    )


def owners(db_path):
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT device_key_id, teammate_id FROM team_device"))


@pytest.fixture()
def team(tmp_path):
    """Real team creation and admission over a local folder.

    Alice is the inviter; Bob has fetched the proposal snapshot but his own
    accepted ownership row has not reached it yet, which is the ordinary
    intermediate state of admission rather than a broken projection.
    """
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    alice = p.create_new_participant(tmp_path, "Alice")
    bob = p.create_new_participant(tmp_path, "Bob")
    p.add_cloud_storage(tmp_path, alice, protocol="localfolder", url=str(cloud))
    created = p.create_team(tmp_path, alice, "ProjectX")
    p.register_app_for_participant(tmp_path, alice, "OtherApp")
    p.activate_app_for_team(tmp_path, alice, "ProjectX", "OtherApp")
    token = p.create_invitation(
        tmp_path,
        alice,
        "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    alice_sync = tmp_path / "Participants" / alice / "ProjectX" / "Sync"
    CodSync(Repo(alice_sync / ".git", alice_sync), LocalFolderStore(str(cloud))).publish()
    acceptance = p.accept_invitation(
        tmp_path, bob, token, inviter_store=LocalFolderStore(str(cloud))
    )
    p.complete_invitation_acceptance(tmp_path, alice, "ProjectX", acceptance)

    backend = SmallSeaBackend(root_dir=tmp_path)
    team_id = bytes.fromhex(created["team_id_hex"])
    alice_db = alice_sync / "core.db"
    bob_sender = load_team_sender_key(device_local_db_path(tmp_path, bob), team_id)
    return SimpleNamespace(
        root=tmp_path,
        cloud=cloud,
        backend=backend,
        alice=alice,
        bob=bob,
        team_id=team_id,
        alice_teammate=bytes.fromhex(created["teammate_id_hex"]),
        bob_teammate=owners(alice_db)[bob_sender.sender_device_key_id],
        alice_db=alice_db,
        bob_db=tmp_path / "Participants" / bob / "ProjectX" / "Sync" / "core.db",
        bob_sender=bob_sender,
    )


def open_session(
    env, nickname, team_name="ProjectX", app_name="SmallSeaCollectiveCore", mode=None
):
    pending, pin = env.backend.request_session(
        nickname, app_name, team_name, "Smoke Tests", mode=mode
    )
    return env.backend.confirm_session(pending, pin).hex()


@pytest.fixture()
def routed(team, monkeypatch):
    """Substitute provider I/O only, and record what each route asked for.

    `stored` is the object store the routes read from and write to; a test
    moves bytes between its keys to stand in for a provider serving the wrong
    object.
    """
    env = team
    stored = {}
    requested = []

    def upload_overwrite(path, data):
        stored[path] = data
        return True, "fixture-etag", ""

    def download(path):
        requested.append(path)
        if path not in stored:
            return False, None, SimpleNamespace(absent=True)
        return True, stored[path], "fixture-etag"

    adapter = SimpleNamespace(
        upload_overwrite=upload_overwrite,
        upload_if_match=lambda path, data, etag: upload_overwrite(path, data),
        download=download,
    )
    monkeypatch.setattr(env.backend, "_resolve_berth_cloud_or_raise", lambda s: object())
    monkeypatch.setattr(env.backend, "_require_own_storage_announcement", lambda s, c: None)
    monkeypatch.setattr(
        env.backend, "_make_materialized_storage_adapter", lambda s, c: adapter
    )
    monkeypatch.setattr(
        env.backend,
        "_download_peer_file",
        lambda token, teammate, path: download(path),
    )
    monkeypatch.setattr(app.state, "backend", env.backend, raising=False)
    env.stored = stored
    env.requested = requested
    env.http = TestClient(app)
    return env


def get(env, token, route, **params):
    return env.http.get(
        route, params=params, headers={"Authorization": f"Bearer {token}"}
    )


def put(env, token, path, data):
    return env.http.post(
        "/cloud_file",
        json={"path": path, "data": base64.b64encode(data).decode()},
        headers={"Authorization": f"Bearer {token}"},
    )


# --- Own reads ---


def test_an_own_read_accepts_only_its_own_object(routed):
    env = routed
    token = open_session(env, "Alice")
    assert put(env, token, OBJECT_PATH, b"Alice's object").status_code == 200
    assert get(env, token, "/cloud_file", path=OBJECT_PATH).status_code == 200

    # The provider serves the same valid publication under a different key.
    env.stored[OTHER_PATH] = env.stored[OBJECT_PATH]
    response = get(env, token, "/cloud_file", path=OTHER_PATH)

    assert response.status_code == 502
    assert response.json()["error"] == "publication_not_authentic"
    assert response.json()["reason"] == "context_mismatch"
    assert "data" not in response.json()


@pytest.mark.parametrize("iteration", [-1, 2**64])
def test_invalid_iteration_is_a_publication_rejection(routed, iteration):
    env = routed
    token = open_session(env, "Alice")
    assert put(env, token, OBJECT_PATH, b"control").status_code == 200
    assert get(env, token, "/cloud_file", path=OBJECT_PATH).status_code == 200
    original = env.stored[OBJECT_PATH]
    message = json.loads(original)
    device_id = bytes.fromhex(message["sender_device_key_id"])
    local_db = device_local_db_path(env.root, env.alice)
    before = load_peer_sender_key(local_db, env.team_id, device_id)
    message["iteration"] = iteration
    env.stored[OBJECT_PATH] = json.dumps(message).encode()

    response = get(env, token, "/cloud_file", path=OBJECT_PATH)

    assert response.status_code == 502
    assert response.json()["error"] == "publication_not_authentic"
    assert response.json()["reason"] == "invalid_iteration"
    assert "data" not in response.json()
    assert load_peer_sender_key(local_db, env.team_id, device_id) == before
    env.stored[OBJECT_PATH] = original
    assert get(env, token, "/cloud_file", path=OBJECT_PATH).status_code == 200


def test_an_own_read_rejects_another_berth_of_the_same_team(routed):
    env = routed
    core = open_session(env, "Alice")
    other_berth = open_session(env, "Alice", app_name="OtherApp")
    assert put(env, core, OBJECT_PATH, b"Alice's Core object").status_code == 200

    response = get(env, other_berth, "/cloud_file", path=OBJECT_PATH)

    assert response.status_code == 502
    assert response.json()["reason"] == "context_mismatch"
    # The control: the berth it was published for still reads it.
    assert get(env, core, "/cloud_file", path=OBJECT_PATH).status_code == 200


def test_exact_logical_path_strings_survive_the_http_boundary(routed):
    env = routed
    token = open_session(env, "Alice")
    paths = [
        "percent%2Fname",
        "percent/name",
        "plus+name",
        "plus name",
        "café",
        "café",
    ]
    for path in paths:
        assert put(env, token, path, f"object at {path}".encode()).status_code == 200
    for path in paths:
        response = get(env, token, "/cloud_file", path=path)
        assert response.status_code == 200
        assert base64.b64decode(response.json()["data"]) == f"object at {path}".encode()

    # Two strings a provider might treat as aliases are two different objects.
    env.stored["café"] = env.stored["café"]
    assert get(env, token, "/cloud_file", path="café").status_code == 502


# --- Peer reads ---


def test_a_peer_read_expects_the_teammate_the_request_named(routed):
    env = routed
    alice_token = open_session(env, "Alice")
    bob_token = open_session(env, "Bob")
    assert put(env, alice_token, OBJECT_PATH, b"Alice's object").status_code == 200

    accepted = get(
        env,
        bob_token,
        "/peer_cloud_file",
        path=OBJECT_PATH,
        teammate_id=env.alice_teammate.hex(),
    )
    assert accepted.status_code == 200
    assert base64.b64decode(accepted.json()["data"]) == b"Alice's object"

    # The same bytes, offered as another teammate's publication.
    refused = get(
        env,
        bob_token,
        "/peer_cloud_file",
        path=OBJECT_PATH,
        teammate_id=env.bob_teammate.hex(),
    )
    assert refused.status_code == 502
    assert refused.json()["reason"] == "unexpected_publisher"
    assert "data" not in refused.json()


# --- Retained candidate inspection ---


def test_candidate_inspection_applies_the_own_read_contract(routed, monkeypatch):
    env = routed
    token = open_session(env, "Alice")
    assert put(env, token, OBJECT_PATH, b"Alice's object").status_code == 200

    session = env.backend._lookup_session(token)
    with sqlite3.connect(
        str(env.root / "Participants" / env.alice / "NoteToSelf" / "Sync" / "core.db")
    ) as conn:
        account = conn.execute(
            "SELECT id, protocol, url, client_id, path_metadata FROM cloud_storage"
        ).fetchone()
    route = {
        "allocation_id": (b"\x01" * 16).hex(),
        "cloud_storage_id": account[0].hex(),
        "protocol": account[1],
        "url": account[2],
        "location": "retained-candidate-location",
        "client_id": account[3],
        "path_metadata": account[4],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(
        "small_sea_hub.backend.saved_route_for_candidate",
        lambda conn, berth_id, key: route if key == "candidate-a" else None,
    )
    stored = env.stored
    monkeypatch.setattr(
        env.backend,
        "_make_storage_adapter_from_record",
        lambda s, cloud: SimpleNamespace(
            download=lambda path: (True, stored[path], "fixture-etag")
            if path in stored
            else (False, None, SimpleNamespace(absent=True))
        ),
    )

    # An old physical location, the same logical object: accepted.
    ok, data, _etag = env.backend.inspect_berth_source_candidate(
        token, "candidate-a", OBJECT_PATH
    )
    assert ok and data == b"Alice's object"

    # The same candidate bytes offered as a different object: refused.
    stored[OTHER_PATH] = stored[OBJECT_PATH]
    response = env.http.get(
        "/berth_source/inspect",
        params={"candidate_key": "candidate-a", "path": OTHER_PATH},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 502
    assert response.json()["reason"] == "context_mismatch"
    assert session.mode == "encrypted"


# --- Missing ownership evidence ---


def test_a_new_invitee_waits_for_its_own_accepted_ownership_row(routed):
    env = routed
    bob_token = open_session(env, "Bob")
    alice_token = open_session(env, "Alice")
    assert put(env, bob_token, OBJECT_PATH, b"Bob's object").status_code == 200

    bob_local_db = device_local_db_path(env.root, env.bob)
    device = env.bob_sender.sender_device_key_id
    before = load_peer_sender_key(bob_local_db, env.team_id, device)

    # Bob holds his own sender key, but his snapshot has no accepted row for
    # his device yet. His own read waits; nothing about it is decided.
    pending = get(env, bob_token, "/cloud_file", path=OBJECT_PATH)
    assert pending.status_code == 409
    assert pending.json()["error"] == "device_ownership_unavailable"
    assert load_peer_sender_key(bob_local_db, env.team_id, device) == before

    # Work whose prerequisites are satisfied is not blocked: Bob can still read
    # from the inviter he already recognizes.
    assert put(env, alice_token, OTHER_PATH, b"Alice's object").status_code == 200
    from_inviter = get(
        env,
        bob_token,
        "/peer_cloud_file",
        path=OTHER_PATH,
        teammate_id=env.alice_teammate.hex(),
    )
    assert from_inviter.status_code == 200

    # The evidence arrives. Retrying the same read now succeeds; no automatic
    # synchronization or repair is involved.
    _record_ownership(env.bob_db, device, env.bob_teammate)
    resolved = get(env, bob_token, "/cloud_file", path=OBJECT_PATH)
    assert resolved.status_code == 200
    assert base64.b64decode(resolved.json()["data"]) == b"Bob's object"


def test_a_contradicted_association_still_prevents_acceptance(routed):
    env = routed
    bob_token = open_session(env, "Bob")
    assert put(env, bob_token, OBJECT_PATH, b"Bob's object").status_code == 200

    # Bob's device recorded as belonging to Alice: not a pending prerequisite.
    _record_ownership(env.bob_db, env.bob_sender.sender_device_key_id, env.alice_teammate)
    response = get(env, bob_token, "/cloud_file", path=OBJECT_PATH)

    assert response.status_code == 502
    assert response.json()["reason"] == "unexpected_publisher"


def test_note_to_self_keeps_its_separate_passthrough_contract(routed):
    """NoteToSelf stays raw transport here, and says so rather than pretending.

    It has no `team_device` projection and no group sender key, so it is not an
    ordinary encrypted team. Adding that lifecycle is separate scope; what this
    branch owes is that the absence is a named outcome and never a silent
    downgrade of an encrypted read.
    """
    env = routed
    passthrough = open_session(
        env, "Alice", team_name="NoteToSelf", mode="passthrough"
    )
    assert put(env, passthrough, OBJECT_PATH, b"raw bytes").status_code == 200
    response = get(env, passthrough, "/cloud_file", path=OBJECT_PATH)
    assert base64.b64decode(response.json()["data"]) == b"raw bytes"

    # There is no ownership projection to consult, which the Hub reports as its
    # own outcome rather than as an empty mapping that would wave reads through.
    encrypted = open_session(env, "Alice", team_name="NoteToSelf", mode="encrypted")
    session = env.backend._lookup_session(encrypted)
    assert env.backend._device_ownership_by_key_id(session) is None

    # Reading the passthrough bytes through an encrypted session is refused, not
    # quietly handed back raw.
    refused = get(env, encrypted, "/cloud_file", path=OBJECT_PATH)
    assert refused.status_code == 502
    assert refused.json()["reason"] == "unreadable_envelope"
    assert "data" not in refused.json()


def _record_ownership(core_db, device_key_id, teammate_id):
    """Stand in for the accepted admission evidence reaching a local snapshot."""
    with sqlite3.connect(str(core_db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO team_device "
            "(device_key_id, teammate_id, public_key, created_at) "
            "VALUES (?, ?, ?, ?)",
            (device_key_id, teammate_id, b"unused-here", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
