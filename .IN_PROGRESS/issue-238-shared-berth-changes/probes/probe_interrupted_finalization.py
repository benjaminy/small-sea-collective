"""Move 7 probe: is provider finalization observable as a completed step?

The Move 6 ledger entry "a selection is not minted until every field it
freezes is final" rests on two claims about a real provider that no probe has
checked: that finalization is observable as a completed step, and that a
partially materialized allocation is recoverable by a sibling.

This probe interrupts finalization at three pause points against a local MinIO
server and compares what survives each one:

  P0  before storage exists            -- the provider call fails outright
  P1  after storage exists, before it is publicly readable
  P2  after finalization completes, before the announcement is signed

The two-installation setup follows `probe_delayed_signing.py`; that file is
left alone so its recorded witness keeps running unchanged.

These probes are not micro tests. They run against current code and fix
nothing; what they assert is the observed behavior, defect or not.
"""
import pathlib
import shutil

import httpx
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from small_sea_hub.adapters.s3 import SmallSeaS3Adapter
from small_sea_hub.server import app
from small_sea_manager import provisioning
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)

TEAM = "ProjectX"

#: Key used only to ask the provider a question. Nothing writes it, so an
#: anonymous GET separates "the policy lets me read" (404 NoSuchKey) from
#: "it does not" (403 AccessDenied) without depending on bucket contents.
PROBE_KEY = "issue-238-readability-probe"


def _open_session(http, nickname, team, mode="encrypted"):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "issue-238 probe",
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


def _copy_team_clone(src_root, dst_root, participant_hex, team_name):
    src = provisioning._team_sync_dir(src_root, participant_hex, team_name).parent
    dst = provisioning._team_sync_dir(dst_root, participant_hex, team_name).parent
    shutil.copytree(src, dst)


def _core_allocation(root, participant_hex):
    state = provisioning.derive_team_join_state(root, participant_hex, TEAM)
    return state["core_berth_id"], state["allocation"]


def _announcements(root, participant_hex, teammate_id, berth_id):
    engine = provisioning._sqlite_engine(
        provisioning._team_db_path(root, participant_hex, TEAM)
    )
    try:
        with engine.begin() as conn:
            return provisioning.load_teammate_berth_storage_announcements(
                conn, teammate_id, berth_id
            )
    finally:
        engine.dispose()


def _client_error(operation):
    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "interrupted"}}, operation
    )


def _bucket_exists(minio, bucket):
    """Ask the account owner, not the public, whether the bucket is there."""
    import boto3
    from botocore.config import Config as BotoConfig

    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    names = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    return bucket in names


def _anonymous_read_status(minio, bucket):
    """HTTP status an unauthenticated teammate gets for a key in `bucket`."""
    return httpx.get(f"{minio['endpoint']}/{bucket}/{PROBE_KEY}").status_code


def _two_installations(workspace, minio, http_a_holder):
    """Alice on two installations, both trusted device keys, one Core berth."""
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    alice_hex = Provisioning.create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(
        root_dir=str(root_a), auto_approve_sessions=True
    )
    app.state.backend = backend_a
    http_a = TestClient(app)
    http_a_holder["backend_a"] = backend_a
    http_a_holder["http_a"] = http_a

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a,
        alice_hex,
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a,
        alice_hex,
        backend_a._lookup_session(nts_token_a).berth_id,
        cloud_storage_id,
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    team_result = manager_a.create_team(TEAM)
    teammate_id = bytes.fromhex(team_result["teammate_id_hex"])
    manager_a.push_note_to_self()

    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)

    manager_b_local = TeamManager(root_b, alice_hex)
    accounts = manager_b_local.list_cloud_storage()
    assert accounts, "device B inherited no cloud_storage row"
    manager_b_local.connect_cloud_storage_credentials(
        accounts[0]["id"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    _copy_team_clone(root_a, root_b, alice_hex, TEAM)

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_a)
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(
        TEAM, prepared["join_request_bundle"]
    )
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])

    state_a = provisioning.derive_team_join_state(root_a, alice_hex, TEAM)
    state_b = provisioning.derive_team_join_state(root_b, alice_hex, TEAM)
    assert state_a["admission"] == state_b["admission"] == "finalized"
    assert state_a["core_berth_id"] == state_b["core_berth_id"]
    assert state_a["device_key_id"] != state_b["device_key_id"]

    return {
        "alice_hex": alice_hex,
        "root_a": root_a,
        "root_b": root_b,
        "manager_a": manager_a,
        "teammate_id": teammate_id,
        "berth_id": state_a["core_berth_id"],
    }


def _pause_before_storage_exists(monkeypatch):
    def never_creates(self):
        raise _client_error("CreateBucket")

    monkeypatch.setattr(SmallSeaS3Adapter, "ensure_bucket_public", never_creates)


def _pause_after_storage_exists(monkeypatch):
    """Create the bucket, then stop before the public-read policy is applied.

    `ensure_bucket_public` is two provider calls, and this is the gap between
    them: the storage exists and no teammate can read it.
    """

    def half_finishes(self):
        try:
            self.s3.create_bucket(Bucket=self.bucket_name)
        except ClientError as exn:
            code = exn.response["Error"]["Code"]
            if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                raise
        raise _client_error("PutBucketPolicy")

    monkeypatch.setattr(SmallSeaS3Adapter, "ensure_bucket_public", half_finishes)


def _pause_before_signing(monkeypatch):
    def interrupted(*args, **kwargs):
        raise RuntimeError("stopped before signing the announcement")

    monkeypatch.setattr(
        provisioning, "publish_teammate_berth_storage_announcement", interrupted
    )


def test_no_durable_evidence_distinguishes_the_three_finalization_pauses(
    playground_dir, minio_server_gen, monkeypatch
):
    """P0, P1 and P2 leave allocation rows of the same shape, and only that."""
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, alice_hex = setup["root_a"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    observed = {}
    for label, pause in (
        ("P0_before_storage_exists", _pause_before_storage_exists),
        ("P1_after_storage_exists", _pause_after_storage_exists),
        ("P2_before_signing", _pause_before_signing),
    ):
        pause(monkeypatch)
        report = manager_a.reconcile_team_route(TEAM, new_location=True)
        monkeypatch.undo()
        _, allocation = _core_allocation(root_a, alice_hex)
        observed[label] = {
            "route": report["route"],
            "route_reason": report["route_reason"],
            "allocation": allocation,
            "bucket_exists": _bucket_exists(minio, allocation["location"]),
            "anonymous_status": _anonymous_read_status(minio, allocation["location"]),
        }

    for label, row in observed.items():
        print(
            f"{label}: route={row['route']} reason={row['route_reason']} "
            f"location={row['allocation']['location']} "
            f"bucket_exists={row['bucket_exists']} "
            f"anonymous_status={row['anonymous_status']}"
        )

    # Every pause leaves a durable allocation row that differs only in the
    # generation ID and the location string. Nothing in it records whether the
    # provider finished, so the rows are indistinguishable as evidence.
    assert set(observed["P0_before_storage_exists"]["allocation"]) == {
        "id",
        "berth_id",
        "cloud_storage_id",
        "location",
        "protocol",
        "url",
        "client_id",
        "path_metadata",
    }
    invariant = {
        label: {
            k: v for k, v in row["allocation"].items() if k not in ("id", "location")
        }
        for label, row in observed.items()
    }
    assert len({tuple(sorted(v.items())) for v in invariant.values()}) == 1, invariant

    # The provider state behind those identical rows is not identical.
    assert observed["P0_before_storage_exists"]["bucket_exists"] is False
    assert observed["P1_after_storage_exists"]["bucket_exists"] is True
    assert observed["P2_before_signing"]["bucket_exists"] is True

    # Nor is what a teammate can read -- but the outsider's view collapses the
    # two failures: no bucket and an unreadable bucket are both 403, so an
    # unauthenticated reader cannot tell them apart either.
    assert observed["P0_before_storage_exists"]["anonymous_status"] == 403
    assert observed["P1_after_storage_exists"]["anonymous_status"] == 403
    assert observed["P2_before_signing"]["anonymous_status"] == 404

    # The route report distinguishes P2 from the other two, and does not
    # distinguish P0 from P1: a failed create and a half-applied bucket policy
    # are one reason.
    assert observed["P0_before_storage_exists"]["route_reason"] == (
        observed["P1_after_storage_exists"]["route_reason"]
    )
    assert observed["P0_before_storage_exists"]["route_reason"] == "materialization_failed"
    assert observed["P2_before_signing"]["route_reason"] == "route_preparation_error"

    # No pause produced a signed announcement, so nothing was minted naming
    # storage that does not exist. Within one device's attempt the runtime
    # does finalize before it mints.
    assert _announcements(root_a, alice_hex, setup["teammate_id"], setup["berth_id"]) != []
    signed_locations = {
        row.location
        for row in _announcements(
            root_a, alice_hex, setup["teammate_id"], setup["berth_id"]
        )
    }
    for row in observed.values():
        assert row["allocation"]["location"] not in signed_locations


def test_a_sibling_cannot_tell_a_half_materialized_allocation_from_a_finished_one(
    playground_dir, minio_server_gen, monkeypatch
):
    """B adopts the allocation A left unreadable and sees nothing wrong."""
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    holder = {}
    setup = _two_installations(workspace, minio, holder)
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    _pause_after_storage_exists(monkeypatch)
    report_a = manager_a.reconcile_team_route(TEAM, new_location=True)
    monkeypatch.undo()
    assert report_a["route"] == "pending"
    assert report_a["route_reason"] == "materialization_failed"
    _, half = _core_allocation(root_a, alice_hex)
    assert _bucket_exists(minio, half["location"])
    assert _anonymous_read_status(minio, half["location"]) == 403
    manager_a.push_note_to_self()

    backend_b = SmallSea.SmallSeaBackend(
        root_dir=str(root_b), auto_approve_sessions=True
    )
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    manager_b.refresh_note_to_self()

    _, adopted = _core_allocation(root_b, alice_hex)
    assert adopted == half, "device B did not adopt the half-materialized allocation"

    # Everything B can read locally about that allocation.
    reported = manager_b.core_storage_allocation(TEAM)
    print(f"sibling report: {reported}")
    assert reported["allocation"] == half
    assert reported["route"] == "pending"
    # `pending` is the same value B would see for an allocation whose bucket is
    # finished and merely unsigned, so it does not name the difference.
    assert set(reported) == {"allocation", "route", "admission"}


def test_a_sibling_repairs_a_half_materialized_allocation_by_redoing_it(
    playground_dir, minio_server_gen, monkeypatch
):
    """B finishes A's interrupted finalization without a new location."""
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    _pause_after_storage_exists(monkeypatch)
    manager_a.reconcile_team_route(TEAM, new_location=True)
    monkeypatch.undo()
    _, half = _core_allocation(root_a, alice_hex)
    assert _anonymous_read_status(minio, half["location"]) == 403
    manager_a.push_note_to_self()

    backend_b = SmallSea.SmallSeaBackend(
        root_dir=str(root_b), auto_approve_sessions=True
    )
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    manager_b.refresh_note_to_self()

    report_b = manager_b.reconcile_team_route(TEAM)
    assert report_b["route"] == "ready", report_b

    _, repaired = _core_allocation(root_b, alice_hex)
    assert repaired == half, "repair changed the allocation it was repairing"
    assert _anonymous_read_status(minio, repaired["location"]) == 404

    [announcement] = [
        row
        for row in _announcements(
            root_b, alice_hex, setup["teammate_id"], setup["berth_id"]
        )
        if row.location == repaired["location"]
    ]
    assert announcement.location == half["location"]


def test_no_shipped_adapter_returns_a_provider_issued_locator():
    """The writeback path the finalization rule depends on has no producer.

    `_handle_materialization_outcome` persists a provider-issued locator on
    `materialized_with_locator`, and that is the only case where a locator is
    settled by the provider rather than generated locally. No adapter in the
    repository ever returns it.
    """
    import inspect

    from small_sea_hub.adapters import base, dropbox, gdrive, s3

    for module in (base, dropbox, gdrive, s3):
        source = inspect.getsource(module)
        assert "materialized_with_locator" not in source, module.__name__
