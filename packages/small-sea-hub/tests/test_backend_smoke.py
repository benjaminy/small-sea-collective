# Top Matter
# Smoke tests for the hub backend.
# Participant provisioning now lives in small_sea_team_manager.provisioning,
# so we call that to set up test participants before exercising hub operations.

import small_sea_hub.backend as SmallSea
import small_sea_team_manager.provisioning as Provisioning

def test_just_make_backend(playground_dir):
    small_sea = SmallSea.SmallSeaBackend(root_dir=playground_dir)

def test_create_user(playground_dir):
    small_sea = SmallSea.SmallSeaBackend(
        root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")

def helper_add_cloud(
        small_sea,
        username,
        cloud_port):
    session_bytes = small_sea.open_session(
        "alice",
        "SmallSeaCollectiveCore",
        "NoteToSelf",
        "Smoke Tests")

    session = session_bytes.hex()

    small_sea.add_cloud_location(
        session,
        "s3",
        f"localhost:{cloud_port}")

    return session

def test_add_cloud(playground_dir, minio_server_gen):
    cloud_port = 9876
    cloud_server = minio_server_gen(
        root_dir=None,
        port=cloud_port)
    small_sea = SmallSea.SmallSeaBackend(
        root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")

    session = helper_add_cloud(
        small_sea,
        "alice",
        cloud_port)


def test_first_sync_to_cloud(playground_dir, minio_server_gen):
    cloud_port = 9878
    cloud_server = minio_server_gen(
        root_dir=None,
        port=cloud_port)
    small_sea = SmallSea.SmallSeaBackend(
        root_dir=playground_dir)

    Provisioning.create_new_participant(playground_dir, "alice")

    session = helper_add_cloud(
        small_sea,
        "alice",
        cloud_port)

    small_sea.sync_to_cloud(session)
