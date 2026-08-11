"""The participant's own label for their own device.

Lives in shared NoteToSelf, never reaches a team database, and is never consulted
by the trust path. See packages/small-sea-manager/spec.md.
"""

import base64
import json
import pathlib
import sqlite3

import cod_sync.protocol as CodSync
import pytest
from sandbox.workspace import SandboxWorkspace
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.provisioning import (
    add_cloud_storage,
    create_new_participant,
    create_team,
)
from small_sea_note_to_self.db import note_to_self_sync_db_path


def _device_labels(root_dir, participant_hex):
    with sqlite3.connect(str(note_to_self_sync_db_path(root_dir, participant_hex))) as conn:
        return [row[0] for row in conn.execute("SELECT label FROM user_device ORDER BY id")]


def _label_by_device_id(root_dir, participant_hex, device_id_hex):
    with sqlite3.connect(str(note_to_self_sync_db_path(root_dir, participant_hex))) as conn:
        row = conn.execute(
            "SELECT label FROM user_device WHERE id = ?",
            (bytes.fromhex(device_id_hex),),
        ).fetchone()
    return row if row is None else row[0]


def _rewrite_join_request_label(join_request_artifact_b64, label):
    """Forge a second artifact for the same device ID and keys but a new label."""
    payload = json.loads(base64.b64decode(join_request_artifact_b64.encode("ascii")).decode("utf-8"))
    payload["device_label"] = label
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _two_installs(workspace, *, authorizer_label="Alice's laptop"):
    """An established participant on install-a and a blank install-b."""
    root1 = workspace / "install-a"
    root2 = workspace / "install-b"
    cloud_dir = workspace / "cloud"
    root1.mkdir()
    root2.mkdir()
    cloud_dir.mkdir()

    alice_hex = create_new_participant(root1, "Alice", device_label=authorizer_label)
    add_cloud_storage(root1, alice_hex, protocol="localfolder", url=str(cloud_dir))
    return root1, root2, alice_hex


def test_initial_device_label_round_trips(playground_dir):
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice", device_label="Alice's laptop")

    assert _device_labels(root, alice_hex) == ["Alice's laptop"]


def test_omitted_device_label_stays_null(playground_dir):
    """Omission must not fall back to the nickname or anything about the host."""
    root = pathlib.Path(playground_dir)

    alice_hex = create_new_participant(root, "Alice")

    assert _device_labels(root, alice_hex) == [None]


def test_initial_device_creation_rejects_a_non_string_label_without_side_effects(
    playground_dir,
):
    root = pathlib.Path(playground_dir)

    with pytest.raises(ValueError, match="device_label"):
        create_new_participant(root, "Alice", device_label=42)

    assert list(root.iterdir()) == []


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


def test_joining_device_label_reaches_both_note_to_self_clones(playground_dir):
    """The authorizer records the label the joiner chose, not one picked locally."""
    root1, root2, alice_hex = _two_installs(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2, device_label="Alice's phone")
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap = bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    joined_hex = bootstrap["joining_device_id_hex"]
    assert _label_by_device_id(root1, alice_hex, joined_hex) == "Alice's phone"
    assert _label_by_device_id(root2, alice_hex, joined_hex) == "Alice's phone"

    # The authorizing device keeps its own label; the two are independent.
    assert sorted(_device_labels(root1, alice_hex)) == ["Alice's laptop", "Alice's phone"]


def test_authorizer_writes_the_artifacts_label_not_its_own(playground_dir):
    """The load-bearing assertion: a locally chosen label would be indistinguishable
    from the artifact's if the authorizer also had no label of its own."""
    root1, root2, alice_hex = _two_installs(
        pathlib.Path(playground_dir), authorizer_label="Authorizer laptop"
    )

    join_request = create_identity_join_request(root2, device_label="Joiner phone")
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap = bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    joined_label = _label_by_device_id(root1, alice_hex, bootstrap["joining_device_id_hex"])
    assert joined_label == "Joiner phone"
    assert joined_label != "Authorizer laptop"
    assert joined_label != "Alice"  # nor the participant nickname


def test_omitted_joining_device_label_stays_null(playground_dir):
    root1, root2, alice_hex = _two_installs(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2)
    alice_manager = TeamManager(root1, alice_hex)
    welcome = alice_manager.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap = bootstrap_existing_identity(root2, welcome["welcome_bundle"])

    assert _label_by_device_id(root1, alice_hex, bootstrap["joining_device_id_hex"]) is None


def test_join_request_creation_rejects_a_non_string_label_without_side_effects(
    playground_dir,
):
    root = pathlib.Path(playground_dir)

    with pytest.raises(ValueError, match="device_label"):
        create_identity_join_request(root, device_label=42)

    assert list(root.iterdir()) == []


def test_reauthorizing_the_same_request_adds_no_row_and_no_commit(playground_dir):
    """Reissuing a welcome bundle is idempotent."""
    root1, root2, alice_hex = _two_installs(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2, device_label="Alice's phone")
    alice_manager = TeamManager(root1, alice_hex)
    alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    sync_dir = root1 / "Participants" / alice_hex / "NoteToSelf" / "Sync"
    head_before = CodSync.gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()

    alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    assert _device_labels(root1, alice_hex) == ["Alice's laptop", "Alice's phone"]
    head_after = CodSync.gitCmd(["-C", str(sync_dir), "rev-parse", "HEAD"]).stdout.strip()
    assert head_after == head_before


def test_relabelling_an_admitted_device_is_refused(playground_dir):
    """Relabelling is a separate operation and must not ride along on a retry."""
    root1, root2, alice_hex = _two_installs(pathlib.Path(playground_dir))

    join_request = create_identity_join_request(root2, device_label="Alice's phone")
    alice_manager = TeamManager(root1, alice_hex)
    alice_manager.authorize_identity_join(join_request["join_request_artifact"])

    relabelled = _rewrite_join_request_label(
        join_request["join_request_artifact"], "Alice's work phone"
    )
    with pytest.raises(ValueError, match="different label"):
        alice_manager.authorize_identity_join(relabelled)

    assert _device_labels(root1, alice_hex) == ["Alice's laptop", "Alice's phone"]


def test_sandbox_tooling_labels_the_device_it_creates(playground_dir):
    """Something other than the tests exercises the field."""
    workspace_dir = pathlib.Path(playground_dir)
    workspace = SandboxWorkspace(workspace_dir=workspace_dir)

    participant = workspace.add_participant("Alice")

    labels = _device_labels(workspace_dir, participant.hex)
    assert labels == ["Sandbox device"]
