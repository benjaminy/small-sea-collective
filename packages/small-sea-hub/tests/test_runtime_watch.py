import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
from cod_sync.protocol import LocalFolderRemote
from small_sea_hub.server import app, _register_session_peers, _run_runtime_reconciliation_for_session
from small_sea_manager import admission_events as AdmissionEvents
from small_sea_manager import provisioning
from small_sea_manager.provisioning import _serialize_prekey_bundle
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public


def _request_and_confirm(client, team="ProjectX", mode="encrypted"):
    resp = client.post(
        "/sessions/request",
        json={
            "participant": "alice",
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    result = resp.json()
    resp = client.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    return resp.json()


def _add_same_member_linked_device_bundle(root: Path, participant_hex: str, team_name: str):
    linked_device_key, _linked_device_private_key = generate_key_pair(ProtectionLevel.DAILY)
    provisioning.issue_device_link_for_member(
        root,
        participant_hex,
        team_name,
        linked_device_key.public_key,
    )
    identity = provisioning.generate_identity_key_pair()
    signed_prekey, _signed_prekey_private_key = provisioning.generate_signed_prekey(
        identity.signing_private_key
    )
    one_time_prekeys = provisioning.generate_one_time_prekeys(2)
    bundle = provisioning.build_prekey_bundle(
        participant_id=key_id_from_public(linked_device_key.public_key),
        identity=identity,
        signed_prekey=signed_prekey,
        one_time_prekeys=[prekey for prekey, _private in one_time_prekeys],
    )
    team_db = (
        root / "Participants" / participant_hex / team_name / "Sync" / "core.db"
    )
    with sqlite3.connect(team_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO device_prekey_bundle
            (device_key_id, prekey_bundle_json, published_at)
            VALUES (?, ?, ?)
            """,
            (
                key_id_from_public(linked_device_key.public_key),
                json.dumps(_serialize_prekey_bundle(bundle), sort_keys=True),
                "2026-04-13T00:00:00+00:00",
            ),
        )
        conn.commit()
    return key_id_from_public(linked_device_key.public_key)


def _push_to_localfolder(repo_dir: Path, cloud_dir: Path):
    import cod_sync.protocol as CS

    cod = CS.CodSync("origin", repo_dir=repo_dir)
    cod.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod.push_to_remote(["main"])


def _create_shared_team_with_bob(root: Path):
    alice_cloud = root / "alice-cloud"
    bob_cloud = root / "bob-cloud"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    alice_hex = provisioning.create_new_participant(root, "alice")
    bob_hex = provisioning.create_new_participant(root, "bob")
    provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(alice_cloud))
    provisioning.add_cloud_storage(root, bob_hex, protocol="localfolder", url=str(bob_cloud))

    provisioning.create_team(root, alice_hex, "ProjectX")
    alice_repo = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_to_localfolder(alice_repo, alice_cloud)

    token_b64 = provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "localfolder", "url": str(alice_cloud)},
        invitee_label="Bob",
    )
    _push_to_localfolder(alice_repo, alice_cloud)

    bob_acceptance = provisioning.accept_invitation(
        root,
        bob_hex,
        token_b64,
        inviter_remote=LocalFolderRemote(str(alice_cloud)),
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", bob_acceptance)
    return alice_hex, bob_hex


def _insert_teammate_linked_device_event(root: Path, observer_hex: str, teammate_hex: str):
    team_id, teammate_member_id = provisioning._team_row(root, teammate_hex, "ProjectX")
    teammate_private_key, teammate_public_key = provisioning.get_current_team_device_key(
        root,
        teammate_hex,
        "ProjectX",
    )
    linked_public_key = generate_key_pair(ProtectionLevel.DAILY)[0].public_key
    cert = provisioning.issue_device_link_cert(
        subject_key=provisioning._participant_key_from_public(linked_public_key),
        issuer_key=provisioning._participant_key_from_public(teammate_public_key),
        issuer_private_key=teammate_private_key,
        team_id=team_id,
        member_id=teammate_member_id,
    )
    observer_db_path = root / "Participants" / observer_hex / "ProjectX" / "Sync" / "core.db"
    engine = provisioning._sqlite_engine(observer_db_path)
    try:
        with engine.begin() as conn:
            provisioning._store_team_certificate(conn, cert, issuer_member_id=teammate_member_id)
            provisioning._upsert_team_device_row(conn, teammate_member_id, linked_public_key)
    finally:
        engine.dispose()
    return cert


def test_watcher_reconciliation_uploads_runtime_artifact_once(playground_dir, monkeypatch):
    root = Path(playground_dir)
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = provisioning.create_new_participant(playground_dir, "alice")
    provisioning.create_team(playground_dir, alice_hex, "ProjectX")
    linked_device_key_id = _add_same_member_linked_device_bundle(root, alice_hex, "ProjectX")
    app.state.backend = backend
    app.state.watched_sessions = {}
    app.state.watched_peers = {}
    app.state.peer_counts = {}
    app.state.peer_signal_events = {}
    app.state.ntfy_listener_tasks = {}
    app.state.logger = backend.logger
    with TestClient(app) as client:
        session_hex = _request_and_confirm(client)
    _register_session_peers(session_hex)

    uploads = []

    def _fake_upload_runtime_artifact(session_hex_arg, path, data, expected_etag=None):
        uploads.append((session_hex_arg, path, data.decode("utf-8")))
        return True, None, ""

    monkeypatch.setattr(backend, "upload_runtime_artifact", _fake_upload_runtime_artifact)

    _run_runtime_reconciliation_for_session(app, session_hex)
    assert len(uploads) == 1
    assert linked_device_key_id.hex() in uploads[0][1]
    _run_runtime_reconciliation_for_session(app, session_hex)
    assert len(uploads) == 1


def test_watcher_retries_linked_device_notification_after_missing_adapter(playground_dir, monkeypatch):
    root = Path(playground_dir)
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex, bob_hex = _create_shared_team_with_bob(root)
    alice_self_member_id = provisioning.get_self_in_team(root, alice_hex, "ProjectX")

    app.state.backend = backend
    app.state.watched_sessions = {}
    app.state.watched_peers = {}
    app.state.peer_counts = {}
    app.state.peer_signal_events = {}
    app.state.ntfy_listener_tasks = {}
    app.state.logger = backend.logger
    with TestClient(app) as client:
        session_hex = _request_and_confirm(client)
    _register_session_peers(session_hex)

    AdmissionEvents.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_member_id_hex=alice_self_member_id,
    )
    linked_cert = _insert_teammate_linked_device_event(root, alice_hex, bob_hex)

    attempts = []

    def _missing_adapter(*_args, **_kwargs):
        attempts.append("missing")
        raise SmallSea.SmallSeaNotFoundExn("No notification service configured")

    def _success(*_args, **_kwargs):
        attempts.append("sent")
        return True, "msg-1", ""

    monkeypatch.setattr(backend, "send_notification", _missing_adapter)
    assert _run_runtime_reconciliation_for_session(app, session_hex) is True
    assert attempts == ["missing"]
    assert (
        AdmissionEvents.AdmissionEventType.LINKED_DEVICE.value,
        linked_cert.cert_id.hex(),
    ) not in provisioning.list_notified_admission_events(root, alice_hex, "ProjectX")

    monkeypatch.setattr(backend, "send_notification", _success)
    assert _run_runtime_reconciliation_for_session(app, session_hex) is False
    assert attempts == ["missing", "sent"]
    assert (
        AdmissionEvents.AdmissionEventType.LINKED_DEVICE.value,
        linked_cert.cert_id.hex(),
    ) in provisioning.list_notified_admission_events(root, alice_hex, "ProjectX")

    assert _run_runtime_reconciliation_for_session(app, session_hex) is False
    assert attempts == ["missing", "sent"]


def test_watcher_does_not_notify_for_self_linked_device_events(playground_dir, monkeypatch):
    root = Path(playground_dir)
    backend = SmallSea.SmallSeaBackend(root_dir=playground_dir)
    alice_hex = provisioning.create_new_participant(playground_dir, "alice")
    provisioning.create_team(playground_dir, alice_hex, "ProjectX")

    app.state.backend = backend
    app.state.watched_sessions = {}
    app.state.watched_peers = {}
    app.state.peer_counts = {}
    app.state.peer_signal_events = {}
    app.state.ntfy_listener_tasks = {}
    app.state.logger = backend.logger
    with TestClient(app) as client:
        session_hex = _request_and_confirm(client)
    _register_session_peers(session_hex)

    AdmissionEvents.list_linked_device_notification_candidates(
        root,
        alice_hex,
        "ProjectX",
        self_member_id_hex=provisioning.get_self_in_team(root, alice_hex, "ProjectX"),
    )
    provisioning.issue_device_link_for_member(
        root,
        alice_hex,
        "ProjectX",
        generate_key_pair(ProtectionLevel.DAILY)[0].public_key,
    )

    sends = []

    def _capture_send(*_args, **_kwargs):
        sends.append("sent")
        return True, "msg-1", ""

    monkeypatch.setattr(backend, "send_notification", _capture_send)
    assert _run_runtime_reconciliation_for_session(app, session_hex) is True
    assert sends == []
