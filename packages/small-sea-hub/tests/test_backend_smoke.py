# Top Matter
# Smoke tests for the hub backend.
# Participant provisioning now lives in small_sea_manager.provisioning,
# so we call that to set up test participants before exercising hub operations.

import pathlib
import sqlite3

import pytest

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning


def test_just_make_backend(playground_dir):
    small_sea = SmallSea.SmallSeaBackend(root_dir=playground_dir)


def test_future_hub_db_version_fails_fast(playground_dir, capsys):
    root = pathlib.Path(playground_dir)
    db_path = root / "small_sea_collective_local.db"
    future_version = SmallSea.SmallSeaBackend.hub_schema_version + 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"PRAGMA user_version = {future_version}")
        conn.commit()

    with pytest.raises(SmallSea.FutureHubDatabaseVersionError) as exc_info:
        SmallSea.SmallSeaBackend(root_dir=playground_dir)

    message = str(exc_info.value)
    assert str(db_path) in message
    assert str(future_version) in message
    assert str(SmallSea.SmallSeaBackend.hub_schema_version) in message
    assert "TODO: DB FROM THE FUTURE!" not in capsys.readouterr().out

    with sqlite3.connect(str(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == future_version
    assert "session" not in tables


def test_create_user(playground_dir):
    small_sea = SmallSea.SmallSeaBackend(root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")


def helper_add_cloud(small_sea, username, cloud_port):
    session_bytes = small_sea.open_session(
        "alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests", mode="passthrough"
    )

    session = session_bytes.hex()

    Provisioning.add_cloud_storage(
        small_sea.root_dir,
        small_sea._lookup_session(session).participant_id.hex(),
        protocol="s3",
        url=f"localhost:{cloud_port}",
    )

    return session


def test_add_cloud(playground_dir, minio_server_gen):
    cloud_server = minio_server_gen(root_dir=None)
    small_sea = SmallSea.SmallSeaBackend(root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")

    session = helper_add_cloud(small_sea, "alice", cloud_server["port"])


def test_two_first_signal_bumps_both_count(playground_dir, monkeypatch):
    """If another writer creates signals.yaml first, our first write must not overwrite it."""
    import types
    import yaml
    from small_sea_hub.cloud_errors import absent, cas_conflict

    berth_id = bytes(16)
    other = "other-berth"

    class RacingAdapter:
        """In-memory storage where another writer creates signals.yaml just after our first read."""

        def __init__(self):
            self.files = {}

        def download(self, path):
            if path not in self.files:
                # The other writer creates the file right after we see it absent.
                self.files[path] = (yaml.dump({"version": 1, other: 1}).encode(), "v1")
                return False, None, absent("not found")
            data, etag = self.files[path]
            return True, data, etag

        def upload_fresh(self, path, data):
            if path in self.files:
                return False, None, cas_conflict("exists")
            self.files[path] = (data, "v2")
            return True, "v2", None

        def upload_overwrite(self, path, data):
            self.files[path] = (data, "v2")
            return True, "v2", None

        def upload_if_match(self, path, data, etag):
            if self.files[path][1] != etag:
                return False, None, cas_conflict("changed")
            self.files[path] = (data, "v2")
            return True, "v2", None

    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    adapter = RacingAdapter()
    monkeypatch.setattr(backend, "_lookup_session", lambda _s: types.SimpleNamespace(berth_id=berth_id))
    monkeypatch.setattr(backend, "_make_storage_adapter", lambda _s: adapter)
    monkeypatch.setattr(backend, "_ntfy_publish_signal", lambda *_a: None)

    assert backend._bump_signal("session") == 1
    signals = yaml.safe_load(adapter.files[backend._SIGNAL_PATH][0])
    assert signals == {"version": 1, other: 1, berth_id.hex(): 1}
