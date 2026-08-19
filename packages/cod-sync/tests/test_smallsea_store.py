# Publish and fetch through SmallSeaStore, backed by the Hub.
#
# Uses FastAPI TestClient (in-process) with a real MinIO server for S3 storage,
# so the gateway rule holds: every byte moves through /cloud_file.

import pathlib

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from cod_sync_test_helpers import commit_file, make_cod_sync, make_repo, working_tree_files
from fastapi.testclient import TestClient
from small_sea_hub.server import app

from cod_sync.format import decode_link
from cod_sync.store import ObjectNotFoundError, SmallSeaStore

MINIO_PORT = 9400


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=MINIO_PORT)


@pytest.fixture()
def hub_env(playground_dir, minio):
    """Backend, participant, TestClient, and session — ready to go."""
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    Provisioning.create_new_participant(playground_dir, "alice")

    app.state.backend = backend
    client = TestClient(app)

    # Open session (two-step flow)
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
            "client": "Smoke Tests",
            "mode": "passthrough",
        },
    )
    assert resp.status_code == 200
    result = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200
    session_hex = resp.json()

    # Register MinIO cloud account, allocate a berth cloud, and pre-create the bucket.
    # Account registration alone is insufficient: the Hub resolves storage per berth
    # via berth_cloud_allocation, so the session's berth needs an explicit allocation.
    storage_id = backend.add_cloud_location(
        session_hex,
        "s3",
        minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    ss_session = backend._lookup_session(session_hex)
    allocation = Provisioning.add_berth_cloud_allocation_by_berth_id(
        playground_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
        storage_id,
    )
    bucket_name = allocation["location"]
    boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    ).create_bucket(Bucket=bucket_name)

    return {
        "client": client,
        "session_hex": session_hex,
        "playground_dir": playground_dir,
        "minio": minio,
    }


def test_publish_and_fetch_roundtrip_via_hub(hub_env, scratch_dir):
    client = hub_env["client"]
    session_hex = hub_env["session_hex"]
    scratch = pathlib.Path(scratch_dir)

    # ---- Alice: commit and publish through the Hub ----
    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "README.md", "# Hub Roundtrip\n")
    commit_file(alice, "notes.txt", "testing through the hub\n")

    alice_store = SmallSeaStore(session_hex, client=client)
    published = make_cod_sync(alice, alice_store).publish()
    assert published.changed is True

    link = decode_link(alice_store.get_latest_link()[0])
    assert link.previous is None
    assert link.head == published.head
    assert link.version == "2.0.0"

    # ---- Bob: cold-start fetch through the same Hub endpoints ----
    bob = make_repo(scratch / "bob", "bob")
    result = make_cod_sync(bob, SmallSeaStore(session_hex, client=client)).fetch()
    bob.checkout_branch("main", result.observed_head)

    assert working_tree_files(bob) == working_tree_files(alice)


def test_incremental_publication_via_hub(hub_env, scratch_dir):
    client = hub_env["client"]
    session_hex = hub_env["session_hex"]
    scratch = pathlib.Path(scratch_dir)

    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "a.txt", "one\n")
    store = SmallSeaStore(session_hex, client=client)
    first = make_cod_sync(alice, store).publish()

    commit_file(alice, "b.txt", "two\n")
    second = make_cod_sync(alice, store).publish()
    assert second.changed is True

    link = decode_link(store.get_latest_link()[0])
    assert link.previous.link_id == first.link_uid
    assert link.previous.head == first.head

    # Publishing the same head again changes nothing in the cloud.
    unchanged = make_cod_sync(alice, store).publish()
    assert unchanged.changed is False
    assert unchanged.link_uid == second.link_uid


def test_an_empty_bucket_reports_absence_not_a_transport_failure(hub_env):
    store = SmallSeaStore(hub_env["session_hex"], client=hub_env["client"])
    with pytest.raises(ObjectNotFoundError):
        store.get_latest_link()
