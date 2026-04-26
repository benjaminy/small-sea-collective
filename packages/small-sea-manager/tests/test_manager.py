import pathlib
import sqlite3
from types import SimpleNamespace

import cod_sync.protocol as CS
from fastapi.testclient import TestClient
from wrasse_trust.keys import ProtectionLevel, generate_key_pair

import small_sea_manager.provisioning as Provisioning
from small_sea_manager.admission_events import AdmissionEventType
from small_sea_manager import admission_events
from small_sea_manager.manager import TeamManager, _CORE_APP
from small_sea_manager.web import create_app


def test_team_manager_session_cache_is_keyed_by_team_and_mode(playground_dir):
    root = pathlib.Path(playground_dir)
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(root, participant_hex)

    manager.set_pending("ProjectX", "pending-encrypted")
    manager.set_pending("ProjectX", "pending-passthrough", mode="passthrough")
    manager.set_pending("NoteToSelf", "pending-nts", mode="passthrough")

    assert manager.session_state("ProjectX") == "pending"
    assert manager.session_state("ProjectX", mode="passthrough") == "pending"
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"

    manager.set_session("ProjectX", "token-encrypted")

    assert manager.session_state("ProjectX") == "active"
    assert manager.get_pending_id("ProjectX") is None
    assert manager.get_pending_id("ProjectX", mode="passthrough") == "pending-passthrough"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") == "pending-nts"
    assert manager.session_state("ProjectX", mode="passthrough") == "pending"
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"


def test_manager_web_pin_flow_updates_cached_session_state(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    app = create_app(playground_dir, participant_hex)
    client = TestClient(app)
    manager = app.state.manager

    captured = {}

    def fake_start_session(participant, app_name, team_name, client_name, mode=None):
        captured["start"] = (participant, app_name, team_name, client_name, mode)
        return None, "pending-123"

    def fake_confirm_session(pending_id, pin):
        captured["confirm"] = (pending_id, pin)
        return SimpleNamespace(token="session-abc")

    monkeypatch.setattr(manager.client, "start_session", fake_start_session)
    monkeypatch.setattr(manager.client, "confirm_session", fake_confirm_session)

    response = client.post("/session/request")
    assert response.status_code == 200
    assert manager.session_state("NoteToSelf", mode="passthrough") == "pending"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") == "pending-123"
    assert captured["start"] == (
        participant_hex,
        _CORE_APP,
        "NoteToSelf",
        "ManagerUI",
        "passthrough",
    )

    response = client.post("/session/confirm", data={"pin": "321"})
    assert response.status_code == 200
    assert manager.session_state("NoteToSelf", mode="passthrough") == "active"
    assert manager.get_pending_id("NoteToSelf", mode="passthrough") is None
    assert captured["confirm"] == ("pending-123", "321")


def _push_to_localfolder(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    cod = CS.CodSync("origin", repo_dir=repo_dir)
    cod.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod.push_to_remote(["main"])


def _create_shared_team_with_bob(root: pathlib.Path):
    alice_cloud = root / "alice-cloud"
    bob_cloud = root / "bob-cloud"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    Provisioning.add_cloud_storage(root, bob_hex, protocol="localfolder", url=str(bob_cloud))

    Provisioning.create_team(root, alice_hex, "ProjectX")
    alice_repo = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_repo, alice_cloud)

    token_b64 = Provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _push_to_localfolder(alice_repo, alice_cloud)

    bob_acceptance = Provisioning.accept_invitation(
        root,
        bob_hex,
        token_b64,
        inviter_remote=CS.LocalFolderRemote(str(alice_cloud)),
    )
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", bob_acceptance)
    return alice_hex, bob_hex


def _insert_teammate_linked_device_event(root: pathlib.Path, observer_hex: str, teammate_hex: str):
    team_id, teammate_member_id = Provisioning._team_row(root, teammate_hex, "ProjectX")
    teammate_private_key, teammate_public_key = Provisioning.get_current_team_device_key(
        root,
        teammate_hex,
        "ProjectX",
    )
    linked_public_key = generate_key_pair(ProtectionLevel.DAILY)[0].public_key
    cert = Provisioning.issue_device_link_cert(
        subject_key=Provisioning._participant_key_from_public(linked_public_key),
        issuer_key=Provisioning._participant_key_from_public(teammate_public_key),
        issuer_private_key=teammate_private_key,
        team_id=team_id,
        member_id=teammate_member_id,
    )
    assert Provisioning.verify_device_link_cert(
        cert,
        issuer_public_key=teammate_public_key,
        team_id=team_id,
        member_id=teammate_member_id,
        subject_public_key=linked_public_key,
    )

    observer_db_path = root / "Participants" / observer_hex / "ProjectX" / "Sync" / "core.db"
    engine = Provisioning._sqlite_engine(observer_db_path)
    try:
        with engine.begin() as conn:
            Provisioning._store_team_certificate(conn, cert, issuer_member_id=teammate_member_id)
            Provisioning._upsert_team_device_row(conn, teammate_member_id, linked_public_key)
    finally:
        engine.dispose()
    return cert


def test_admission_event_dismissals_persist_across_manager_instances(playground_dir):
    root = pathlib.Path(playground_dir)
    cloud_dir = root / "cloud"
    cloud_dir.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud_dir))
    Provisioning.create_team(root, alice_hex, "ProjectX")

    linked_public_key = generate_key_pair(ProtectionLevel.DAILY)[0].public_key
    linked_cert = Provisioning.issue_device_link_for_member(root, alice_hex, "ProjectX", linked_public_key)
    invitation_token = Provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(cloud_dir)},
        invitee_label="Bob",
    )
    assert invitation_token

    manager = TeamManager(root, alice_hex)
    team = manager.get_team("ProjectX")
    event_types = {(event.event_type.value, event.artifact_id_hex) for event in team["admission_events"]}
    assert (AdmissionEventType.LINKED_DEVICE.value, linked_cert.cert_id.hex()) in event_types
    pending_invitation = next(
        event for event in team["admission_events"] if event.event_type is AdmissionEventType.PROPOSAL_SHELL
    )

    manager.dismiss_admission_event(
        "ProjectX",
        pending_invitation.event_type,
        pending_invitation.artifact_id_hex,
    )

    manager_reloaded = TeamManager(root, alice_hex)
    team_reloaded = manager_reloaded.get_team("ProjectX")
    reloaded_types = {(event.event_type.value, event.artifact_id_hex) for event in team_reloaded["admission_events"]}
    assert (AdmissionEventType.LINKED_DEVICE.value, linked_cert.cert_id.hex()) in reloaded_types
    assert (pending_invitation.event_type.value, pending_invitation.artifact_id_hex) not in reloaded_types


def test_linked_device_notification_candidates_seed_backlog_and_keep_notified_cards_visible(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex = _create_shared_team_with_bob(root)
    alice_self_member_id = Provisioning.get_self_in_team(root, alice_hex, "ProjectX")

    historical_cert = _insert_teammate_linked_device_event(root, alice_hex, bob_hex)

    first_candidates = admission_events.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_member_id_hex=alice_self_member_id,
    )
    assert first_candidates == []

    fresh_cert = _insert_teammate_linked_device_event(root, alice_hex, bob_hex)
    second_candidates = admission_events.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_member_id_hex=alice_self_member_id,
    )
    assert [candidate.artifact_id_hex for candidate in second_candidates] == [fresh_cert.cert_id.hex()]

    Provisioning.mark_admission_event_notified(
        root,
        alice_hex,
        "ProjectX",
        AdmissionEventType.LINKED_DEVICE.value,
        fresh_cert.cert_id.hex(),
    )
    assert admission_events.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_member_id_hex=alice_self_member_id,
    ) == []

    team = TeamManager(root, alice_hex).get_team("ProjectX")
    linked_ids = {
        event.artifact_id_hex
        for event in team["admission_events"]
        if event.event_type is AdmissionEventType.LINKED_DEVICE
    }
    assert historical_cert.cert_id.hex() in linked_ids
    assert fresh_cert.cert_id.hex() in linked_ids


def test_manager_web_renders_admin_and_non_admin_admission_controls(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_cloud = root / "alice-cloud"
    bob_cloud = root / "bob-cloud"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")
    Provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    Provisioning.add_cloud_storage(root, bob_hex, protocol="localfolder", url=str(bob_cloud))

    Provisioning.create_team(root, alice_hex, "ProjectX")
    alice_repo = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_repo, alice_cloud)

    token_b64 = Provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _push_to_localfolder(alice_repo, alice_cloud)

    bob_acceptance = Provisioning.accept_invitation(
        root,
        bob_hex,
        token_b64,
        inviter_remote=CS.LocalFolderRemote(str(alice_cloud)),
    )

    bob_self_id_hex = next(
        team["self_in_team"] for team in Provisioning.list_teams(root, bob_hex) if team["name"] == "ProjectX"
    )
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(bob_team_db)) as conn:
        # Test-only fixture shortcut: force Bob into a non-admin local view so
        # we can assert the UI hides admin-only controls for that viewer.
        conn.execute(
            "UPDATE berth_role SET role = 'read-only' WHERE member_id = ?",
            (bytes.fromhex(bob_self_id_hex),),
        )
        conn.commit()

    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", bob_acceptance)

    alice_app = create_app(root, alice_hex)
    alice_client = TestClient(alice_app)
    alice_html = alice_client.get("/teams/ProjectX").text
    assert "Admission finalized for Bob" in alice_html
    assert "Exclude" in alice_html

    bob_app = create_app(root, bob_hex)
    bob_client = TestClient(bob_app)
    bob_html = bob_client.get("/teams/ProjectX").text
    assert "Proposal shell open for Bob" in bob_html
    assert "Revoke" not in bob_html
    assert "Exclude" not in bob_html


def test_admission_watch_endpoint_backs_off_when_hub_wait_unavailable(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, "ProjectX")
    app = create_app(playground_dir, participant_hex)
    client = TestClient(app)
    manager = app.state.manager

    manager.set_session("ProjectX", "token-encrypted")
    monkeypatch.setattr(
        manager,
        "wait_for_team_admission_signal",
        lambda team_name, timeout=15: False,
    )

    response = client.get("/teams/ProjectX/admission-events/watch")
    assert response.status_code == 200
    assert 'load delay:5s' in response.text


def test_admission_watch_endpoint_stays_fast_when_hub_wait_succeeds(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, "ProjectX")
    app = create_app(playground_dir, participant_hex)
    client = TestClient(app)
    manager = app.state.manager

    manager.set_session("ProjectX", "token-encrypted")
    monkeypatch.setattr(
        manager,
        "wait_for_team_admission_signal",
        lambda team_name, timeout=15: True,
    )

    response = client.get("/teams/ProjectX/admission-events/watch")
    assert response.status_code == 200
    assert 'load delay:0.2s' in response.text
