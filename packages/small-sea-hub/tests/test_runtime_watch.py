import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
from small_sea_hub.server import app, _register_session_peers, _run_runtime_reconciliation_for_session
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
