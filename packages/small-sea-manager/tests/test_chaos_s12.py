"""Micro test S12: a linked second device preserves concurrent work.

Two installations of one identity (shared teammate ID, distinct team-device
keys) each append a Constitution event signed with their own current
team-device key and commit. Each snapshot is parked into the other
installation under refs/cod-sync/parked/s12. integrate_core_sources must give
both installations the exact union of the captured rows, with each new event
retaining its own signer, an empty pending table, and a second sweep that
reports no changes and leaves Git heads untouched.
"""

import pathlib
import sqlite3
import subprocess

from sqlalchemy import create_engine

from cod_sync.repo import Repo
from small_sea_manager import constitution_store, provisioning
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import add_cloud_storage, create_new_participant
from test_linked_device_bootstrap import _copy_team_baseline
from wrasse_trust.events import decode_event

TEAM = "ProjectX"
PARKED_REF = "refs/cod-sync/parked/s12"


def _stored_rows(sync: pathlib.Path) -> list[tuple]:
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()


def _pending_rows(sync: pathlib.Path) -> list[tuple]:
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute(
            "SELECT event_id, encoded FROM constitution_event_pending ORDER BY event_id"
        ).fetchall()


def _encoded_of(rows: list[tuple], event_id: bytes) -> bytes:
    for row in rows:
        if row[0] == event_id:
            return row[2]
    raise AssertionError(f"event {event_id.hex()} not in stored rows")


def _commit_dirty(sync: pathlib.Path, message: str) -> None:
    repo = Repo(sync / ".git", sync)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        repo.commit(message)


def _append_event(root, participant_hex: str, sync: pathlib.Path, label: str):
    """Append a chaos_probe event signed by this installation's current team-device key."""
    private_key = provisioning.get_current_team_device_key(root, participant_hex, TEAM)[0]
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    try:
        with engine.begin() as conn:
            (event,) = constitution_store.append_local_event(
                conn, "chaos_probe", {"label": label}, private_key
            )
    finally:
        engine.dispose()
    return event


def test_linked_device_preserves_concurrent_work(playground_dir):
    workspace = pathlib.Path(playground_dir)
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    for directory in (root1, root2, cloud_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # Setup: same teammate ID, two distinct team-device keys (linked bootstrap).
    alice_hex = create_new_participant(root1, "Alice")
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    join_request = create_identity_join_request(root2)
    manager1 = TeamManager(root1, alice_hex)
    welcome = manager1.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root2, welcome["welcome_bundle"])
    team_result = manager1.create_team(TEAM)
    team_id = bytes.fromhex(team_result["team_id_hex"])
    teammate_id = bytes.fromhex(team_result["teammate_id_hex"])
    _copy_team_baseline(root1, root2, alice_hex, alice_hex, TEAM, team_id, teammate_id)
    manager2 = TeamManager(root2, alice_hex)
    prepared = manager2.prepare_linked_device_team_join(TEAM)
    created = manager1.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])
    finalized = manager2.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])
    assert "bootstrap_id_hex" in finalized, f"finalize failed: {finalized}"

    # Step 1: shared teammate ID, different team-device public keys.
    id1 = provisioning.derive_team_join_state(root1, alice_hex, TEAM)["self_in_team"]
    id2 = provisioning.derive_team_join_state(root2, alice_hex, TEAM)["self_in_team"]
    assert id1 == id2 == teammate_id, f"teammate IDs diverged: {id1.hex()} vs {id2.hex()}"
    pub1 = provisioning.get_current_team_device_key(root1, alice_hex, TEAM)[1]
    pub2 = provisioning.get_current_team_device_key(root2, alice_hex, TEAM)[1]
    assert pub1 != pub2, "linked installations must hold distinct team-device keys"

    sync1 = provisioning._team_sync_dir(root1, alice_hex, TEAM)
    sync2 = provisioning._team_sync_dir(root2, alice_hex, TEAM)
    _commit_dirty(sync1, "S12 baseline install A")
    _commit_dirty(sync2, "S12 baseline install B")

    # Step 2: concurrent events, each signed by its own installation's key.
    event1 = _append_event(root1, alice_hex, sync1, "S12-device-1")
    _commit_dirty(sync1, "S12 install A appends local event")
    event2 = _append_event(root2, alice_hex, sync2, "S12-device-2")
    _commit_dirty(sync2, "S12 install B appends local event")
    assert event1.signer_public_key == pub1
    assert event2.signer_public_key == pub2

    # Step 3: capture the post-append rows.
    rows1 = _stored_rows(sync1)
    rows2 = _stored_rows(sync2)
    _encoded_of(rows1, event1.event_id)
    _encoded_of(rows2, event2.event_id)
    expected_union = {(r[0], r[1], r[2]) for r in rows1} | {(r[0], r[1], r[2]) for r in rows2}

    # Step 4: park each snapshot in the other installation under the same ref.
    head1 = Repo(sync1 / ".git", sync1).head()
    head2 = Repo(sync2 / ".git", sync2).head()
    subprocess.run(
        ["git", "-C", str(sync2), "fetch", "--no-tags", str(sync1), f"{head1}:{PARKED_REF}"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(sync1), "fetch", "--no-tags", str(sync2), f"{head2}:{PARKED_REF}"],
        check=True,
    )
    # Parking must not disturb either installation's main or worktree.
    assert Repo(sync1 / ".git", sync1).head() == head1, "fetch moved install A's main"
    assert Repo(sync2 / ".git", sync2).head() == head2, "fetch moved install B's main"
    assert not Repo(sync1 / ".git", sync1).work_tree_paths_differ_from_head(["core.db"])
    assert not Repo(sync2 / ".git", sync2).work_tree_paths_differ_from_head(["core.db"])

    # Step 5, first sweep: both installations integrate the parked source.
    manager1 = TeamManager(root1, alice_hex)
    manager2 = TeamManager(root2, alice_hex)
    results1 = manager1.integrate_core_sources(TEAM)
    results2 = manager2.integrate_core_sources(TEAM)
    parked1 = [r for r in results1 if r["ref_name"] == PARKED_REF]
    parked2 = [r for r in results2 if r["ref_name"] == PARKED_REF]
    assert len(parked1) == 1 and parked1[0]["outcome"] == "integrated", f"install A: {results1}"
    assert len(parked2) == 1 and parked2[0]["outcome"] == "integrated", f"install B: {results2}"
    for result in results1 + results2:
        assert result["outcome"] in ("integrated", "no_change"), f"refused: {result}"

    # Both installations hold the exact union of the captured rows.
    rows1_after = _stored_rows(sync1)
    rows2_after = _stored_rows(sync2)
    assert {(r[0], r[1], r[2]) for r in rows1_after} == expected_union, (
        "install A does not hold the exact union of captured rows"
    )
    assert {(r[0], r[1], r[2]) for r in rows2_after} == expected_union, (
        "install B does not hold the exact union of captured rows"
    )
    # Each new event retained its own signer.
    stored1 = decode_event(_encoded_of(rows1_after, event1.event_id))
    stored2 = decode_event(_encoded_of(rows1_after, event2.event_id))
    stored1_b = decode_event(_encoded_of(rows2_after, event1.event_id))
    stored2_b = decode_event(_encoded_of(rows2_after, event2.event_id))
    for event, decoded in (
        (event1, stored1),
        (event2, stored2),
        (event1, stored1_b),
        (event2, stored2_b),
    ):
        assert decoded.signer_public_key == event.signer_public_key, (
            f"signer changed for {event.payload['label']}"
        )
        assert decoded.payload == {"label": event.payload["label"]}
    # Pending is empty on both.
    assert _pending_rows(sync1) == [], f"install A pending: {_pending_rows(sync1)}"
    assert _pending_rows(sync2) == [], f"install B pending: {_pending_rows(sync2)}"

    # Step 5, second sweep: fresh managers report no changes and move no heads.
    _commit_dirty(sync1, "S12 install A after sweep one")
    _commit_dirty(sync2, "S12 install B after sweep one")
    head1_after = Repo(sync1 / ".git", sync1).head()
    head2_after = Repo(sync2 / ".git", sync2).head()
    manager1 = TeamManager(root1, alice_hex)
    manager2 = TeamManager(root2, alice_hex)
    resweep1 = manager1.integrate_core_sources(TEAM)
    resweep2 = manager2.integrate_core_sources(TEAM)
    for result in resweep1 + resweep2:
        assert result["outcome"] == "no_change", f"second sweep changed state: {result}"
        assert result["new_events"] == 0, f"second sweep added events: {result}"
    assert Repo(sync1 / ".git", sync1).head() == head1_after, "second sweep moved install A's head"
    assert Repo(sync2 / ".git", sync2).head() == head2_after, "second sweep moved install B's head"
    assert _stored_rows(sync1) == rows1_after, "second sweep altered install A's rows"
    assert _stored_rows(sync2) == rows2_after, "second sweep altered install B's rows"
