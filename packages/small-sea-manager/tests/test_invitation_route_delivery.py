"""Micro tests for first-contact route delivery (issue #183).

The invitee's Core storage route is prepared after Hub materialization, held in
a pending state until it lands, and couriered beside -- never inside -- the
signed `admission_acceptance`. These cover the pending-state machinery, the
export gate, the device-local acceptance artifact, and the acceptance-scoped
sidecar verification on the inviter's side.

Most run over LocalFolderStore with a fake Hub session. The two that must see a
real provider-issued locator writeback use a real Hub backend with a stubbed
storage adapter.
"""

import base64
import json
import pathlib
import sqlite3
import subprocess
from dataclasses import replace

import cod_sync.protocol as CS
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from fastapi.testclient import TestClient
from small_sea_client.client import (
    SmallSeaCloudStorageRequired,
    SmallSeaError,
    SmallSeaHubUnavailable,
)
from small_sea_hub.cloud_errors import MaterializationOutcome
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager
from small_sea_note_to_self.db import (
    AcceptanceArtifactAlreadyExportedError,
    list_admission_acceptance_artifacts,
    save_admission_acceptance_artifact,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    canonical_teammate_berth_storage_announcement_bytes,
)

TEAM = "ProjectX"


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #


def _push(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    CS.CodSync(
        Repo(repo_dir / ".git", repo_dir), LocalFolderStore(str(cloud_dir))
    ).publish()


def _sync_dir(root, participant_hex) -> pathlib.Path:
    return root / "Participants" / participant_hex / TEAM / "Sync"


def _setup(root: pathlib.Path, *, bob_cloud: bool = True):
    """Alice with a team; Bob provisioned but not yet invited."""
    alice_cloud = root / "alice-cloud"
    alice_cloud.mkdir()
    alice_hex = provisioning.create_new_participant(root, "Alice")
    bob_hex = provisioning.create_new_participant(root, "Bob")
    provisioning.add_cloud_storage(
        root, alice_hex, protocol="localfolder", url=str(alice_cloud)
    )
    if bob_cloud:
        bob_cloud_dir = root / "bob-cloud"
        bob_cloud_dir.mkdir()
        provisioning.add_cloud_storage(
            root, bob_hex, protocol="localfolder", url=str(bob_cloud_dir)
        )
    provisioning.create_team(root, alice_hex, TEAM)
    _push(_sync_dir(root, alice_hex), alice_cloud)
    return alice_hex, bob_hex, alice_cloud


def _invite(root, alice_hex, alice_cloud, label="Bob") -> str:
    token = provisioning.create_invitation(
        root,
        alice_hex,
        TEAM,
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label=label,
    )
    _push(_sync_dir(root, alice_hex), alice_cloud)
    return token


def _accept_locally(root, invitee_hex, token, alice_cloud) -> str:
    """Run the invitee's local ceremony. Returns the base acceptance token."""
    return provisioning.accept_invitation(
        root, invitee_hex, token, inviter_store=LocalFolderStore(str(alice_cloud))
    )


class _FakeSession:
    """Stands in for a Hub team session during route preparation."""

    def __init__(self, *, error=None, on_ready=None):
        self.error = error
        self.on_ready = on_ready
        self.calls = 0

    def ensure_cloud_ready(self):
        self.calls += 1
        if self.on_ready is not None:
            self.on_ready()
        if self.error is not None:
            raise self.error


def _manager(root, participant_hex, *, session=None, session_error=None):
    mgr = TeamManager(root, participant_hex)

    def _open(team, mode="encrypted"):
        if session_error is not None:
            raise session_error
        assert mode == "encrypted", f"route preparation used mode {mode!r}"
        return session

    mgr._get_or_open_session = _open
    return mgr


def _bob_announcement(root, bob_hex) -> TeammateBerthStorageAnnouncement:
    state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    return provisioning.selected_own_berth_storage_announcement(
        root, bob_hex, TEAM, state
    )


def _stored_announcements(db_path, teammate_id: bytes):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature "
            "FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
            (teammate_id,),
        ).fetchall()
    finally:
        conn.close()


def _prepared_bob(root, alice_cloud, alice_hex, bob_hex, token):
    """Bob accepts and reaches a ready route. Returns his courier token."""
    _accept_locally(root, bob_hex, token, alice_cloud)
    mgr = _manager(root, bob_hex, session=_FakeSession())
    report = mgr.prepare_team_route(TEAM)
    assert report["route"] == "ready", report
    return mgr.export_admission_acceptance(TEAM)["acceptance_token"]


# --------------------------------------------------------------------------- #
# 1. Core berth resolution
# --------------------------------------------------------------------------- #


def test_ambiguous_core_berth_raises(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex, _cloud = _setup(root)
    db_path = _sync_dir(root, alice_hex) / "core.db"

    conn = sqlite3.connect(str(db_path))
    with conn:
        berth_id, app_id = conn.execute(
            "SELECT tab.id, tab.app_id FROM team_app_berth tab "
            "JOIN app a ON a.id = tab.app_id WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (berth_id[:-1] + bytes([berth_id[-1] ^ 1]), app_id),
        )
    conn.close()

    engine = provisioning._sqlite_engine(db_path)
    try:
        with engine.begin() as conn:
            with pytest.raises(provisioning.AmbiguousCoreBerthError):
                provisioning._core_berth_id(conn)
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# 2-3. The local ceremony publishes no route and persists one artifact
# --------------------------------------------------------------------------- #


def test_accept_invitation_publishes_no_route(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    token = _invite(root, alice_hex, alice_cloud)

    base_token = _accept_locally(root, bob_hex, token, alice_cloud)

    state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    assert state["join"] == "complete"
    assert state["admission"] == "pending"
    assert state["route"] == "pending"
    # An allocation was chosen locally, but nothing was signed over it.
    assert state["allocation"] is not None
    assert _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db", state["self_in_team"]
    ) == []

    artifacts = list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])
    assert len(artifacts) == 1
    assert artifacts[0]["acceptance_token"] == base_token
    assert artifacts[0]["first_exported_at"] is None
    assert artifacts[0]["author_teammate_id"] == state["self_in_team"]
    assert artifacts[0]["author_device_key_id"] == state["device_key_id"]


def test_artifact_write_is_idempotent_and_immutable_after_export(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    token = _invite(root, alice_hex, alice_cloud)
    base_token = _accept_locally(root, bob_hex, token, alice_cloud)

    state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    stored = list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])[0]
    fields = {
        "team_id": stored["team_id"],
        "proposal_id": stored["proposal_id"],
        "nonce": stored["nonce"],
        "author_teammate_id": stored["author_teammate_id"],
        "author_device_key_id": stored["author_device_key_id"],
        "acceptance_record_id": stored["acceptance_record_id"],
    }

    # Identical write: no-op, same bytes and record_id.
    save_admission_acceptance_artifact(
        root, bob_hex, acceptance_token=base_token, **fields
    )
    again = list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])[0]
    assert again["acceptance_token"] == base_token
    assert again["acceptance_record_id"] == stored["acceptance_record_id"]

    # Never exported: a differently signed artifact may replace it.
    save_admission_acceptance_artifact(
        root, bob_hex, acceptance_token="second-signed-acceptance", **fields
    )
    assert (
        list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])[0][
            "acceptance_token"
        ]
        == "second-signed-acceptance"
    )

    # Exported: replacement is refused, but re-export of the same bytes is not.
    provisioning.mark_admission_acceptance_artifact_exported(
        root, bob_hex, stored["team_id"], stored["proposal_id"]
    )
    with pytest.raises(AcceptanceArtifactAlreadyExportedError):
        save_admission_acceptance_artifact(
            root, bob_hex, acceptance_token="third-signed-acceptance", **fields
        )
    save_admission_acceptance_artifact(
        root, bob_hex, acceptance_token="second-signed-acceptance", **fields
    )
    assert (
        list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])[0][
            "acceptance_token"
        ]
        == "second-signed-acceptance"
    )


def test_artifact_stays_device_local(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    shared = root / "Participants" / bob_hex / "NoteToSelf" / "Sync" / "core.db"
    conn = sqlite3.connect(str(shared))
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT 1 FROM admission_acceptance_artifact")
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 4. Route preparation is retryable, and names its reason
# --------------------------------------------------------------------------- #


def test_route_pending_without_cloud_storage_then_ready_after_adding(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root, bob_cloud=False)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    session = _FakeSession()
    mgr = _manager(root, bob_hex, session=session)
    report = mgr.prepare_team_route(TEAM)
    assert report["join"] == "complete"
    assert report["route"] == "pending"
    assert report["route_reason"] == "storage_not_configured"
    assert report["acceptance"] == "withheld"
    assert report["acceptance_reason"] == "route_pending"
    # No storage means no provider to contact.
    assert session.calls == 0

    bob_cloud = root / "bob-cloud-late"
    bob_cloud.mkdir()
    provisioning.add_cloud_storage(
        root, bob_hex, protocol="localfolder", url=str(bob_cloud)
    )
    retried = mgr.prepare_team_route(TEAM)
    assert retried["route"] == "ready"
    assert retried["route_reason"] is None
    assert retried["acceptance"] == "exportable"
    assert session.calls == 1


@pytest.mark.parametrize(
    "error, expected",
    [
        (SmallSeaHubUnavailable(), "hub_session_unavailable"),
        (SmallSeaError("not in auto-approve mode"), "hub_session_unavailable"),
    ],
)
def test_route_pending_when_no_hub_session(playground_dir, error, expected):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    mgr = _manager(root, bob_hex, session_error=error)
    report = mgr.prepare_team_route(TEAM)
    assert report["route"] == "pending"
    assert report["route_reason"] == expected
    assert report["acceptance"] == "withheld"
    assert mgr.export_admission_acceptance(TEAM)["acceptance_token"] is None


@pytest.mark.parametrize(
    "cloud_reason, expected",
    [
        ("cloud_user_action_required", "user_action_required"),
        ("cloud_materialization_failed", "materialization_failed"),
        ("cloud_allocation_conflict", "allocation_conflict"),
        ("cloud_location_missing", "storage_not_configured"),
        ("cloud_credentials_missing", "storage_not_configured"),
        ("announcement_missing", "route_preparation_error"),
    ],
)
def test_each_cloud_setup_failure_leaves_a_retryable_pending_route(
    playground_dir, cloud_reason, expected
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    failing = _FakeSession(
        error=SmallSeaCloudStorageRequired(cloud_reason, cloud_reason)
    )
    mgr = _manager(root, bob_hex, session=failing)
    report = mgr.prepare_team_route(TEAM)
    assert report["route"] == "pending"
    assert report["route_reason"] == expected
    assert report["acceptance"] == "withheld"
    assert _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db",
        provisioning.derive_team_join_state(root, bob_hex, TEAM)["self_in_team"],
    ) == []

    # The local join survived; a retry against a working Hub reaches ready.
    mgr._get_or_open_session = lambda team, mode="encrypted": _FakeSession()
    assert mgr.prepare_team_route(TEAM)["route"] == "ready"


def test_publication_failure_leaves_join_pending(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(
        provisioning, "publish_teammate_berth_storage_announcement", _boom
    )
    mgr = _manager(root, bob_hex, session=_FakeSession())
    report = mgr.prepare_team_route(TEAM)
    assert report["route"] == "pending"
    assert report["route_reason"] == "route_preparation_error"
    assert report["acceptance"] == "withheld"

    monkeypatch.undo()
    assert mgr.prepare_team_route(TEAM)["route"] == "ready"


def test_commit_failure_is_retryable_without_recontacting_provider(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    session = _FakeSession()
    mgr = _manager(root, bob_hex, session=session)
    repo = mgr._team_repo(TEAM)
    real_commit_paths = repo.commit_paths
    attempts = 0

    def _fail_twice(paths, message):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError("injected commit failure")
        return real_commit_paths(paths, message)

    monkeypatch.setattr(repo, "commit_paths", _fail_twice)
    monkeypatch.setattr(mgr, "_team_repo", lambda team_name: repo)

    first = mgr.prepare_team_route(TEAM)
    assert first["route"] == "pending"
    assert first["route_reason"] == "route_preparation_error"
    assert first["acceptance"] == "withheld"
    assert repo.work_tree_paths_differ_from_head(["core.db"])

    direct_export = mgr.export_admission_acceptance(TEAM)
    assert direct_export["route"] == "pending"
    assert direct_export["route_reason"] == "route_preparation_error"
    assert direct_export["acceptance"] == "withheld"
    assert direct_export["acceptance_token"] is None
    assert repo.work_tree_paths_differ_from_head(["core.db"])

    retried = mgr.prepare_team_route(TEAM)
    assert retried["route"] == "ready"
    assert retried["acceptance"] == "exportable"
    assert not repo.work_tree_paths_differ_from_head(["core.db"])
    assert session.calls == 1


def test_route_preparation_skips_the_provider_without_an_eligible_artifact(
    playground_dir,
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    conn = sqlite3.connect(
        str(provisioning.device_local_db_path(root, bob_hex))
    )
    with conn:
        conn.execute("DELETE FROM admission_acceptance_artifact")
    conn.close()

    session = _FakeSession()
    report = _manager(root, bob_hex, session=session).prepare_team_route(TEAM)
    assert report["route"] == "pending"
    assert report["acceptance"] == "withheld"
    assert report["acceptance_reason"] == "artifact_missing"
    assert session.calls == 0
    assert _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db", state["self_in_team"]
    ) == []


# --------------------------------------------------------------------------- #
# 5. The export gate
# --------------------------------------------------------------------------- #


def test_export_is_withheld_until_the_route_is_ready_then_stable(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root, bob_cloud=False)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    mgr = _manager(root, bob_hex, session=_FakeSession())
    withheld = mgr.export_admission_acceptance(TEAM)
    assert withheld["acceptance"] == "withheld"
    assert withheld["acceptance_reason"] == "route_pending"
    assert withheld["acceptance_token"] is None

    bob_cloud = root / "bob-cloud-late"
    bob_cloud.mkdir()
    provisioning.add_cloud_storage(
        root, bob_hex, protocol="localfolder", url=str(bob_cloud)
    )
    assert mgr.prepare_team_route(TEAM)["route"] == "ready"

    first = mgr.export_admission_acceptance(TEAM)["acceptance_token"]
    second = mgr.export_admission_acceptance(TEAM)["acceptance_token"]
    assert first is not None
    # No intervening route change, so repeated exports are byte-identical.
    assert first == second


def test_export_marks_first_export_and_refuses_replacement_afterwards(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)
    mgr = _manager(root, bob_hex, session=_FakeSession())
    mgr.prepare_team_route(TEAM)
    assert mgr.export_admission_acceptance(TEAM)["acceptance_token"] is not None

    state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    stored = list_admission_acceptance_artifacts(root, bob_hex, state["team_id"])[0]
    assert stored["first_exported_at"] is not None
    with pytest.raises(AcceptanceArtifactAlreadyExportedError):
        save_admission_acceptance_artifact(
            root,
            bob_hex,
            team_id=stored["team_id"],
            proposal_id=stored["proposal_id"],
            nonce=stored["nonce"],
            author_teammate_id=stored["author_teammate_id"],
            author_device_key_id=stored["author_device_key_id"],
            acceptance_record_id=stored["acceptance_record_id"],
            acceptance_token="a-second-signed-acceptance",
        )


@pytest.mark.parametrize("field", ["author_teammate_id", "author_device_key_id"])
def test_a_stale_artifact_is_never_exported(playground_dir, field):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)
    mgr = _manager(root, bob_hex, session=_FakeSession())
    mgr.prepare_team_route(TEAM)

    local_db = provisioning.device_local_db_path(root, bob_hex)
    conn = sqlite3.connect(str(local_db))
    with conn:
        current = conn.execute(
            f"SELECT {field} FROM admission_acceptance_artifact"
        ).fetchone()[0]
        conn.execute(
            f"UPDATE admission_acceptance_artifact SET {field} = ?",
            (current[:-1] + bytes([current[-1] ^ 1]),),
        )
    conn.close()

    report = mgr.export_admission_acceptance(TEAM)
    assert report["route"] == "ready"
    assert report["acceptance"] == "withheld"
    assert report["acceptance_reason"] == "artifact_stale"
    assert report["acceptance_token"] is None


def test_export_works_without_the_proposal_row_in_the_invitee_clone(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    # `create_invitation` commits the proposal but never pushes, so an invitee
    # whose inviter forgot to push holds no copy of their own proposal.
    token = provisioning.create_invitation(
        root,
        alice_hex,
        TEAM,
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _accept_locally(root, bob_hex, token, alice_cloud)

    bob_db = _sync_dir(root, bob_hex) / "core.db"
    conn = sqlite3.connect(str(bob_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM admission_proposal").fetchone()[0] == 0
    finally:
        conn.close()

    mgr = _manager(root, bob_hex, session=_FakeSession())
    assert mgr.prepare_team_route(TEAM)["route"] == "ready"
    assert mgr.export_admission_acceptance(TEAM)["acceptance_token"] is not None


# --------------------------------------------------------------------------- #
# 6-7. The sidecar is the stored row, carried outside the signed fields
# --------------------------------------------------------------------------- #


def test_sidecar_is_the_stored_row_and_leaves_the_acceptance_untouched(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    token = _invite(root, alice_hex, alice_cloud)
    base_token = _accept_locally(root, bob_hex, token, alice_cloud)

    mgr = _manager(root, bob_hex, session=_FakeSession())
    mgr.prepare_team_route(TEAM)
    courier = mgr.export_admission_acceptance(TEAM)["acceptance_token"]

    payload = json.loads(base64.b64decode(courier).decode())
    assert payload["admission_acceptance"] == base_token
    assert payload["route"] == provisioning.serialize_berth_storage_announcement(
        _bob_announcement(root, bob_hex)
    )

    # Canonical bytes and record_id of the acceptance are unchanged by the
    # attachment: the route never enters its signed fields.
    acceptance = json.loads(base64.b64decode(base_token).decode())
    assert set(acceptance) == {
        "record_id",
        "record_type",
        "author_teammate_id",
        "author_device_key_id",
        "created_at",
        "subject_record_id",
        "nonce",
        "team_id",
        "invitee_device_public_key",
        "invitee_bootstrap_key",
        "signature",
    }


def test_read_back_signed_by_another_device_is_refused_before_export(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    real_read = provisioning.read_teammate_berth_storage_announcement

    def _other_signer(*args, **kwargs):
        announcement = real_read(*args, **kwargs)
        return replace(announcement, signer_key_id=b"\x00" * len(announcement.signer_key_id))

    monkeypatch.setattr(
        provisioning, "read_teammate_berth_storage_announcement", _other_signer
    )
    mgr = _manager(root, bob_hex, session=_FakeSession())
    report = mgr.prepare_team_route(TEAM)
    assert report["route"] == "pending"
    assert report["route_reason"] == "route_preparation_error"
    assert report["acceptance"] == "withheld"


# --------------------------------------------------------------------------- #
# 8-9. Acceptance-scoped sidecar verification on the inviter's side
# --------------------------------------------------------------------------- #


def _tamper(courier: str, mutate) -> str:
    payload = json.loads(base64.b64decode(courier).decode())
    payload["route"] = mutate(dict(payload["route"]))
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _resign(root, bob_hex, route: dict, **overrides) -> dict:
    private_key, public_key = provisioning.get_current_team_device_key(
        root, bob_hex, TEAM
    )
    announcement = TeammateBerthStorageAnnouncement(
        announcement_id=bytes.fromhex(route["announcement_id"]),
        teammate_id=bytes.fromhex(route["teammate_id"]),
        berth_id=bytes.fromhex(route["berth_id"]),
        protocol=route["protocol"],
        url=route["url"],
        location=route["location"],
        announced_at=route["announced_at"],
        signer_key_id=key_id_from_public(public_key),
        signature=b"",
    )
    announcement = replace(announcement, **overrides)
    signature = provisioning._sign_bytes(
        private_key, canonical_teammate_berth_storage_announcement_bytes(announcement)
    )
    return provisioning.serialize_berth_storage_announcement(
        replace(announcement, signature=signature)
    )


def _alice_route_rows(root, alice_hex, teammate_id_hex):
    return _stored_announcements(
        _sync_dir(root, alice_hex) / "core.db", bytes.fromhex(teammate_id_hex)
    )


def test_sidecar_delivers_the_route_and_finalizes_admission(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )

    result = provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)
    assert result == {
        "route_delivery": "imported",
        "route_reason": None,
        "admission": "finalized",
    }

    bob_state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    assert _alice_route_rows(
        root, alice_hex, bob_state["self_in_team"].hex()
    ) == _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db", bob_state["self_in_team"]
    )


@pytest.mark.parametrize(
    "name, mutate, expected_reason",
    [
        ("malformed", lambda route: {"announcement_id": "zz"}, "malformed"),
        (
            "bad_signature",
            lambda route: {**route, "signature": "00" * 64},
            "bad_signature",
        ),
    ],
)
def test_sidecar_failures_never_cost_the_invitee_admission(
    playground_dir, name, mutate, expected_reason
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    result = provisioning.complete_invitation_acceptance(
        root, alice_hex, TEAM, _tamper(courier, mutate)
    )
    assert result["route_delivery"] == "invalid"
    assert result["route_reason"] == expected_reason
    assert result["admission"] == "finalized"
    assert _alice_route_rows(root, alice_hex, bob_teammate_id.hex()) == []


def test_a_signed_non_utf8_sidecar_never_costs_admission(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    result = provisioning.complete_invitation_acceptance(
        root,
        alice_hex,
        TEAM,
        _tamper(
            courier,
            lambda route: _resign(root, bob_hex, route, protocol="\ud800"),
        ),
    )
    assert result == {
        "route_delivery": "invalid",
        "route_reason": "malformed",
        "admission": "finalized",
    }
    assert _alice_route_rows(root, alice_hex, bob_teammate_id.hex()) == []


def test_a_wrongly_signed_sidecar_is_invalid(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]
    stranger, _private = generate_key_pair(ProtectionLevel.DAILY)

    result = provisioning.complete_invitation_acceptance(
        root,
        alice_hex,
        TEAM,
        _tamper(
            courier,
            lambda route: {
                **route,
                "signer_key_id": key_id_from_public(stranger.public_key).hex(),
            },
        ),
    )
    assert result["route_delivery"] == "invalid"
    assert result["route_reason"] == "wrong_signer"
    assert result["admission"] == "finalized"
    assert _alice_route_rows(root, alice_hex, bob_teammate_id.hex()) == []


def test_a_sidecar_for_another_teammate_is_invalid(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]
    other = bob_teammate_id[:-1] + bytes([bob_teammate_id[-1] ^ 1])

    result = provisioning.complete_invitation_acceptance(
        root,
        alice_hex,
        TEAM,
        _tamper(courier, lambda route: _resign(root, bob_hex, route, teammate_id=other)),
    )
    assert result["route_delivery"] == "invalid"
    assert result["route_reason"] == "wrong_teammate"
    assert result["admission"] == "finalized"
    assert _alice_route_rows(root, alice_hex, other.hex()) == []


def test_a_validly_signed_non_core_sidecar_is_invalid(playground_dir):
    """The first-contact cycle is only broken by a Core route."""
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    alice_db = _sync_dir(root, alice_hex) / "core.db"
    conn = sqlite3.connect(str(alice_db))
    with conn:
        other_app_id = provisioning.uuid7()
        other_berth_id = provisioning.uuid7()
        conn.execute(
            "INSERT INTO app (id, name) VALUES (?, 'Files')", (other_app_id,)
        )
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (other_berth_id, other_app_id),
        )
    conn.close()

    result = provisioning.complete_invitation_acceptance(
        root,
        alice_hex,
        TEAM,
        _tamper(
            courier,
            lambda route: _resign(root, bob_hex, route, berth_id=other_berth_id),
        ),
    )
    assert result["route_delivery"] == "invalid"
    assert result["route_reason"] == "wrong_berth"
    assert result["admission"] == "finalized"
    assert _alice_route_rows(root, alice_hex, bob_teammate_id.hex()) == []


def test_an_ambiguous_inviter_side_core_berth_is_invalid_not_an_exception(
    playground_dir,
):
    """The sidecar path must never be what aborts an admission.

    Exercised against the helper directly: with an ambiguous Core join the
    inviter's endorsement-authority lookup also cannot resolve Core and fails
    the admission on its own, which would hide whether the sidecar path raised.
    """
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    route = json.loads(base64.b64decode(courier).decode())["route"]
    bob_state = provisioning.derive_team_join_state(root, bob_hex, TEAM)

    alice_db = _sync_dir(root, alice_hex) / "core.db"
    conn = sqlite3.connect(str(alice_db))
    with conn:
        berth_id, app_id = conn.execute(
            "SELECT tab.id, tab.app_id FROM team_app_berth tab "
            "JOIN app a ON a.id = tab.app_id WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (berth_id[:-1] + bytes([berth_id[-1] ^ 1]), app_id),
        )
    conn.close()

    engine = provisioning._sqlite_engine(alice_db)
    try:
        with engine.begin() as conn:
            outcome = provisioning._import_acceptance_route_sidecar(
                conn,
                sidecar=route,
                invitee_device_public_key=bob_state["device_public_key"],
                invitee_teammate_id=bob_state["self_in_team"],
            )
    finally:
        engine.dispose()
    assert outcome == ("invalid", "core_berth_ambiguous")
    assert _alice_route_rows(root, alice_hex, bob_state["self_in_team"].hex()) == []


def test_a_route_less_acceptance_still_completes(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    base_token = _accept_locally(
        root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud
    )

    # Both shapes a stripping courier can produce: the bare record, and an
    # envelope with no route.
    result = provisioning.complete_invitation_acceptance(
        root, alice_hex, TEAM, base_token
    )
    assert result == {
        "route_delivery": "missing",
        "route_reason": "absent",
        "admission": "finalized",
    }


def test_an_id_collision_does_not_cost_admission(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    route = json.loads(base64.b64decode(courier).decode())["route"]
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    # A different row already stored under the same announcement_id.
    conn = sqlite3.connect(str(_sync_dir(root, alice_hex) / "core.db"))
    with conn:
        conn.execute(
            "INSERT INTO teammate_berth_storage_announcement "
            "(announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature) "
            "VALUES (?, ?, ?, 'localfolder', 'file:///elsewhere', 'other', "
            "?, ?, ?)",
            (
                bytes.fromhex(route["announcement_id"]),
                bytes.fromhex(route["teammate_id"]),
                bytes.fromhex(route["berth_id"]),
                route["announced_at"],
                bytes.fromhex(route["signer_key_id"]),
                bytes.fromhex(route["signature"]),
            ),
        )
    conn.close()

    result = provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)
    assert result["route_delivery"] == "conflict"
    assert result["route_reason"] == "announcement_id_collision"
    assert result["admission"] == "finalized"
    # The stored row was not overwritten.
    stored = _alice_route_rows(root, alice_hex, bob_teammate_id.hex())
    assert len(stored) == 1
    assert stored[0][5] == "other"


def test_an_identical_stored_row_is_a_no_op_import(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    route = json.loads(base64.b64decode(courier).decode())["route"]
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    conn = sqlite3.connect(str(_sync_dir(root, alice_hex) / "core.db"))
    with conn:
        conn.execute(
            "INSERT INTO teammate_berth_storage_announcement "
            "(announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bytes.fromhex(route["announcement_id"]),
                bytes.fromhex(route["teammate_id"]),
                bytes.fromhex(route["berth_id"]),
                route["protocol"],
                route["url"],
                route["location"],
                route["announced_at"],
                bytes.fromhex(route["signer_key_id"]),
                bytes.fromhex(route["signature"]),
            ),
        )
    conn.close()

    result = provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)
    assert result["route_delivery"] == "imported"
    assert len(_alice_route_rows(root, alice_hex, bob_teammate_id.hex())) == 1


def test_a_blocked_admission_inserts_no_sidecar(playground_dir):
    """The block_reason path writes nothing and raises afterwards."""
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    token = _invite(root, alice_hex, alice_cloud)
    courier = _prepared_bob(root, alice_cloud, alice_hex, bob_hex, token)
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    proposal_id = json.loads(base64.b64decode(token).decode())["proposal_id"]
    provisioning.revoke_invitation(root, alice_hex, TEAM, proposal_id)

    with pytest.raises(ValueError):
        provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)
    assert _alice_route_rows(root, alice_hex, bob_teammate_id.hex()) == []


def test_a_quorum_pending_row_is_inert_until_finalization(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    provisioning.set_team_admission_policy(root, alice_hex, TEAM, quorum=2)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    result = provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)
    assert result["route_delivery"] == "imported"
    assert result["admission"] == "pending"

    # The row is stored but routes nothing: its signer is not trusted yet.
    assert len(_alice_route_rows(root, alice_hex, bob_teammate_id.hex())) == 1
    alice_team_id, _self = provisioning._team_row(root, alice_hex, TEAM)
    engine = provisioning._sqlite_engine(_sync_dir(root, alice_hex) / "core.db")
    try:
        with engine.begin() as conn:
            selection = provisioning.selected_teammate_berth_storage_announcement(
                conn,
                bob_teammate_id,
                provisioning._core_berth_id(conn),
                team_id=alice_team_id,
            )
    finally:
        engine.dispose()
    assert selection.status == "missing"


# --------------------------------------------------------------------------- #
# 11. Publication happens after materialization, over the final locator
# --------------------------------------------------------------------------- #


class _StubAdapter:
    def __init__(self, outcome):
        self._outcome = outcome

    def materialize(self):
        return self._outcome


def _prepare_through_hub(root, bob_hex, outcome, monkeypatch):
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)
    monkeypatch.setattr(
        SmallSea.SmallSeaBackend,
        "_make_storage_adapter_from_record",
        lambda self, ss_session, cloud: _StubAdapter(outcome),
    )
    mgr = TeamManager(root, bob_hex, _http_client=http)
    return mgr.prepare_team_route(TEAM), mgr


def test_a_provider_issued_locator_is_reread_before_signing(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)

    before = provisioning.derive_team_join_state(root, bob_hex, TEAM)["allocation"]
    final_location = before["location"] + "-final"

    report, mgr = _prepare_through_hub(
        root,
        bob_hex,
        MaterializationOutcome("materialized_with_locator", final_location),
        monkeypatch,
    )
    assert report["route"] == "ready", report

    after = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    assert after["allocation"]["location"] == final_location
    announcement = _bob_announcement(root, bob_hex)
    # Only the final locator is signed: a provisional one would hand peers a
    # stale route and fail Bob's own `_require_own_storage_announcement` check.
    assert announcement.location == final_location

    courier = mgr.export_admission_acceptance(TEAM)["acceptance_token"]
    assert json.loads(base64.b64decode(courier).decode())["route"][
        "location"
    ] == final_location


def test_a_materialization_failure_leaves_a_pending_retryable_join(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    _accept_locally(root, bob_hex, _invite(root, alice_hex, alice_cloud), alice_cloud)
    bob_teammate_id = provisioning.derive_team_join_state(root, bob_hex, TEAM)[
        "self_in_team"
    ]

    report, mgr = _prepare_through_hub(
        root, bob_hex, MaterializationOutcome("failed"), monkeypatch
    )
    assert report["route"] == "pending"
    assert report["route_reason"] == "materialization_failed"
    assert report["acceptance"] == "withheld"
    assert mgr.export_admission_acceptance(TEAM)["acceptance_token"] is None
    assert _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db", bob_teammate_id
    ) == []

    # Storage availability is not admission authority: the provider recovering
    # costs a retry, not the proposal.
    monkeypatch.setattr(
        SmallSea.SmallSeaBackend,
        "_make_storage_adapter_from_record",
        lambda self, ss_session, cloud: _StubAdapter(
            MaterializationOutcome("materialized")
        ),
    )
    assert mgr.prepare_team_route(TEAM)["route"] == "ready"
    assert mgr.export_admission_acceptance(TEAM)["acceptance_token"] is not None


# --------------------------------------------------------------------------- #
# 12. The same row arriving later through Git is not a conflict
# --------------------------------------------------------------------------- #


def test_the_couriered_row_merges_cleanly_with_the_invitees_own_history(
    playground_dir,
):
    """Both branches insert the same primary key from a common ancestor.

    The inviter's copy came by courier; the invitee's is their own commit. The
    real splice-sqlite merge driver must treat them as redundant.
    """
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, alice_cloud = _setup(root)
    courier = _prepared_bob(
        root, alice_cloud, alice_hex, bob_hex, _invite(root, alice_hex, alice_cloud)
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, TEAM, courier)

    bob_cloud = root / "bob-git-cloud"
    bob_cloud.mkdir()
    _push(_sync_dir(root, bob_hex), bob_cloud)

    alice_sync = _sync_dir(root, alice_hex)
    fetched = CS.CodSync(
        Repo(alice_sync / ".git", alice_sync), LocalFolderStore(str(bob_cloud))
    ).fetch()
    # Run git directly so the merge driver's own stderr is observable; Repo.merge
    # captures and discards it.
    merge = subprocess.run(
        ["git", "-C", str(alice_sync), "merge", "--no-edit", fetched.observed_head],
        capture_output=True,
        text=True,
    )
    assert merge.returncode == 0, merge.stderr
    assert "insert/insert conflict" not in merge.stderr
    bob_state = provisioning.derive_team_join_state(root, bob_hex, TEAM)
    merged = _alice_route_rows(root, alice_hex, bob_state["self_in_team"].hex())
    assert merged == _stored_announcements(
        _sync_dir(root, bob_hex) / "core.db", bob_state["self_in_team"]
    )
