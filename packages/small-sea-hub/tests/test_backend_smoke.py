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

    small_sea.add_cloud_location(session, "s3", f"localhost:{cloud_port}")

    return session


def test_add_cloud(playground_dir, minio_server_gen):
    cloud_port = 9876
    cloud_server = minio_server_gen(root_dir=None, port=cloud_port)
    small_sea = SmallSea.SmallSeaBackend(root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")

    session = helper_add_cloud(small_sea, "alice", cloud_port)
