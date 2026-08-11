"""The participant's own label for their own device.

Lives in shared NoteToSelf, never reaches a team database, and is never consulted
by the trust path. See packages/small-sea-manager/spec.md.
"""

import pathlib
import sqlite3

from sandbox.workspace import SandboxWorkspace
from small_sea_manager.provisioning import create_new_participant, create_team
from small_sea_note_to_self.db import note_to_self_sync_db_path


def _device_labels(root_dir, participant_hex):
    with sqlite3.connect(str(note_to_self_sync_db_path(root_dir, participant_hex))) as conn:
        return [row[0] for row in conn.execute("SELECT label FROM user_device ORDER BY id")]


def test_initial_device_label_round_trips(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice", device_label="Alice's laptop")

    assert _device_labels(root, alice_hex) == ["Alice's laptop"]


def test_omitted_device_label_stays_null(playground_dir):
    """Omission must not fall back to the nickname or anything about the host."""
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")

    assert _device_labels(root, alice_hex) == [None]


def test_device_label_does_not_reach_team_device(playground_dir):
    """The rejected alternative was a plain label column on the team-synced device row.

    A durable synced row is unerasable and device names leak hardware, employer, and
    travel patterns, so this guards the specific mistake the design record names.
    """
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice", device_label="Alice's laptop")
    create_team(root, alice_hex, "ProjectX")

    team_db = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(team_db)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(team_device)")}
    assert columns, "expected a team_device table in the team database"
    assert "label" not in columns


def test_sandbox_tooling_labels_the_device_it_creates(playground_dir):
    """Something other than the tests exercises the field."""
    workspace_dir = pathlib.Path(playground_dir)
    workspace = SandboxWorkspace(workspace_dir=workspace_dir)

    participant = workspace.add_participant("Alice")

    labels = _device_labels(workspace_dir, participant.hex)
    assert labels == ["Sandbox device"]
