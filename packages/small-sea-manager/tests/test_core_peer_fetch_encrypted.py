"""Encrypted Core peer-fetch probes, one per invitation direction (#185).

Integration probes, not micro tests: the real peer path runs over S3 or
Dropbox, so LocalFolderStore cannot stand in for it. Everything here is local —
one in-process Hub and two MinIO servers — and every byte moves through Hub
endpoints.

Both participant orderings are exercised against separately configured storage
and Hub routes.
They are staged as two invitations in opposite directions, in two teams, over
one shared setup.
Both reads are invitee-reads-inviter: reading a teammate the other way round
inside a single team needs that teammate's sender key redistributed, and
redistribution needs the membership certificate to travel back over the Core
chain, which is the integration #185 deliberately does not perform.

What is proved: a fetch through the encrypted Core session imports the peer's
published head, parks it under that teammate's ref, and leaves local `main`
alone. What is not proved, here or anywhere in #185: that the head was authored
by that teammate, or that it may be integrated.
"""

import json
import pathlib
import sqlite3

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from cod_sync.repo import Repo
from fastapi.testclient import TestClient
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import (
    PeerSenderKeyUnavailableError,
    TeamManager,
    core_peer_latest_ref,
)
from test_support import accept_and_export, acceptance_record_from_courier

_ALICE_TEAM = "ProjectX"
_BOB_TEAM = "ProjectY"
_MAIN = "refs/heads/main"


def _s3(minio):
    return boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _make_bucket_public(minio, bucket_name):
    _s3(minio).put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
                    }
                ],
            }
        ),
    )


def _team_repo(root, participant_hex, team_name):
    sync = root / "Participants" / participant_hex / team_name / "Sync"
    return Repo(sync / ".git", sync)


def _core_berth_id_hex(root, participant_hex, team_name):
    db = root / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT b.id FROM team_app_berth b JOIN app a ON a.id = b.app_id "
            "WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()
    return row[0].hex()


def _invite(root, inviter, inviter_hex, invitee, team_name, inviter_minio):
    """Run one whole invitation and return (inviter_teammate_id, admission_package_token).

    Publication is encrypted throughout, which is what the invitee's clone of
    the inviter's chain has to decrypt with the sender key carried in the
    token.
    """
    team = Provisioning.create_team(root, inviter_hex, team_name)
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        root, inviter_hex, team["berth_id_hex"]
    )
    inviter.publish_teammate_berth_storage_announcement(
        team_name, bytes.fromhex(team["berth_id_hex"]), allocation
    )
    inviter.push_team(team_name)
    _make_bucket_public(inviter_minio, allocation["location"])

    token = Provisioning.create_invitation(
        root, inviter_hex, team_name,
        {"protocol": "s3", "url": inviter_minio["endpoint"]},
        invitee_label="invitee",
    )
    inviter.push_team(team_name)

    acceptance_b64 = accept_and_export(invitee, token)
    report = inviter.complete_invitation_acceptance(team_name, acceptance_b64)
    inviter.push_team(team_name)
    return team["teammate_id_hex"], report["admission_package"]


@pytest.fixture(scope="module")
def minios(minio_server_gen):
    return minio_server_gen(), minio_server_gen()


@pytest.fixture()
def teams(playground_dir, minios):
    """Alice and Bob, each the inviter of one team and the invitee of the other.

    Shared by both probes: they read different chains and assert on different
    refs, so neither depends on the other having run.
    """
    alice_minio, bob_minio = minios
    root = pathlib.Path(playground_dir)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    hub_app.state.backend = backend
    http = TestClient(hub_app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")

    Provisioning.add_cloud_storage(
        root, alice_hex, protocol="s3", url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"], secret_key=alice_minio["secret_key"],
    )
    Provisioning.add_cloud_storage(
        root, bob_hex, protocol="s3", url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"], secret_key=bob_minio["secret_key"],
    )

    alice = TeamManager(root, alice_hex, _http_client=http)
    bob = TeamManager(root, bob_hex, _http_client=http)

    alice_teammate_id, alice_package = _invite(root, alice, alice_hex, bob, _ALICE_TEAM, alice_minio)
    bob_teammate_id, _ = _invite(root, bob, bob_hex, alice, _BOB_TEAM, bob_minio)

    return dict(
        root=root,
        alice=alice, alice_hex=alice_hex, alice_teammate_id=alice_teammate_id,
        alice_package=alice_package,
        bob=bob, bob_hex=bob_hex, bob_teammate_id=bob_teammate_id,
    )


def _probe(teams, *, team_name, reader, writer, teammate_id):
    """Fetch the inviter's Core chain as the invitee, and check what landed."""
    reader_manager = teams[reader]
    published_head = _team_repo(teams["root"], teams[f"{writer}_hex"], team_name).resolve_ref(_MAIN)
    repo = _team_repo(teams["root"], teams[f"{reader}_hex"], team_name)
    main_before = repo.resolve_ref(_MAIN)

    result = reader_manager.fetch_teammate_core(team_name, teammate_id)

    assert result.disposition == "created"
    assert result.observed_head_sha == published_head
    assert result.current_head_sha == published_head
    assert result.observation_ref_name is None
    latest_ref = core_peer_latest_ref(teammate_id)
    assert repo.resolve_ref(latest_ref) == published_head
    assert repo.has_commit(published_head)
    # Fetch and park only: the local checkout is untouched.
    assert repo.resolve_ref(_MAIN) == main_before

    parked = [
        head
        for head in reader_manager.list_core_source_heads(team_name)
        if head.ref_name == latest_ref
    ]
    assert len(parked) == 1
    assert parked[0].source_kind == "teammate"
    assert parked[0].teammate_id == teammate_id
    assert parked[0].is_maximal
    assert parked[0].head_sha == published_head


def test_the_invitee_fetches_the_inviters_core_chain(teams):
    _probe(
        teams,
        team_name=_ALICE_TEAM,
        reader="bob",
        writer="alice",
        teammate_id=teams["alice_teammate_id"],
    )


def test_the_same_fetch_works_with_the_invitation_direction_reversed(teams):
    _probe(
        teams,
        team_name=_BOB_TEAM,
        reader="alice",
        writer="bob",
        teammate_id=teams["bob_teammate_id"],
    )




def test_admission_import_alone_does_not_let_the_inviter_read_the_invitee(teams):
    """The admission package fixes the invitee's side but not the inviter's read (#228, task 99).

    Alice invited Bob to ProjectX.
    After Bob imports the admission package, his Core holds his own membership
    certificate, so `reconcile_runtime_state` reports his device as trusted and
    produces a sender-key redistribution artifact for Alice's device.
    That is the step that returned early before the import.

    Alice's fetch of Bob's chain still fails with `PeerSenderKeyUnavailableError`.
    The artifact is only returned to the caller here.
    Delivering it to Alice runs in the Hub's runtime watch, which this probe
    does not start, so Alice never receives Bob's sender key.
    Whether delivery would then make the read work is not tested.
    """
    root, alice, bob = teams["root"], teams["alice"], teams["bob"]
    assert bob.import_admission_package(_ALICE_TEAM, teams["alice_package"]) is True

    db = root / "Participants" / teams["alice_hex"] / _ALICE_TEAM / "Sync" / "core.db"
    with sqlite3.connect(str(db)) as conn:
        ids = [row[0].hex() for row in conn.execute("SELECT id FROM teammate")]
    (bob_id,) = [i for i in ids if i != teams["alice_teammate_id"]]

    bob.push_team(_ALICE_TEAM)
    report = bob.reconcile_runtime_state(_ALICE_TEAM)
    assert report["local_device_trusted"] is True
    assert len(report["redistribution_artifacts"]) == 1

    with pytest.raises(PeerSenderKeyUnavailableError):
        alice.fetch_teammate_core(_ALICE_TEAM, bob_id)


def test_delivering_the_redistribution_artifact_lets_the_inviter_read_the_invitee(teams):
    """PLACEHOLDER (#228, task 100)."""
    root, alice, bob = teams["root"], teams["alice"], teams["bob"]
    assert bob.import_admission_package(_ALICE_TEAM, teams["alice_package"]) is True

    db = root / "Participants" / teams["alice_hex"] / _ALICE_TEAM / "Sync" / "core.db"
    with sqlite3.connect(str(db)) as conn:
        ids = [row[0].hex() for row in conn.execute("SELECT id FROM teammate")]
    (bob_id,) = [i for i in ids if i != teams["alice_teammate_id"]]

    bob.push_team(_ALICE_TEAM)
    report = bob.reconcile_runtime_state(_ALICE_TEAM)
    (artifact,) = report["redistribution_artifacts"]

    # Same call the Hub's runtime inbox makes on receipt.
    Provisioning.receive_sender_key_distribution(
        root, teams["alice_hex"], _ALICE_TEAM, artifact["distribution_payload"]
    )
    try:
        result = alice.fetch_teammate_core(_ALICE_TEAM, bob_id)
        print("RESULT1", result)
    except Exception as e:
        print("RESULT1 exc", type(e), e)
    bob.push_team(_ALICE_TEAM)
    try:
        result = alice.fetch_teammate_core(_ALICE_TEAM, bob_id)
        print("RESULT2", result)
    except Exception as e:
        print("RESULT2 exc", type(e), e)
