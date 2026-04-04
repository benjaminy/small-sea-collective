import base64
import json
import pathlib
import socket

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from click.testing import CliRunner
from fastapi.testclient import TestClient
from shared_file_vault import sync, vault
from shared_file_vault.cli import cli
from small_sea_client.client import SmallSeaClient, SmallSeaSession
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager

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


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
    team_bucket = f"ss-{team_result['station_id_hex'][:16]}"

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


def test_sync_config_roundtrip_and_remote_prefixes(tmp_path, monkeypatch):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    sync.save_config(
        {
            "vault_root": "/tmp/vault",
            "participant_hex": "aa" * 16,
            "hub_port": 12345,
            "team_sessions": {"Project X": {"session_token": "tok-123"}},
        }
    )

    loaded = sync.load_config()
    assert loaded["vault_root"] == "/tmp/vault"
    assert loaded["participant_hex"] == "aa" * 16
    assert loaded["hub_port"] == 12345
    assert loaded["team_sessions"]["Project X"]["session_token"] == "tok-123"

    session = SmallSeaSession(SmallSeaClient(port=7777), "session-token")
    registry_remote = sync.make_registry_remote("ProjectX", session)
    niche_remote = sync.make_niche_remote("ProjectX", "docs", session)

    assert registry_remote._path_prefix == "vault/ProjectX/registry/"
    assert niche_remote._path_prefix == "vault/ProjectX/niches/docs/"


def test_login_team_pin_flow_persists_token(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    config_file = root / "alice-vault.toml"
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
        result = sync.login_team(
            "ProjectX",
            alice_hex,
            _http_client=http,
            pin_reader=lambda _: captured["pin"],
        )
    finally:
        backend.request_session = original

    assert result.auto_approved is False
    assert result.session_info["team_name"] == "ProjectX"
    assert sync.load_config()["team_sessions"]["ProjectX"]["session_token"] == result.session_token


def test_cli_push_uses_config_defaults(monkeypatch, tmp_path):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))
    sync.save_config(
        {
            "vault_root": "/tmp/example-vault",
            "participant_hex": "aa" * 16,
            "hub_port": 23456,
        }
    )

    captured = {}

    def _fake_push(vault_root, participant_hex, team_name, niche_name, *, hub_port, _http_client=None):
        captured.update(
            {
                "vault_root": vault_root,
                "participant_hex": participant_hex,
                "team_name": team_name,
                "niche_name": niche_name,
                "hub_port": hub_port,
            }
        )

    monkeypatch.setattr(sync, "push_via_hub", _fake_push)
    runner = CliRunner()
    result = runner.invoke(cli, ["push", "ProjectX", "docs"])

    assert result.exit_code == 0, result.output
    assert captured == {
        "vault_root": "/tmp/example-vault",
        "participant_hex": "aa" * 16,
        "team_name": "ProjectX",
        "niche_name": "docs",
        "hub_port": 23456,
    }


def test_hub_push_pull_refreshes_checkout(playground_dir, minio_server_gen, monkeypatch):
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
    (alice_checkout / "notes.txt").write_text("v1\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="init")

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    sync.login_team("ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

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
    assert (bob_checkout / "notes.txt").read_text() == "v1\n"

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    (alice_checkout / "notes.txt").write_text("v2\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="update")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    sync.pull_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    assert (bob_checkout / "notes.txt").read_text() == "v2\n"


def test_hub_pull_conflict_reports_paths(playground_dir, minio_server_gen, monkeypatch):
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
    (alice_checkout / "shared.txt").write_text("base\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="base")

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    sync.login_team("ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

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

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    (alice_checkout / "shared.txt").write_text("alice change\n")
    vault.publish(alice_vault_root, env["alice_hex"], "ProjectX", "docs", str(alice_checkout), message="alice")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    (bob_checkout / "shared.txt").write_text("bob change\n")
    vault.publish(bob_vault_root, env["bob_hex"], "ProjectX", "docs", str(bob_checkout), message="bob")

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    with pytest.raises(sync.PullConflictError) as exc_info:
        sync.pull_via_hub(
            bob_vault_root,
            env["bob_hex"],
            "ProjectX",
            "docs",
            env["alice_member_id_hex"],
            _http_client=http,
        )

    assert exc_info.value.scope == "niche"
    assert "shared.txt" in exc_info.value.paths
