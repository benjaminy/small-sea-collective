import anyio
import functools
import pathlib
import sqlite3
from types import SimpleNamespace

import pytest
from cod_sync.store import LocalFolderStore as AsyncLocalFolderStore
from cod_sync.repo import Repo
from fastapi.testclient import TestClient
from wrasse_trust.keys import ProtectionLevel, generate_key_pair

import small_sea_manager.provisioning as Provisioning
from small_sea_manager.admission_events import AdmissionEventType
from small_sea_manager import admission_events
from small_sea_client.client import SmallSeaHubUnavailable
import small_sea_manager.manager as manager_module
from small_sea_manager.manager import TeamManager, _CORE_APP
from small_sea_manager.web import create_app
from test_support import publish_main


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

    async def fake_start_session(_http, participant, app_name, team_name, client_name, mode=None):
        captured["start"] = (participant, app_name, team_name, client_name, mode)
        return None, "pending-123"

    async def fake_confirm_session(_http, pending_id, pin):
        captured["confirm"] = (pending_id, pin)
        return SimpleNamespace(token="session-abc")

    monkeypatch.setattr(manager_module._hub_client, "start_session", fake_start_session)
    monkeypatch.setattr(manager_module._hub_client, "confirm_session", fake_confirm_session)

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


def test_a_late_confirmation_keeps_a_newer_request_pending(playground_dir, monkeypatch):
    """Another tab may start a new request while the Hub confirms an older one."""
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(pathlib.Path(playground_dir), participant_hex)
    manager.set_pending("ProjectX", "pending-a")

    async def fake_confirm_session(_http, pending_id, pin):
        manager.set_pending("ProjectX", "pending-b")
        return SimpleNamespace(token="session-a")

    monkeypatch.setattr(manager_module._hub_client, "confirm_session", fake_confirm_session)

    anyio.run(manager.confirm_session, "ProjectX", "321")
    assert manager.session_state("ProjectX") == "pending"
    assert manager.get_pending_id("ProjectX") == "pending-b"


def test_a_confirmation_finishing_before_a_newer_request_is_answered_is_ignored(
    playground_dir, monkeypatch
):
    """Tab B's request may still be waiting on the Hub when tab A's confirmation returns."""
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(pathlib.Path(playground_dir), participant_hex)
    manager.set_pending("ProjectX", "pending-a")

    async def scenario():
        b_started = anyio.Event()
        a_done = anyio.Event()

        async def fake_confirm_session(_http, pending_id, pin):
            await b_started.wait()
            return SimpleNamespace(token="session-a")

        async def fake_start_session(_http, *args, mode=None):
            b_started.set()
            await a_done.wait()
            return None, "pending-b"

        monkeypatch.setattr(manager_module._hub_client, "confirm_session", fake_confirm_session)
        monkeypatch.setattr(manager_module._hub_client, "start_session", fake_start_session)

        async def confirm_a():
            await manager.confirm_session("ProjectX", "321")
            a_done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(confirm_a)
            tg.start_soon(manager.request_session, "ProjectX", "encrypted", "ManagerUI")

    anyio.run(scenario)
    assert manager.session_state("ProjectX") == "pending"
    assert manager.get_pending_id("ProjectX") == "pending-b"


def test_an_older_pin_submitted_after_a_newer_request_started_is_not_confirmed(
    playground_dir, monkeypatch
):
    """A's PIN, submitted while B waits on the Hub, is not sent to the Hub."""
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(pathlib.Path(playground_dir), participant_hex)
    confirmed = []

    async def scenario():
        b_started = anyio.Event()
        release_b = anyio.Event()
        answers = iter(["pending-a", "pending-b"])

        async def fake_start_session(_http, *args, mode=None):
            pending_id = next(answers)
            if pending_id == "pending-b":
                b_started.set()
                await release_b.wait()
            return None, pending_id

        async def fake_confirm_session(_http, pending_id, pin):
            confirmed.append(pending_id)
            return SimpleNamespace(token="session-a")

        monkeypatch.setattr(manager_module._hub_client, "start_session", fake_start_session)
        monkeypatch.setattr(manager_module._hub_client, "confirm_session", fake_confirm_session)

        await manager.request_session("ProjectX", "encrypted", "ManagerUI")
        async with anyio.create_task_group() as tg:
            tg.start_soon(manager.request_session, "ProjectX", "encrypted", "ManagerUI")
            await b_started.wait()
            await manager.confirm_session("ProjectX", "321")
            release_b.set()

    anyio.run(scenario)
    assert confirmed == []
    assert manager.session_state("ProjectX") == "pending"
    assert manager.get_pending_id("ProjectX") == "pending-b"


def test_a_failed_replacement_request_leaves_nothing_pending(playground_dir, monkeypatch):
    """The person can request again instead of facing A's unconfirmable PIN."""
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(pathlib.Path(playground_dir), participant_hex)
    answers = iter([(None, "pending-a"), SmallSeaHubUnavailable()])

    async def fake_start_session(_http, *args, mode=None):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(manager_module._hub_client, "start_session", fake_start_session)

    async def scenario():
        await manager.request_session("ProjectX", "encrypted", "ManagerUI")
        with pytest.raises(SmallSeaHubUnavailable):
            await manager.request_session("ProjectX", "encrypted", "ManagerUI")

    anyio.run(scenario)
    assert manager.session_state("ProjectX") == "none"
    assert manager.get_pending_id("ProjectX") is None


def test_a_late_answer_to_an_older_request_is_ignored(playground_dir, monkeypatch):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    manager = TeamManager(pathlib.Path(playground_dir), participant_hex)

    async def scenario():
        b_done = anyio.Event()
        answers = iter(["pending-a", "pending-b"])

        async def fake_start_session(_http, *args, mode=None):
            pending_id = next(answers)
            if pending_id == "pending-a":
                await b_done.wait()
            return None, pending_id

        monkeypatch.setattr(manager_module._hub_client, "start_session", fake_start_session)

        async def request_b():
            await manager.request_session("ProjectX", "encrypted", "ManagerUI")
            b_done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(manager.request_session, "ProjectX", "encrypted", "ManagerUI")
            await anyio.sleep(0)
            tg.start_soon(request_b)

    anyio.run(scenario)
    assert manager.get_pending_id("ProjectX") == "pending-b"


def _push_to_localfolder(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    publish_main(Repo(repo_dir / ".git", repo_dir), AsyncLocalFolderStore(str(cloud_dir)))


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

    bob_acceptance = anyio.run(
        functools.partial(
            Provisioning.accept_invitation,
            root,
            bob_hex,
            token_b64,
            inviter_store=AsyncLocalFolderStore(str(alice_cloud)),
        )
    )
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", bob_acceptance)
    return alice_hex, bob_hex


def test_acceptance_records_membership_certificate_event(playground_dir):
    from small_sea_manager.constitution_store import list_events
    from sqlalchemy import text

    root = pathlib.Path(playground_dir)
    alice_hex, _bob_hex = _create_shared_team_with_bob(root)
    db_path = root / "Participants" / alice_hex / "ProjectX" / "Sync" / "core.db"
    engine = Provisioning._sqlite_engine(db_path)
    try:
        with engine.connect() as conn:
            certificates = {
                row[0].hex()
                for row in conn.execute(text(
                    "SELECT cert_id FROM key_certificate WHERE cert_type = 'membership'"
                ))
            }
            recorded = {
                event.payload["cert_id"]
                for event in list_events(conn)
                if event.event_type == "key_certificate"
            }
    finally:
        engine.dispose()
    assert len(certificates) >= 2
    assert certificates <= recorded


def _insert_teammate_linked_device_event(root: pathlib.Path, observer_hex: str, teammate_hex: str):
    team_id, teammate_teammate_id = Provisioning._team_row(root, teammate_hex, "ProjectX")
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
        teammate_id=teammate_teammate_id,
    )
    assert Provisioning.verify_device_link_cert(
        cert,
        issuer_public_key=teammate_public_key,
        team_id=team_id,
        teammate_id=teammate_teammate_id,
        subject_public_key=linked_public_key,
    )

    observer_db_path = root / "Participants" / observer_hex / "ProjectX" / "Sync" / "core.db"
    engine = Provisioning._sqlite_engine(observer_db_path)
    try:
        with engine.begin() as conn:
            Provisioning._store_team_certificate(conn, cert, issuer_teammate_id=teammate_teammate_id)
            Provisioning._upsert_team_device_row(conn, teammate_teammate_id, linked_public_key)
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
    linked_cert = Provisioning.issue_device_link_for_teammate(root, alice_hex, "ProjectX", linked_public_key)
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
    alice_self_teammate_id = Provisioning.get_self_in_team(root, alice_hex, "ProjectX")

    historical_cert = _insert_teammate_linked_device_event(root, alice_hex, bob_hex)

    first_candidates = admission_events.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_teammate_id_hex=alice_self_teammate_id,
    )
    assert first_candidates == []

    fresh_cert = _insert_teammate_linked_device_event(root, alice_hex, bob_hex)
    second_candidates = admission_events.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_teammate_id_hex=alice_self_teammate_id,
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
        self_teammate_id_hex=alice_self_teammate_id,
    ) == []

    team = TeamManager(root, alice_hex).get_team("ProjectX")
    linked_ids = {
        event.artifact_id_hex
        for event in team["admission_events"]
        if event.event_type is AdmissionEventType.LINKED_DEVICE
    }
    assert historical_cert.cert_id.hex() in linked_ids
    assert fresh_cert.cert_id.hex() in linked_ids


def test_manager_web_renders_steward_and_non_steward_admission_controls(playground_dir):
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

    bob_acceptance = anyio.run(
        functools.partial(
            Provisioning.accept_invitation,
            root,
            bob_hex,
            token_b64,
            inviter_store=AsyncLocalFolderStore(str(alice_cloud)),
        )
    )

    bob_self_id_hex = next(
        team["self_in_team"] for team in Provisioning.list_teams(root, bob_hex) if team["name"] == "ProjectX"
    )
    bob_team_db = root / "Participants" / bob_hex / "ProjectX" / "Sync" / "core.db"
    with sqlite3.connect(str(bob_team_db)) as conn:
        # Test-only fixture shortcut: force Bob into a non-steward local view so
        # we can assert the UI hides steward-only controls for that viewer.
        conn.execute(
            "UPDATE berth_role SET role = 'read-only' WHERE teammate_id = ?",
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
    async def _wait(team_name, timeout=15):
        return False

    monkeypatch.setattr(manager, "wait_for_team_admission_signal", _wait)

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
    async def _wait(team_name, timeout=15):
        return True

    monkeypatch.setattr(manager, "wait_for_team_admission_signal", _wait)

    response = client.get("/teams/ProjectX/admission-events/watch")
    assert response.status_code == 200
    assert 'load delay:0.2s' in response.text


class _WatchSession:
    """A fake async team session: one peer, and a watch that reports an update."""

    def __init__(self, *, hub_down=False):
        self.hub_down = hub_down
        self.watched = None

    async def session_peers(self):
        if self.hub_down:
            raise SmallSeaHubUnavailable()
        return [{"teammate_id": "aa", "signal_count": 2}]

    async def watch_notifications(self, known, timeout=30):
        self.watched = (known, timeout)
        return {"updated": {"aa": 3}}


def _watching_manager(playground_dir, monkeypatch, session):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, "ProjectX")
    manager = TeamManager(playground_dir, participant_hex)
    manager.set_session("ProjectX", "token-encrypted")

    async def _open(http, team, mode="encrypted"):
        assert (team, mode) == ("ProjectX", "encrypted")
        manager.watch_http_timeout = http.timeout
        return session

    monkeypatch.setattr(manager, "_hub_session", _open)
    return manager


def test_admission_wait_watches_from_the_peers_known_counts(playground_dir, monkeypatch):
    session = _WatchSession()
    manager = _watching_manager(playground_dir, monkeypatch, session)

    assert anyio.run(manager.wait_for_team_admission_signal, "ProjectX", 7) is True
    assert session.watched == ({"aa": 2}, 7)


def test_admission_wait_gives_up_soon_after_its_own_timeout(playground_dir, monkeypatch):
    """A stalled Hub must not hold the panel for the cloud-transfer timeout."""
    manager = _watching_manager(playground_dir, monkeypatch, _WatchSession())

    anyio.run(manager.wait_for_team_admission_signal, "ProjectX", 7)
    assert 7 < manager.watch_http_timeout.read <= 20


def test_admission_wait_reports_false_when_the_hub_is_down(playground_dir, monkeypatch):
    manager = _watching_manager(playground_dir, monkeypatch, _WatchSession(hub_down=True))

    assert anyio.run(manager.wait_for_team_admission_signal, "ProjectX") is False


def test_admission_wait_without_a_team_session_asks_no_hub(playground_dir, monkeypatch):
    manager = _watching_manager(playground_dir, monkeypatch, None)
    manager.clear_session("ProjectX")

    assert anyio.run(manager.wait_for_team_admission_signal, "ProjectX") is False


def test_admission_watch_response_has_one_oob_events_root(playground_dir):
    participant_hex = Provisioning.create_new_participant(playground_dir, "alice")
    Provisioning.create_team(playground_dir, participant_hex, "ProjectX")
    client = TestClient(create_app(playground_dir, participant_hex))

    response = client.get("/teams/ProjectX/admission-events/watch")

    assert response.status_code == 200
    assert response.text.count('id="admission-events-ProjectX"') == 1
    assert '<div id="admission-events-ProjectX" hx-swap-oob="outerHTML">' in response.text


def test_cloud_storage_listing_survives_the_device_local_credential_join(playground_dir):
    """Regression: `ORDER BY rowid` was ambiguous once the query joined
    `local.cloud_storage_credential`, so the whole panel — provider list and
    add-provider form alike — failed with OperationalError."""
    root = pathlib.Path(playground_dir)
    participant_hex = Provisioning.create_new_participant(root, "alice")
    Provisioning.add_cloud_storage(
        root,
        participant_hex,
        protocol="s3",
        url="http://localhost:9000",
        access_key="alice-key",
        secret_key="alice-secret",
    )

    providers = Provisioning.list_cloud_storage(root, participant_hex)
    assert [
        (p["protocol"], p["url"], p["access_key"], p["credentials_on_this_device"])
        for p in providers
    ] == [("s3", "http://localhost:9000", "alice-key", True)]

    client = TestClient(create_app(root, participant_hex))
    response = client.get("/cloud-storage")
    assert response.status_code == 200
    assert "http://localhost:9000" in response.text
    assert "Register S3 / MinIO account" in response.text
    assert "alice-secret" not in response.text
