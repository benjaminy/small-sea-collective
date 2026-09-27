"""Micro tests for first-use signing setup."""

from concurrent.futures import ThreadPoolExecutor
import pathlib
import sqlite3
import subprocess

import pytest

import small_sea_manager.provisioning as provisioning
from small_sea_note_to_self.db import device_local_db_path


def _setup(root):
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    berth = provisioning.derive_team_join_state(root, participant, "ProjectX")["berth_id"]
    return participant, bytes.fromhex(berth) if isinstance(berth, str) else berth


def _state(root, participant):
    sync = root / "Participants" / participant / "ProjectX" / "Sync"
    with sqlite3.connect(sync / "core.db") as conn:
        count = conn.execute("SELECT count(*) FROM workhorse_delegation").fetchone()[0]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=sync).strip()
    return count, head


def test_first_call_and_repeat_are_idempotent(playground_dir):
    root = pathlib.Path(playground_dir)
    participant, berth = _setup(root)
    before = _state(root, participant)
    first = provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth)
    after = _state(root, participant)
    assert after[0] == 1 and after[1] != before[1]
    assert provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth) == first
    assert _state(root, participant) == after


def test_existing_key_gets_delegation(playground_dir):
    root = pathlib.Path(playground_dir)
    participant, berth = _setup(root)
    key = provisioning.get_workhorse_signing_key(root, participant, berth)
    assert _state(root, participant)[0] == 0
    assert provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth) == key
    assert _state(root, participant)[0] == 1


def test_refusals(playground_dir):
    root = pathlib.Path(playground_dir)
    participant, berth = _setup(root)
    with pytest.raises(provisioning.SigningSetupRefusedError) as exc:
        provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", b"x" * 16)
    assert exc.value.code == "berth_not_held"
    assert not list(root.rglob("workhorse-*.key"))
    team_id, _ = provisioning._team_row(root, participant, "ProjectX")
    with sqlite3.connect(device_local_db_path(root, participant)) as conn:
        conn.execute("DELETE FROM team_authority_anchor WHERE team_id = ?", (team_id,))
    with pytest.raises(provisioning.SigningSetupRefusedError) as exc:
        provisioning.ensure_signing_is_set_up(root, participant, "ProjectX", berth)
    assert exc.value.code == "authority_anchor_absent"


def test_concurrent_calls_create_one_key_and_delegation(playground_dir):
    root = pathlib.Path(playground_dir)
    participant, berth = _setup(root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(provisioning.ensure_signing_is_set_up,
                               root, participant, "ProjectX", berth) for _ in range(2)]
        keys = [future.result() for future in futures]
    assert keys[0] == keys[1]
    assert _state(root, participant)[0] == 1
    assert len(list(root.rglob("workhorse-*.key"))) == 1
