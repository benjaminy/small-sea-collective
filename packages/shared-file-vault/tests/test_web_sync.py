import base64
import json
import pathlib
import socket

import boto3
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from fastapi.testclient import TestClient

from shared_file_vault import sync, vault
from shared_file_vault.web import create_app
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _open_session(http, nickname, team):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "Smoke Tests",
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_bucket_public(endpoint, access_key, secret_key, bucket_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    s3.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
                    }
                ],
            }
        ),
    )


def _push_team_repo_via_hub(http, session_hex, repo_dir):
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text

    from cod_sync.protocol import CodSync, SmallSeaRemote

    remote = SmallSeaRemote(session_hex, base_url="http://testserver", client=http)
    cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
    cs.remote = remote
    cs.push_to_remote(["main"])


def _setup_two_member_team(playground_dir, minio_server_gen):
    alice_minio = minio_server_gen(port=_free_port())
    bob_minio = minio_server_gen(port=_free_port())
    root = pathlib.Path(playground_dir)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")

    alice_nts = _open_session(http, "Alice", "NoteToSelf")
    backend.add_cloud_location(
        alice_nts,
        "s3",
        alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf")
    backend.add_cloud_location(
        bob_nts,
        "s3",
        bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    alice_member_id_hex = team_result["member_id_hex"]
    team_bucket = f"ss-{team_result['berth_id_hex'][:16]}"

    alice_team_token = _open_session(http, "Alice", "ProjectX")
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push_team_repo_via_hub(http, alice_team_token, alice_team_sync)
    _make_bucket_public(
        alice_minio["endpoint"],
        alice_minio["access_key"],
        alice_minio["secret_key"],
        team_bucket,
    )

    token_b64 = Provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    _push_team_repo_via_hub(http, alice_team_token, alice_team_sync)

    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token_b64)
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    return {
        "root": root,
        "http": http,
        "alice_hex": alice_hex,
        "bob_hex": bob_hex,
        "alice_member_id_hex": alice_member_id_hex,
        "bob_member_id_hex": bob_member_id_hex,
    }


def test_web_push_requires_cached_session(playground_dir, monkeypatch):
    runner_root = playground_dir
    config_file = f"{runner_root}/vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", config_file)

    vault_root = f"{runner_root}/vault"
    participant_hex = "aa" * 16
    vault.init_vault(vault_root, participant_hex)
    vault.create_niche(vault_root, participant_hex, "ProjectX", "docs")

    vault_app = create_app(vault_root, participant_hex)
    client = TestClient(vault_app)

    resp = client.post("/teams/ProjectX/niches/docs/push")
    assert resp.status_code == 200
    assert "No cached Hub session" in resp.text


def test_web_session_request_auto_approve(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    config_file = root / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.create_team(root, alice_hex, "ProjectX")

    vault_root = str(root / "vault")
    vault.init_vault(vault_root, alice_hex)
    vault.create_niche(vault_root, alice_hex, "ProjectX", "docs")

    vault_app = create_app(vault_root, alice_hex, _http_client=http)
    client = TestClient(vault_app)

    resp = client.post("/teams/ProjectX/session/request")
    assert resp.status_code == 200
    assert "session active" in resp.text
    assert sync.load_config()["team_sessions"]["ProjectX"]["session_token"]


def test_web_session_request_pin_flow(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    config_file = root / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    backend = SmallSea.SmallSeaBackend(root_dir=str(root))
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.create_team(root, alice_hex, "ProjectX")

    captured = {}
    original = backend.request_session

    def _capturing(participant, app_name, team, client_name):
        pending_id, pin = original(participant, app_name, team, client_name)
        captured["pin"] = pin
        return pending_id, pin

    backend.request_session = _capturing
    try:
        vault_root = str(root / "vault")
        vault.init_vault(vault_root, alice_hex)
        vault.create_niche(vault_root, alice_hex, "ProjectX", "docs")

        vault_app = create_app(vault_root, alice_hex, _http_client=http)
        client = TestClient(vault_app)

        resp = client.post("/teams/ProjectX/session/request")
        assert resp.status_code == 200
        assert "PIN sent via notification" in resp.text

        confirm = client.post(
            "/teams/ProjectX/session/confirm",
            data={"pin": captured["pin"]},
        )
        assert confirm.status_code == 200
        assert "session active" in confirm.text
        assert sync.load_config()["team_sessions"]["ProjectX"]["session_token"]
    finally:
        backend.request_session = original


def test_web_push_and_pull_through_hub(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_member_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_vault_root = str(root / "vault-alice")
    bob_vault_root = str(root / "vault-bob")
    vault.init_vault(alice_vault_root, env["alice_hex"])
    vault.init_vault(bob_vault_root, env["bob_hex"])

    alice_checkout = root / "alice-checkout"
    bob_checkout = root / "bob-checkout"

    vault.create_niche(alice_vault_root, env["alice_hex"], "ProjectX", "docs")
    vault.add_checkout(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout))
    (alice_checkout / "notes.txt").write_text("hello from web\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="init")

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    sync.login_team("ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_app = create_app(alice_vault_root, env["alice_hex"], _http_client=http)
    alice_client = TestClient(alice_app)
    detail_resp = alice_client.get("/teams/ProjectX/niches/docs")
    assert detail_resp.status_code == 200
    assert "Check For Updates" in detail_resp.text
    assert 'placeholder="Peer member ID hex"' not in detail_resp.text

    push_resp = alice_client.post("/teams/ProjectX/niches/docs/push")
    assert push_resp.status_code == 200
    assert "Pushed niche and registry through the Hub." in push_resp.text

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    sync.login_team("ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    sync.pull_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    vault.add_checkout(bob_vault_root, env["bob_hex"], "ProjectX", "docs", str(bob_checkout))

    (alice_checkout / "notes.txt").write_text("hello again\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="update")
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    push_resp = alice_client.post("/teams/ProjectX/niches/docs/push")
    assert push_resp.status_code == 200

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    bob_app = create_app(bob_vault_root, env["bob_hex"], _http_client=http)
    bob_client = TestClient(bob_app)
    detail_resp = bob_client.get("/teams/ProjectX/niches/docs")
    assert detail_resp.status_code == 200
    assert "Check For Updates" in detail_resp.text
    assert 'placeholder="Peer member ID hex"' not in detail_resp.text
    assert "Merge Changes" not in detail_resp.text

    fetch_resp = bob_client.post(
        "/teams/ProjectX/niches/docs/fetch",
        data={"from_member_id": env["alice_member_id_hex"]},
    )
    assert fetch_resp.status_code == 200
    assert f"Fetched changes from {env['alice_member_id_hex']}. They are ready to merge." in fetch_resp.text
    assert "Merge Changes" in fetch_resp.text
    assert (bob_checkout / "notes.txt").read_text() == "hello from web\n"

    merge_resp = bob_client.post(
        "/teams/ProjectX/niches/docs/merge",
        data={"from_member_id": env["alice_member_id_hex"]},
    )
    assert merge_resp.status_code == 200
    assert f"Merged parked changes from {env['alice_member_id_hex']}." in merge_resp.text
    assert (bob_checkout / "notes.txt").read_text() == "hello again\n"
