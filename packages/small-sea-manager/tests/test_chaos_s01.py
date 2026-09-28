"""Chaos scenario S01: Alice, Bob, and Carol converge after different arrival orders.

Each participant appends one uniquely labeled "chaos_probe" event on their own
Core, then the three participants integrate the captured pre-integration
snapshots in three different orders. The event set must converge to the union
regardless of arrival order, and replaying the same snapshots must be a clean
no-op that leaves Git heads unchanged.
"""

import pathlib
import sqlite3
import subprocess

from sqlalchemy import create_engine

from cod_sync.repo import Repo
from small_sea_manager import constitution_store, provisioning
from test_admission_records import _admit, _setup_team


def _sync_dir(root: pathlib.Path, participant_hex: str) -> pathlib.Path:
    return root / "Participants" / participant_hex / "ProjectX" / "Sync"


def _repo(root: pathlib.Path, participant_hex: str) -> Repo:
    sync = _sync_dir(root, participant_hex)
    return Repo(sync / ".git", sync)


def _admit_with_package(root, alice_hex, invitee_hex, cloud, *, label):
    acceptance = _admit(root, alice_hex, invitee_hex, cloud, label=label)
    raw = provisioning.export_admission_package(
        root, alice_hex, "ProjectX", bytes.fromhex(acceptance["record_id"])
    )
    assert provisioning.import_admission_package(root, invitee_hex, "ProjectX", raw) is True


def _append_probe(root: pathlib.Path, participant_hex: str, label: str) -> None:
    """Append one labeled chaos_probe event with the participant's device key and commit Core."""
    sync = _sync_dir(root, participant_hex)
    private_key, _public_key = provisioning.get_current_team_device_key(
        root, participant_hex, "ProjectX"
    )
    engine = create_engine(f"sqlite:///{sync / 'core.db'}")
    with engine.begin() as conn:
        constitution_store.append_local_event(
            conn, "chaos_probe", {"label": label}, private_key
        )
    engine.dispose()
    repo = _repo(root, participant_hex)
    if repo.work_tree_paths_differ_from_head(["core.db"]):
        repo.stage(["core.db"])
        assert repo.commit(f"Append chaos probe {label}") is not None


def _event_rows(root: pathlib.Path, participant_hex: str) -> dict:
    with sqlite3.connect(_sync_dir(root, participant_hex) / "core.db") as conn:
        rows = conn.execute(
            "SELECT event_id, event_type, encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _pending_rows(root: pathlib.Path, participant_hex: str) -> list:
    with sqlite3.connect(_sync_dir(root, participant_hex) / "core.db") as conn:
        return conn.execute(
            "SELECT event_id, encoded FROM constitution_event_pending ORDER BY event_id"
        ).fetchall()


def _teammate_hex(root: pathlib.Path, participant_hex: str) -> str:
    value = provisioning.derive_team_join_state(root, participant_hex, "ProjectX")["self_in_team"]
    assert value is not None
    return value.hex() if isinstance(value, bytes) else value


def _transfer(
    root: pathlib.Path,
    receiver_hex: str,
    sender_hex: str,
    sha: str,
    tag: str,
) -> dict:
    """Fetch the sender's captured SHA into a fresh parked ref, then integrate it."""
    receiver_sync = _sync_dir(root, receiver_hex)
    sender_sync = _sync_dir(root, sender_hex)
    receiver_repo = _repo(root, receiver_hex)
    main_before = receiver_repo.head()
    ref = f"refs/small-sea/core-peer/{_teammate_hex(root, sender_hex)}/observations/{tag}"
    subprocess.run(
        [
            "git",
            "-C",
            str(receiver_sync),
            "fetch",
            "--no-tags",
            str(sender_sync),
            f"{sha}:{ref}",
        ],
        check=True,
    )
    # Fetching must leave the receiver's worktree and main unchanged.
    assert receiver_repo.head() == main_before
    assert receiver_repo.work_tree_paths_differ_from_head(["core.db"]) is False
    return provisioning.integrate_core_events(root, receiver_hex, "ProjectX", sha)


def test_s01_three_way_convergence_after_different_arrival_orders(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, _alice_sync = _setup_team(root)
    _admit_with_package(root, alice_hex, bob_hex, cloud, label="Bob")
    carol_hex = provisioning.create_new_participant(root, "Carol")
    _admit_with_package(root, alice_hex, carol_hex, cloud, label="Carol")

    # 1. Each participant appends one uniquely labeled chaos_probe event, then commits Core.
    for participant_hex, label in (
        (alice_hex, "A"),
        (bob_hex, "B"),
        (carol_hex, "C"),
    ):
        _append_probe(root, participant_hex, label)

    # 2. Capture all three SHAs and the union of their event rows before any integration.
    shas = {
        participant_hex: _repo(root, participant_hex).head()
        for participant_hex in (alice_hex, bob_hex, carol_hex)
    }
    assert all(sha for sha in shas.values())
    union = {}
    for participant_hex in (alice_hex, bob_hex, carol_hex):
        union.update(_event_rows(root, participant_hex))
    assert len(union) >= 3  # at least the three probes
    # Non-vacuity: before integration nobody has the full union yet, so the
    # final equality below actually exercises cross-participant convergence.
    for participant_hex in (alice_hex, bob_hex, carol_hex):
        assert _event_rows(root, participant_hex) != union, participant_hex

    # 3. Each participant integrates the other two snapshots in a different order.
    orders = {
        alice_hex: [(bob_hex, "s01-a"), (carol_hex, "s01-b")],
        bob_hex: [(carol_hex, "s01-a"), (alice_hex, "s01-b")],
        carol_hex: [(alice_hex, "s01-a"), (bob_hex, "s01-b")],
    }
    for receiver_hex, pairs in orders.items():
        for sender_hex, tag in pairs:
            result = _transfer(root, receiver_hex, sender_hex, shas[sender_hex], tag)
            assert result["outcome"] == "integrated", (receiver_hex, sender_hex, result)
            assert result["code"] is None, (receiver_hex, sender_hex, result)

    heads_after_integration = {
        participant_hex: _repo(root, participant_hex).head()
        for participant_hex in orders
    }

    # 4. Replay both foreign snapshots on each participant.
    for receiver_hex, pairs in orders.items():
        for sender_hex, tag in pairs:
            result = _transfer(root, receiver_hex, sender_hex, shas[sender_hex], tag)
            assert result == {"outcome": "no_change", "code": None, "new_events": 0}, (
                receiver_hex,
                sender_hex,
                result,
            )
            assert (
                _repo(root, receiver_hex).head() == heads_after_integration[receiver_hex]
            ), f"replay moved {receiver_hex} Git head"

    # Assert: all three event tables equal the captured union, and pending is empty.
    for participant_hex in (alice_hex, bob_hex, carol_hex):
        assert _event_rows(root, participant_hex) == union, participant_hex
        assert _pending_rows(root, participant_hex) == [], participant_hex
