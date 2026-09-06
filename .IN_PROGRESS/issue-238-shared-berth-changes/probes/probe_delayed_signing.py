"""Move 1 witness: can delayed signing reverse an observed succession?

Two installations of one identity (Alice) share one Core berth allocation.
Device A finalizes route X in the shared NoteToSelf evidence and is
interrupted before signing its announcement. Device B adopts that evidence,
deliberately replaces X with Y, and signs and stores its announcement of Y.
Device A then resumes without ever observing Y and signs X.

The question is what a recipient holding both valid announcements selects.
The claim under test is that it selects X -- the route B observed and
replaced -- because `_uuid7_after` mints A's announcement ID from the
predecessor A last saw, which excludes Y.

This probe runs against current code and fixes nothing.
"""
import pathlib
import shutil
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager import provisioning
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from sqlalchemy import text

TEAM = "ProjectX"


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
    """Give the sibling the same team clone.

    Unlike `_copy_team_baseline` in test_linked_device_bootstrap.py, the
    NoteToSelf `team` row is not inserted here: device B inherited it by
    cloning A's NoteToSelf during identity bootstrap, and inserting it again
    would both collide and detach B's row from the shared history.
    """
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


def _insert_announcement(db_path, announcement):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO teammate_berth_storage_announcement "
            "(announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                announcement.announcement_id,
                announcement.teammate_id,
                announcement.berth_id,
                announcement.protocol,
                announcement.url,
                announcement.location,
                announcement.announced_at,
                announcement.signer_key_id,
                announcement.signature,
            ),
        )
        conn.commit()


def _select(db_path, teammate_id, berth_id, team_id):
    engine = provisioning._sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            return provisioning.selected_teammate_berth_storage_announcement(
                conn, teammate_id, berth_id, team_id=team_id
            )
    finally:
        engine.dispose()


def test_delayed_signing_reverses_an_observed_succession(playground_dir, minio_server_gen, monkeypatch):
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    # --- Device A: identity, one S3 account, NoteToSelf berth allocation ---
    alice_hex = Provisioning.create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    app.state.backend = backend_a
    http_a = TestClient(app)

    nts_token_a = _open_session(http_a, "Alice", "NoteToSelf", mode="passthrough")
    cloud_storage_id = Provisioning.add_cloud_storage(
        root_a, alice_hex, protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a, alice_hex, backend_a._lookup_session(nts_token_a).berth_id, cloud_storage_id
    )

    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    team_result = manager_a.create_team(TEAM)
    team_id = bytes.fromhex(team_result["team_id_hex"])
    teammate_id = bytes.fromhex(team_result["teammate_id_hex"])
    manager_a.push_note_to_self()

    # --- Device B: same identity, same team clone, own trusted device key ---
    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)

    manager_b_local = TeamManager(root_b, alice_hex)
    accounts = manager_b_local.list_cloud_storage()
    assert accounts, "device B inherited no cloud_storage row"
    manager_b_local.connect_cloud_storage_credentials(
        accounts[0]["id"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    _copy_team_clone(root_a, root_b, alice_hex, TEAM)

    manager_b = TeamManager(root_b, alice_hex, _http_client=http_a)
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])

    state_a = provisioning.derive_team_join_state(root_a, alice_hex, TEAM)
    state_b = provisioning.derive_team_join_state(root_b, alice_hex, TEAM)
    assert state_a["admission"] == "finalized"
    assert state_b["admission"] == "finalized", "device B's key is not trusted for this teammate"
    assert state_a["self_in_team"] == state_b["self_in_team"] == teammate_id
    berth_id = state_a["core_berth_id"]
    assert berth_id == state_b["core_berth_id"]
    assert state_a["device_key_id"] != state_b["device_key_id"]

    # `create_team` already announced a first route, so both devices start
    # from a common predecessor announcement rather than from nothing.
    [predecessor] = _announcements(root_a, alice_hex, teammate_id, berth_id)

    # --- 1. A finalizes X with the provider, then is interrupted before signing ---
    def interrupted(*args, **kwargs):
        raise RuntimeError("device A stopped before signing its announcement")

    monkeypatch.setattr(provisioning, "publish_teammate_berth_storage_announcement", interrupted)
    report = manager_a.reconcile_team_route(TEAM, new_location=True)
    monkeypatch.undo()
    assert report["route"] == "pending"
    assert report["route_reason"] == "route_preparation_error"

    _, allocation_x = _core_allocation(root_a, alice_hex)
    assert allocation_x["location"] != predecessor.location
    assert _announcements(root_a, alice_hex, teammate_id, berth_id) == [predecessor]
    manager_a.push_note_to_self()

    # --- 2. B adopts X from durable shared evidence, then replaces it with Y ---
    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    app.state.backend = backend_b
    http_b = TestClient(app)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    manager_b.refresh_note_to_self()

    _, adopted = _core_allocation(root_b, alice_hex)
    # Load-bearing: without this, the probe witnesses competing selections
    # rather than the reversal of a succession B actually observed.
    assert adopted == allocation_x, "device B did not adopt A's finalized route X"

    report_b = manager_b.reconcile_team_route(TEAM, new_location=True)
    assert report_b["route"] == "ready", report_b
    _, allocation_y = _core_allocation(root_b, alice_hex)
    assert allocation_y["location"] != allocation_x["location"]
    announcement_y = _announcements(root_b, alice_hex, teammate_id, berth_id)[0]
    assert announcement_y.location == allocation_y["location"]
    manager_b.push_note_to_self()

    # --- 3. A resumes from its stale view and signs X ---
    app.state.backend = backend_a
    _, still_x = _core_allocation(root_a, alice_hex)
    assert still_x == allocation_x, "device A observed the replacement; the schedule is not staged"
    report_a = manager_a.reconcile_team_route(TEAM)
    assert report_a["route"] == "ready", report_a
    announcement_x = _announcements(root_a, alice_hex, teammate_id, berth_id)[0]
    assert announcement_x.location == allocation_x["location"]

    # --- 4. A recipient holding both announcements resolves the route ---
    # Delivery is staged by inserting the peer's authentic signed row into a
    # copy of the recipient's team DB; it is not carried by the runtime's
    # fetch and merge path.
    recipient_db = provisioning._team_db_path(root_a, alice_hex, TEAM)
    results = {}
    for order, (first, second) in {
        "x_then_y": (announcement_x, announcement_y),
        "y_then_x": (announcement_y, announcement_x),
    }.items():
        scratch = workspace / f"recipient-{order}.db"
        shutil.copyfile(recipient_db, scratch)
        with sqlite3.connect(scratch) as conn:
            conn.execute("DELETE FROM teammate_berth_storage_announcement")
            conn.commit()
        _insert_announcement(scratch, first)
        # Control: the selector returns after validating its highest-ranked
        # acceptable row, so selecting X from both rows never exercises Y's
        # acceptance in this recipient. Check each row alone first; the
        # `y_then_x` case is the one that establishes Y alone selects Y.
        alone = _select(scratch, teammate_id, berth_id, team_id)
        assert alone.status == "announced", (order, alone)
        assert alone.announcement_id == first.announcement_id, (order, alone)
        _insert_announcement(scratch, second)
        results[order] = _select(scratch, teammate_id, berth_id, team_id)

    for order, selection in results.items():
        assert selection.status == "announced", (order, selection)

    # Printed so the recorded result names the actual bytes and clock values
    # the conclusion rests on.
    for label, row in (
        ("predecessor", predecessor),
        ("X (A, signed last)", announcement_x),
        ("Y (B, successor)", announcement_y),
    ):
        print(
            f"{label}: id={row.announcement_id.hex()} "
            f"announced_at={row.announced_at} location={row.location} "
            f"signer={row.signer_key_id.hex()[:16]}"
        )

    # The mechanism: A's later-minted ID outranks the successor it never saw.
    assert announcement_x.announcement_id > announcement_y.announcement_id, (
        "A's resumed signing did not outrank Y; record the observed IDs and "
        "the clock conditions before concluding anything about the defect"
    )
    # The defect: the recipient routes to the replaced location, either order.
    for order, selection in results.items():
        assert selection.announcement_id == announcement_x.announcement_id, order
        assert selection.transport.location == allocation_x["location"], order
